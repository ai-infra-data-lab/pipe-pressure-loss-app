"""Darcy losses and component laws. No GUI and no silently guessed K factors."""
from dataclasses import dataclass, field
import math

from model import Element, Fluid


G = 9.80665
ELBOW_SOURCE = "Rennels & Hudson (2012), rounded bend; https://fluids.readthedocs.io/fluids.fittings.html#fluids.fittings.bend_rounded"


def rounded_elbow_k(angle_deg, radius_ratio, darcy_f):
    """Rennels rounded-bend equation, angle in degrees, radius on centerline.

    Includes bend-wall friction. Smooth turbulent calibration; see ELBOW_SOURCE.
    This implements the published equation, without importing library code.
    """
    if not 0 <= angle_deg <= 180 or radius_ratio <= 0.5:
        raise ValueError("エルボは角度0–180度、中心線曲げ半径比 R/D > 0.5 が必要です")
    alpha = math.radians(angle_deg)
    s = math.sin(alpha/2)
    return (darcy_f*alpha*radius_ratio + (0.10+2.4*darcy_f)*s
            + 6.6*darcy_f*(math.sqrt(s)+s)/radius_ratio**(4*alpha/math.pi))


def area(d_mm):
    return math.pi * (d_mm / 1000.0) ** 2 / 4


def flow_state(q_m3s, d_mm, fluid):
    v = q_m3s / area(d_mm)
    re = abs(v) * d_mm / 1000.0 / (fluid.viscosity_cst * 1e-6)
    return v, re


def colebrook(re, relative_roughness):
    x = 7.0
    for _ in range(80):
        new = -2 * math.log10(relative_roughness / 3.7 + 2.51 * x / re)
        if abs(new - x) < 1e-12:
            return 1 / new**2
        x = new
    raise ArithmeticError("Colebrook式が収束しません")


def friction_factor(re, relative_roughness):
    if re <= 0:
        return 0.0, "停止"
    if re < 2300:
        return 64 / re, "層流"
    if re >= 4000:
        return colebrook(re, relative_roughness), "乱流"
    w = (re - 2300) / 1700
    return (1-w) * 64/2300 + w * colebrook(4000, relative_roughness), "遷移域"


def kinetic(q, d_mm, fluid):
    v, re = flow_state(q, d_mm, fluid)
    # Interpolate alpha consistently with the friction transition convention.
    alpha = 2.0 if re < 2300 else 1.0 if re >= 4000 else 2 - (re-2300)/1700
    return alpha * fluid.density_kg_m3 * v*v / 2


@dataclass
class Loss:
    pa: float
    velocity: float
    re: float
    f: float = 0.0
    k: float | None = None
    model: str = ""
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source: str = ""


def check_k_applicability(value, re, source, re_min, re_max, estimate, result):
    reasons = []
    if value is None:
        result.errors.append("Kが未設定です")
        return
    if not source.strip():
        reasons.append("Kの出典が未入力")
    if not re_min <= re <= re_max:
        reasons.append(f"KのRe適用範囲外（{re_min:g}–{re_max:g}）")
    if re < 4000:
        reasons.append("低ReのK適用根拠を確認してください")
    if reasons:
        # A low-Re warning alone does not invalidate a measured coefficient
        # explicitly documented by the user for this range.
        hard = not source.strip() or not re_min <= re <= re_max
        if hard and not estimate:
            result.errors.extend(reasons)
        else:
            result.warnings.extend(reasons)
    if estimate:
        result.warnings.append("ユーザー指定の概算係数")


