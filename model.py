"""Versioned, explicit engineering inputs. UI units appear in field names."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import math
from pathlib import Path


class ModelError(ValueError):
    pass


@dataclass
class Fluid:
    name: str = "油（物性を入力）"
    temperature_c: float = 40.0
    density_kg_m3: float = 850.0
    viscosity_cst: float = 32.0


@dataclass
class Node:
    id: str
    kind: str = "junction"  # junction, flow (positive injection), pressure
    x: float = 0.0
    y: float = 0.0
    elevation_m: float = 0.0
    flow_lpm: float = 0.0
    pressure_kpa: float = 0.0  # gauge static pressure
    boundary_diameter_mm: float = 0.0  # 0 = reservoir (velocity ~ 0)


@dataclass
class Element:
    id: str
    start: str
    end: str
    kind: str = "pipe"  # pipe, local, expansion, contraction, filter, elbow
    diameter_mm: float = 25.0
    outlet_diameter_mm: float = 25.0
    length_m: float = 1.0
    roughness_mm: float = 0.045
    k: float | None = None
    reference_diameter_mm: float = 25.0
    source: str = ""
    re_min: float = 0.0
    re_max: float = 1e12
    allow_estimate: bool = False
    fixed_flow_lpm: float = 10.0
    filter_mode: str = "curve"  # curve, fixed
    curve: list[list[float]] = field(default_factory=list)  # L/min, kPa
    fixed_dp_kpa: float = 10.0
    fixed_at_lpm: float = 10.0
    curve_viscosity_cst: float = 32.0
    bypass_kpa: float = 0.0  # 0 = not specified
    angle_deg: float = 90.0
    bend_radius_ratio: float = 1.5  # centerline radius / actual inside diameter
    elbow_mode: str = "rennels"  # rennels (rounded), specified_k (actual angle)


@dataclass
class Tee:
    id: str
    common: str
    run: str
    branch: str
    kind: str = "split"  # split, merge
    common_diameter_mm: float = 25.0
    run_diameter_mm: float = 20.0
    branch_diameter_mm: float = 20.0
    k_run: float | None = None
    k_branch: float | None = None
    reference: str = "common"  # common or path
    source: str = ""
    re_min: float = 0.0
    re_max: float = 1e12
    allow_estimate: bool = False
    fixed_run_lpm: float = 5.0
    fixed_branch_lpm: float = 5.0


@dataclass
class Project:
    title: str = "新しい油配管"
    schema_version: int = 1
    fluid: Fluid = field(default_factory=Fluid)
    mode: str = "auto"  # auto, specified
    nodes: list[Node] = field(default_factory=list)
    elements: list[Element] = field(default_factory=list)
    tees: list[Tee] = field(default_factory=list)
    notes: str = ""

    def to_dict(self):
        return asdict(self)

    def clone(self):
        return project_from_dict(self.to_dict())


def _record(cls, data):
    if not isinstance(data, dict):
        raise ModelError(f"{cls.__name__}: オブジェクトが必要です")
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ModelError(f"{cls.__name__}: 未対応の項目 {sorted(unknown)}")
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        kind = str(f.type)
        if kind in ("float", "float | None"):
            if value is None and kind == "float | None":
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ModelError(f"{f.name}: 有限の数値が必要です")
        elif kind == "str" and not isinstance(value, str):
            raise ModelError(f"{f.name}: 文字列が必要です")
        elif kind == "bool" and not isinstance(value, bool):
            raise ModelError(f"{f.name}: true/falseが必要です")
    enums = {
        "Node": {"kind": {"junction", "flow", "pressure"}},
        "Element": {"kind": {"pipe", "local", "expansion", "contraction", "filter", "elbow"}, "filter_mode": {"curve", "fixed"}, "elbow_mode": {"rennels", "specified_k"}},
        "Tee": {"kind": {"split", "merge"}, "reference": {"common", "path"}},
        "Project": {"mode": {"auto", "specified"}},
    }
    for key, allowed in enums.get(cls.__name__, {}).items():
        if key in data and data[key] not in allowed:
            raise ModelError(f"{cls.__name__}.{key}: 未対応の種類です")
    try:
        return cls(**data)
    except TypeError as exc:
        raise ModelError(str(exc)) from exc


def project_from_dict(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        raise ModelError("未対応のファイル形式です（schema_version=1が必要）")
    # Reject non-finite numbers anywhere, including unused metadata.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ModelError("NaN / Infinityは保存・読込できません")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        if isinstance(value, list):
            for item in value:
                finite(item)
    finite(data)
    value = dict(data)
    try:
        value["fluid"] = _record(Fluid, value.get("fluid", {}))
        for key, cls in (("nodes", Node), ("elements", Element), ("tees", Tee)):
            if not isinstance(value.get(key, []), list):
                raise ModelError(f"{key}: 配列が必要です")
            value[key] = [_record(cls, item) for item in value.get(key, [])]
        return _record(Project, value)
    except (TypeError, AttributeError) as exc:
        raise ModelError("入力データの型が正しくありません") from exc


def load_project(path):
    try:
        return project_from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ModelError(f"JSONを読み込めません: {exc}") from exc


def save_project(project, path):
    """Atomic replace protects an existing project from partial writes."""
    path = Path(path)
    text = json.dumps(project.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)

