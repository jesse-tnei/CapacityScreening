from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class Bus:
    idx: int
    name: str
    substation: str
    v_nom_kv: float
    is_busbar: bool
    in_service: bool


@dataclass
class Branch:
    idx: int
    name: str
    cls: str                          # 'ElmLne' / 'ElmTr2' / 'ElmTr3'
    from_bus: int
    to_bus: int
    x_pu: float                       # series reactance on system MVA base
    s_rated_mva: float
    in_service: bool
    local_to_subs: list[str] = field(default_factory=list)


@dataclass
class Coupler:
    name: str
    bus_a: int
    bus_b: int
    closed: bool


@dataclass
class CandidateSite:
    substation: str
    test_bus: int
    local_branch_idx: list[int]


@dataclass
class NetworkSnapshot:
    buses: list[Bus]
    branches: list[Branch]
    couplers: list[Coupler]
    candidates: list[CandidateSite]
    slack_bus_idx: int
    intact_flows_mw: np.ndarray
    nodal_injections_mw: np.ndarray


@dataclass
class HeadroomResult:
    capacity_mw: float
    binding_outage: Optional[str]
    binding_monitor: Optional[str]
    base_loading_pct: Optional[float]
    post_loading_pct: Optional[float]
    sensitivity_pct_per_mw: Optional[float]
    status: str = "OK"
