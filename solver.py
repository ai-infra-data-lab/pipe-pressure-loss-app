"""Steady energy-network solver with explicit three-port tee paths.

Unknown node potentials are p_gauge + alpha*rho*v**2/2 + rho*g*z.
Static pressures are derived at individual ports, never guessed at mixed ports.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import math
import threading

import numpy as np
from scipy.optimize import least_squares

from hydraulics import G, Loss, area, check_k_applicability, element_loss, flow_state, kinetic
from model import ModelError, Project


VERSION = "0.2.0"


class Cancelled(Exception):
    pass


@dataclass
class Link:
    id: str
    start: str
    end: str
    owner: object
    leg: str = ""


def compile_links(project):
    links = [Link(e.id, e.start, e.end, e) for e in project.elements]
    for t in project.tees:
        for leg, port in (("run", t.run), ("branch", t.branch)):
            a, b = (t.common, port) if t.kind == "split" else (port, t.common)
            links.append(Link(t.id + ":" + leg, a, b, t, leg))
    return links


def validate(project: Project):
    messages = []
    if project.mode not in ("auto", "specified"):
        raise ModelError("計算モードが不正です")
    if not 2 <= len(project.nodes) <= 200 or len(project.elements)+2*len(project.tees) > 500:
        raise ModelError("初版の対応規模は節点2–200個、計算枝500本以内です")
    for obj in [project.fluid, *project.nodes, *project.elements, *project.tees]:
        for f in fields(obj):
            value = getattr(obj, f.name)
            if str(f.type) in ("float", "float | None"):
                if value is None and "None" in str(f.type):
                    continue
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ModelError(f"{getattr(obj, 'id', '油')}.{f.name}: 有限の数値が必要です")
            elif str(f.type) == "str" and not isinstance(value, str):
                raise ModelError(f"{f.name}: 文字列が必要です")
            elif str(f.type) == "bool" and not isinstance(value, bool):
                raise ModelError(f"{f.name}: true/falseが必要です")
    fluid = project.fluid
    if fluid.density_kg_m3 <= 0 or fluid.viscosity_cst <= 0:
        raise ModelError("密度・動粘度は正の値にしてください")
    identifiers = [obj.id for obj in [*project.nodes, *project.elements, *project.tees]]
    if len(set(identifiers)) != len(identifiers) or any(not x.strip() or ":" in x for x in identifiers):
        raise ModelError("IDは空欄・重複・コロンを使用できません")
    nodes = {n.id: n for n in project.nodes}
    for n in project.nodes:
        if n.kind not in ("junction", "flow", "pressure") or n.boundary_diameter_mm < 0:
            raise ModelError(f"{n.id}: 節点種類または境界内径が不正です")
    if not any(n.kind == "pressure" for n in project.nodes):
        raise ModelError("静圧基準となる圧力境界を1個以上指定してください")
    physical_ports = {n.id: [] for n in project.nodes}
    for e in project.elements:
        if e.kind not in ("pipe", "local", "expansion", "contraction", "filter", "elbow"):
            raise ModelError(f"{e.id}: 未対応の部品種類")
        if e.start not in nodes or e.end not in nodes or e.start == e.end:
            raise ModelError(f"{e.id}: 接続先が不正です")
        if min(e.diameter_mm, e.outlet_diameter_mm, e.reference_diameter_mm) <= 0:
            raise ModelError(f"{e.id}: 内径は正の値が必要です")
        if e.length_m < 0 or not 0 <= e.roughness_mm/e.diameter_mm < 0.1:
            raise ModelError(f"{e.id}: 長さ/粗さが範囲外です（ε/D < 0.1）")
        if e.kind == "pipe" and e.length_m <= 0:
            raise ModelError(f"{e.id}: 直管長は正の値が必要です")
        if e.re_min < 0 or e.re_max < e.re_min or (e.k is not None and e.k < 0):
            raise ModelError(f"{e.id}: KまたはRe範囲が不正です")
        if e.kind == "expansion" and e.outlet_diameter_mm <= e.diameter_mm:
            raise ModelError(f"{e.id}: 急拡大は出口内径 > 入口内径が必要です")
        if e.kind == "contraction" and e.outlet_diameter_mm >= e.diameter_mm:
            raise ModelError(f"{e.id}: 急縮小は出口内径 < 入口内径が必要です")
        if e.kind not in ("expansion", "contraction") and e.diameter_mm != e.outlet_diameter_mm:
            raise ModelError(f"{e.id}: 径変化は専用の急拡大/急縮小部品を使ってください")
        if e.kind == "elbow":
            if not 0 <= e.angle_deg <= 180:
                raise ModelError(f"{e.id}: エルボ角度は0–180度にしてください")
            if e.bend_radius_ratio <= 0.5:
                raise ModelError(f"{e.id}: 中心線曲げ半径比 R/D > 0.5 が必要です")
            if e.elbow_mode not in ("rennels", "specified_k"):
                raise ModelError(f"{e.id}: エルボ計算方式が不正です")
            if e.elbow_mode == "specified_k" and e.angle_deg == 0 and e.k not in (None,0):
                raise ModelError(f"{e.id}: 0度エルボの指定Kは0にしてください")
        physical_ports[e.start].append(e.diameter_mm)
        physical_ports[e.end].append(e.outlet_diameter_mm)
        if e.kind == "filter":
            if e.filter_mode not in ("fixed", "curve"):
                raise ModelError(f"{e.id}: フィルター入力モードが不正です")
            if min(e.fixed_dp_kpa, e.fixed_at_lpm, e.bypass_kpa) < 0 or e.curve_viscosity_cst <= 0:
                raise ModelError(f"{e.id}: フィルター条件が不正です")
            if e.filter_mode == "curve":
                if not isinstance(e.curve, list) or len(e.curve) < 2:
                    raise ModelError(f"{e.id}: 特性曲線は2点以上必要です")
                for point in e.curve:
                    if not isinstance(point, list) or len(point) != 2 or any(isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or x < 0 for x in point):
                        raise ModelError(f"{e.id}: 特性点は非負の [L/min, kPa] を入力")
                if any(b[0] <= a[0] or b[1] < a[1] for a,b in zip(e.curve, e.curve[1:])):
                    raise ModelError(f"{e.id}: 流量は昇順、差圧は単調非減少にしてください")
    for t in project.tees:
        ports = [t.common, t.run, t.branch]
        if len(set(ports)) != 3 or any(p not in nodes for p in ports):
            raise ModelError(f"{t.id}: 3個の異なる接続ポートが必要です")
        if t.kind not in ("split", "merge") or t.reference not in ("common", "path"):
            raise ModelError(f"{t.id}: 分岐/合流またはK基準が不正です")
        if min(t.common_diameter_mm,t.run_diameter_mm,t.branch_diameter_mm) <= 0 or t.re_min < 0 or t.re_max < t.re_min:
            raise ModelError(f"{t.id}: 内径またはRe範囲が不正です")
        for p,d in zip(ports, (t.common_diameter_mm,t.run_diameter_mm,t.branch_diameter_mm)):
            physical_ports[p].append(d)
        messages.append(f"{t.id}: 経路別の指定Kモデル。流量比によるKの自動更新は未実装です")
    for n in project.nodes:
        ds = physical_ports[n.id]
        if not ds:
            raise ModelError(f"{n.id}: 未接続の節点です")
        if max(ds)-min(ds) > 1e-6:
            raise ModelError(f"{n.id}: 接続内径が不一致です。径変化部品を挿入してください")
        if len(ds) > 2:
            raise ModelError(f"{n.id}: 3本以上の接続には三ポート分岐/合流部品を使ってください")
        if n.kind in ("flow", "pressure") and len(ds) != 1:
            raise ModelError(f"{n.id}: 境界節点は部品1個に接続してください")
        if n.boundary_diameter_mm and abs(n.boundary_diameter_mm-ds[0]) > 1e-6:
            raise ModelError(f"{n.id}: 境界内径と接続内径が不一致です")
    links = compile_links(project)
    adjacent = {n.id: set() for n in project.nodes}
    for l in links:
        adjacent[l.start].add(l.end)
        adjacent[l.end].add(l.start)
    seen, todo = set(), [project.nodes[0].id]
    while todo:
        n = todo.pop()
        if n not in seen:
            seen.add(n)
            todo.extend(adjacent[n]-seen)
    if len(seen) != len(nodes):
        raise ModelError("配管網が切断されています。全節点を接続してください")
    return messages


def solve(project: Project, cancel: threading.Event | None = None):
    diagnostics = validate(project)
    links = compile_links(project)
    nodes = project.nodes
    ni = {n.id:i for i,n in enumerate(nodes)}
    li = {l.id:i for i,l in enumerate(links)}
    n, m = len(nodes), len(links)
    incidence = np.zeros((n,m))
    for j,l in enumerate(links):
        incidence[ni[l.start],j] = 1
        incidence[ni[l.end],j] = -1
    supply = np.array([nd.flow_lpm/60000 if nd.kind == "flow" else 0 for nd in nodes])
    free = [i for i,nd in enumerate(nodes) if nd.kind != "pressure"]
    pressure_nodes = [i for i,nd in enumerate(nodes) if nd.kind == "pressure"]
    qscale = max(sum(abs(supply)), max((abs(e.fixed_flow_lpm)/60000 for e in project.elements),default=0), 1/60000)
    fixed_q = np.array([getattr(l.owner, "fixed_"+l.leg+"_lpm")/60000 if l.leg else l.owner.fixed_flow_lpm/60000 for l in links])

    def losses(q):
        output = []
        for j,l in enumerate(links):
            if not l.leg:
                output.append(element_loss(l.owner,float(q[j]),project.fluid))
                continue
            t = l.owner
            combined = q[li[t.id+":run"]]+q[li[t.id+":branch"]]
            refq = combined if t.reference == "common" else q[j]
            d = t.common_diameter_mm if t.reference == "common" else getattr(t,l.leg+"_diameter_mm")
            v,re = flow_state(refq,d,project.fluid)
            k = getattr(t,"k_"+l.leg)
            out = Loss((1 if q[j]>=0 else -1)*(k or 0)*project.fluid.density_kg_m3*v*v/2,v,re,k=k,model="経路別K / "+t.reference)
            check_k_applicability(k,re,t.source,t.re_min,t.re_max,t.allow_estimate,out)
            if q[j] < -1e-12:
                out.errors.append("分岐/合流ポートの逆流：向き・種類を再設定してください")
            output.append(out)
        return output

    trial = losses(np.full(m,qscale))
    escale = max(1000.0, max(abs(x.pa) for x in trial), max(abs(nd.pressure_kpa)*1000+abs(nd.elevation_m)*project.fluid.density_kg_m3*G for nd in nodes))
    rhog = project.fluid.density_kg_m3 * G

    def boundary_energy(i,q):
        nd = nodes[i]
        result = nd.pressure_kpa*1000 + rhog*nd.elevation_m
        if nd.boundary_diameter_mm > 0:
            result += kinetic(float(incidence[i]@q),nd.boundary_diameter_mm,project.fluid)
        return result

    def residual(x):
        if cancel is not None and cancel.is_set():
            raise Cancelled("計算を中断しました")
        q = x[:m]*qscale if project.mode == "auto" else fixed_q
        energy = (x[m:] if project.mode == "auto" else x)*escale
        loss = np.array([v.pa for v in losses(q)])
        eq = list((incidence.T@energy-loss)/escale)
        if project.mode == "auto":
            eq.extend((incidence@q-supply)[free]/qscale)
        eq.extend((energy[i]-boundary_energy(i,q))/escale for i in pressure_nodes)
        return np.array(eq)

    if project.mode == "specified":
        mass_error = (incidence@fixed_q-supply)[free]
        if len(mass_error) and np.max(np.abs(mass_error)) > max(1e-12,qscale*1e-7):
            raise ModelError("指定流量が節点の連続式と一致しません。枝流量・分流比を確認してください")
    # Continuity-only initial flow guess avoids bias toward one branch.
    q0 = np.linalg.lstsq(incidence[free],supply[free],rcond=None)[0] if free else np.full(m,qscale)
    energy0 = np.full(n,boundary_energy(pressure_nodes[0],q0)) / escale
    x0 = np.r_[q0/qscale,energy0] if project.mode == "auto" else energy0
    opt = least_squares(residual,x0,xtol=1e-11,ftol=1e-11,gtol=1e-11,max_nfev=1000)
    q = opt.x[:m]*qscale if project.mode == "auto" else fixed_q
    energy = (opt.x[m:] if project.mode == "auto" else opt.x)*escale
    res = residual(opt.x)
    if not opt.success or np.max(np.abs(res)) > 1e-6:
        suffix = " 指定流量の経路間圧力差が不整合の可能性があります。" if project.mode == "specified" else " 境界条件と部品特性を確認してください。"
        raise ModelError(f"収束条件を満たしません（最大無次元残差 {np.max(np.abs(res)):.3g}）。"+suffix)
    final = losses(q)
    errors, warnings = [], list(diagnostics)
    rows = []
    for j,(l,loss) in enumerate(zip(links,final)):
        e = l.owner
        if l.leg:
            qc = q[li[e.id+":run"]]+q[li[e.id+":branch"]]
            dp = getattr(e,l.leg+"_diameter_mm")
            if e.kind == "split":
                d1,d2,q1,q2 = e.common_diameter_mm,dp,qc,q[j]
            else:
                d1,d2,q1,q2 = dp,e.common_diameter_mm,q[j],qc
            length = 0.0
        else:
            d1,d2,q1,q2 = e.diameter_mm,e.outlet_diameter_mm,q[j],q[j]
            length = e.length_m if e.kind == "pipe" else math.radians(e.angle_deg)*e.bend_radius_ratio*e.diameter_mm/1000 if e.kind == "elbow" else 0.0
        p1 = energy[ni[l.start]]-rhog*nodes[ni[l.start]].elevation_m-kinetic(q1,d1,project.fluid)
        p2 = energy[ni[l.end]]-rhog*nodes[ni[l.end]].elevation_m-kinetic(q2,d2,project.fluid)
        for message in loss.errors:
            errors.append(f"{l.id}: {message}")
        for message in loss.warnings:
            warnings.append(f"{l.id}: {message}")
        actual_loss = loss.pa * (1 if q[j]>=0 else -1)
        rows.append(dict(id=l.id,owner=e.id,kind=e.kind,start=l.start,end=l.end,
                         diameter_mm=d1,outlet_diameter_mm=d2,length_m=length,
                         flow_lpm=float(q[j])*60000,velocity_m_s=loss.velocity,re=loss.re,
                         regime="停止" if loss.re == 0 else "層流" if loss.re < 2300 else "乱流" if loss.re >= 4000 else "遷移域",
                         friction=loss.f,k=loss.k,loss_kpa=actual_loss/1000,
                         gradient_kpa_m=actual_loss/1000/length if length else None,
                         inlet_kpa=p1/1000,outlet_kpa=p2/1000,
                         inlet_total_kpa=(energy[ni[l.start]]-rhog*nodes[ni[l.start]].elevation_m)/1000,
                         outlet_total_kpa=(energy[ni[l.end]]-rhog*nodes[ni[l.end]].elevation_m)/1000,
                         power_w=float(q[j])*loss.pa,model=loss.model,source=loss.source or e.source,
                         angle_deg=e.angle_deg if e.kind == "elbow" else None,
                         bend_radius_ratio=e.bend_radius_ratio if e.kind == "elbow" else None,
                         reference_diameter_mm=(e.common_diameter_mm if e.reference == "common" else getattr(e,l.leg+"_diameter_mm")) if l.leg else min(d1,d2) if e.kind in ("expansion","contraction") else e.reference_diameter_mm if e.kind == "local" else d1,
                         status="適用外" if loss.errors else "要確認" if loss.warnings else "計算済み",
                         messages=" / ".join(loss.errors+loss.warnings)))
    for t in project.tees:
        power = sum(r["power_w"] for r in rows if r["owner"] == t.id)
        if power < -1e-7:
            errors.append(f"{t.id}: 分岐/合流全体の損失動力が負です。経路Kを見直してください")
            for row in rows:
                if row['owner'] == t.id:
                    row['status'] = '適用外'
                    row['messages'] += ' / 三ポート全体の損失動力が負'
    node_rows = []
    for i,nd in enumerate(nodes):
        candidates = [r["inlet_kpa"] for r in rows if r["start"] == nd.id]+[r["outlet_kpa"] for r in rows if r["end"] == nd.id]
        # A reservoir boundary has a distinct (near-zero velocity) reference.
        static = nd.pressure_kpa if nd.kind == "pressure" else candidates[0] if max(candidates)-min(candidates)<1e-5 else None
        node_rows.append(dict(id=nd.id,kind=nd.kind,energy_kpa=float(energy[i])/1000,
                              pressure_kpa=static,elevation_m=nd.elevation_m,
                              external_flow_lpm=float(incidence[i]@q)*60000))
    fingerprint=hashlib.sha256(json.dumps(project.to_dict(),sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()
    return dict(version=VERSION,title=project.title,rows=rows,nodes=node_rows,
                input_sha256=fingerprint,calculated_at=datetime.now(timezone.utc).isoformat(),
                valid=not errors,errors=errors,warnings=list(dict.fromkeys(warnings)),
                residual=float(np.max(np.abs(res))),evaluations=opt.nfev,
                mass_residual_lpm=float(np.max(np.abs((incidence@q-supply)[free])))*60000 if free else 0,
                energy_residual_pa=float(np.max(np.abs(incidence.T@energy-np.array([v.pa for v in final])))),
                loss_power_w=sum(r["power_w"] for r in rows),
                boundary_power_w=float((incidence@q)@energy),
                notes=project.notes)


def route_rows(result,start,end):
    """Find one actual-flow route. Refuse cycles instead of assuming seriality."""
    todo = [(start,[])]
    seen = set()
    while todo:
        node,path = todo.pop(0)
        if node == end:
            return path
        if node in seen:
            continue
        seen.add(node)
        for row in result["rows"]:
            a,b = (row["start"],row["end"]) if row["flow_lpm"]>=0 else (row["end"],row["start"])
            if a == node and abs(row["flow_lpm"])>1e-8:
                todo.append((b,path+[(row,row["flow_lpm"]>=0)]))
    return []