def element_loss(element: Element, q: float, fluid: Fluid):
    e = element
    v, re = flow_state(q, e.diameter_mm, fluid)
    out = Loss(0.0, v, re)
    sign = -1.0 if q < 0 else 1.0
    if e.kind == "pipe":
        f, regime = friction_factor(re, e.roughness_mm/e.diameter_mm)
        out.pa = sign * f * e.length_m / (e.diameter_mm/1000) * fluid.density_kg_m3 * v*v/2
        out.f, out.model = f, "Darcy–Weisbach / " + regime
        if regime == "遷移域":
            out.warnings.append("遷移域：摩擦係数を補間した推定値")
        if 0 < re < 2300 and e.length_m < 0.05*re*e.diameter_mm/1000:
            out.warnings.append("層流助走長の目安より短い直管：発達流仮定を確認")
    elif e.kind == "elbow":
        if e.elbow_mode == "specified_k":
            out.k = e.k
            out.model = f"エルボ {e.angle_deg:g}° / 指定K（入力角度の値）"
            check_k_applicability(e.k,re,e.source,e.re_min,e.re_max,e.allow_estimate,out)
        else:
            out.source = ELBOW_SOURCE
            out.model = f"Rennels 丸形エルボ {e.angle_deg:g}° / R/D={e.bend_radius_ratio:g}（曲管摩擦込み）"
            out.f,_ = friction_factor(re,e.roughness_mm/e.diameter_mm)
            out.k = rounded_elbow_k(e.angle_deg,e.bend_radius_ratio,out.f)
            if e.angle_deg > 0 and re > 0:
                issues=[]
                if re < 4000:
                    issues.append("エルボ自動K：低Reでの適用は未確認")
                if e.roughness_mm > 0:
                    issues.append("エルボ自動K：滑面以外は摩擦係数による拡張で適用未確認")
                (out.warnings if e.allow_estimate else out.errors).extend(issue + ("（概算）" if e.allow_estimate else "") for issue in issues)
                if e.allow_estimate:
                    out.warnings.append("エルボ自動Kを概算として使用")
        out.pa = sign * (out.k or 0.0)*fluid.density_kg_m3*v*v/2
    elif e.kind in ("local", "contraction", "expansion"):
        ref_d = min(e.diameter_mm, e.outlet_diameter_mm) if e.kind != "local" else e.reference_diameter_mm
        vr, rr = flow_state(q, ref_d, fluid)
        out.velocity, out.re = vr, rr
        if e.kind == "expansion" and e.k is None:
            out.k = (1 - area(e.diameter_mm)/area(e.outlet_diameter_mm))**2
            out.model = "Borda–Carnot（乱流）"
            if 0 < rr < 4000:
                target = out.warnings if e.allow_estimate else out.errors
                target.append("急拡大：Borda–Carnot式の低Re適用は未確認" + ("（概算）" if e.allow_estimate else ""))
        else:
            out.k, out.model = e.k, "指定K / 小径側" if e.kind != "local" else "指定K / 参照径"
            check_k_applicability(e.k, rr, e.source, e.re_min, e.re_max, e.allow_estimate, out)
        out.pa = sign * (out.k or 0.0) * fluid.density_kg_m3 * vr*vr/2
        if q < -1e-12 and e.kind in ("contraction", "expansion"):
            out.errors.append("径変化の逆流：部品の向き・種類を変更してください")
    elif e.kind == "filter":
        out.model = "フィルター / " + ("曲線補間" if e.filter_mode == "curve" else "指定運転点")
        ql = abs(q)*60000
        if e.filter_mode == "fixed":
            out.pa = sign * e.fixed_dp_kpa*1000
            if abs(ql-e.fixed_at_lpm) > max(1e-5, e.fixed_at_lpm*1e-5):
                out.errors.append(f"指定差圧は {e.fixed_at_lpm:g} L/min 専用です（流量が不一致）")
        else:
            points = e.curve
            if len(points) < 2:
                out.errors.append("フィルター曲線には2点以上必要です")
                return out
            # Linear endpoint continuation is used ONLY during iteration. An
            # out-of-domain final solution is always rejected below.
            i = 0
            while i < len(points)-2 and ql > points[i+1][0]:
                i += 1
            x1, y1 = points[i]
            x2, y2 = points[i+1]
            out.pa = sign * max(0.0, y1 + (y2-y1)*(ql-x1)/(x2-x1))*1000
            if ql < points[0][0]-1e-5 or ql > points[-1][0]+1e-5:
                out.errors.append("フィルター曲線の流量範囲外（外挿結果は採用不可）")
            if abs(fluid.viscosity_cst-e.curve_viscosity_cst) > 1e-6:
                out.errors.append("フィルター曲線の基準粘度と油の粘度が不一致")
        if not e.source.strip():
            (out.warnings if e.allow_estimate else out.errors).append("フィルター特性の出典が未入力")
        if e.allow_estimate:
            out.warnings.append("ユーザー指定の仮想・概算フィルター特性")
        if q < -1e-12:
            out.errors.append("フィルターの逆流特性は未対応")
        if e.bypass_kpa > 0 and abs(out.pa) > e.bypass_kpa*1000:
            out.errors.append("バイパス開弁差圧を超過：バイパス流量の計算が必要")
        if q == 0:
            out.pa = 0.0
    return out
