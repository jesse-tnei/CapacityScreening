"""DC load-flow extraction for N-1 screening.

Produces a NetworkSnapshot — a PF-free, index-based dataclass — that is
the sole input to PTDFLODFEngine.  All PowerFactory object references are
resolved and discarded before this function returns.
"""
from __future__ import annotations

import math

import numpy as np

from admittance_matrix.screening.config import ScreeningConfig
from admittance_matrix.screening.models import (
    Branch, Bus, CandidateSite, Coupler, NetworkSnapshot,
)
from admittance_matrix.screening.topology import (
    merge_closed_couplers, prune_to_slack_component,
)


def _resolve_bus_idx(cubicle, bus_idx_by_fullname: dict[str, int]):
    """StaCubic -> bus index via cterm. None if anything is missing."""
    if cubicle is None:
        return None
    term = getattr(cubicle, "cterm", None)
    if term is None:
        return None
    try:
        return bus_idx_by_fullname.get(term.GetFullName())
    except Exception:
        return None


def _line_x_pu_sys(elem, s_base: float):
    """Return (x_pu on system base, S_rated_MVA) for an ElmLne."""
    typ = getattr(elem, "typ_id", None)
    if typ is None:
        return None, None
    length_km = float(getattr(elem, "dline", 0.0) or 0.0)
    x_per_km = float(getattr(typ, "xline", 0.0) or 0.0)
    v_kv = float(getattr(typ, "uline", 0.0) or 0.0)
    i_rated_ka = float(getattr(typ, "sline", 0.0) or 0.0)
    if v_kv <= 0 or length_km <= 0:
        return None, None
    x_total_ohm = length_km * x_per_km
    z_base_ohm = (v_kv ** 2) / s_base
    x_pu = x_total_ohm / z_base_ohm
    s_rated = math.sqrt(3.0) * v_kv * i_rated_ka
    return x_pu, s_rated


def _tr2_x_pu_sys(elem, s_base: float):
    """Return (x_pu on system base, S_rated_MVA) for an ElmTr2.

    Rigorous DC-LF reactance: x_pu_typ = sqrt((uk/100)^2 - (pcu/(1000*strn))^2),
    then converted from type base to system base via S_base/strn.
    """
    typ = getattr(elem, "typ_id", None)
    if typ is None:
        return None, None
    strn = float(getattr(typ, "strn", 0.0) or 0.0)
    if strn <= 0:
        return None, None
    uk_pct = float(getattr(typ, "uktr", 0.0) or 0.0)
    pcu_kw = float(getattr(typ, "pcutr", 0.0) or 0.0)
    z_pu_typ = uk_pct / 100.0
    r_pu_typ = pcu_kw / (1000.0 * strn)
    x_pu_typ = math.sqrt(max(z_pu_typ ** 2 - r_pu_typ ** 2, 0.0))
    if x_pu_typ <= 0:
        return None, None
    x_pu_sys = x_pu_typ * (s_base / strn)
    return x_pu_sys, strn


def _tr3_hm_x_pu_sys(elem, s_base: float):
    """Treat an ElmTr3 as a Tr2 between HV and MV.

    The tertiary (LV) is a delta to a synthetic junction terminal with no
    real-power flow path, so the only DC flow path is H<->M.
    """
    typ = getattr(elem, "typ_id", None)
    if typ is None:
        return None, None
    strn_h = float(getattr(typ, "strn3_h", 0.0) or 0.0)
    strn_m = float(getattr(typ, "strn3_m", 0.0) or 0.0)
    if strn_h <= 0 or strn_m <= 0:
        return None, None
    base_hm = min(strn_h, strn_m)
    uk_hm_pct = float(getattr(typ, "uktr3_h", 0.0) or 0.0)
    pcu_h_kw = float(getattr(typ, "pcut3_h", 0.0) or 0.0)
    z_pu_typ = uk_hm_pct / 100.0
    r_pu_typ = pcu_h_kw / (1000.0 * base_hm)
    x_pu_typ = math.sqrt(max(z_pu_typ ** 2 - r_pu_typ ** 2, 0.0))
    if x_pu_typ <= 0:
        return None, None
    x_pu_sys = x_pu_typ * (s_base / base_hm)
    return x_pu_sys, base_hm


def _terminal_substation_name(term) -> str:
    p = term.GetParent() if term else None
    while p is not None:
        if p.GetClassName() == "ElmSubstat":
            return p.loc_name
        p = p.GetParent()
    return ""


def _branch_local_subs(from_idx, to_idx, buses: list[Bus]) -> list[str]:
    subs: set[str] = set()
    if from_idx is not None and buses[from_idx].substation:
        subs.add(buses[from_idx].substation)
    if to_idx is not None and buses[to_idx].substation:
        subs.add(buses[to_idx].substation)
    return list(subs)


def _find_slack_bus(app, bus_idx_by_fullname: dict[str, int]) -> int:
    """Locate the slack bus index.

    Looks for, in order:
      1. ElmXnet with bustp == 'SL'
      2. ElmSym with ip_ctrl == 1
      3. ElmGenstat with ip_ctrl == 1

    Raises if none found — better to fail loudly than silently mis-factor B'.
    """
    candidates = []
    for cls in ("*.ElmXnet", "*.ElmSym", "*.ElmGenstat"):
        for elem in app.GetCalcRelevantObjects(cls) or []:
            if getattr(elem, "outserv", 0) == 1:
                continue
            is_slack = (
                getattr(elem, "bustp", "") == "SL"
                or getattr(elem, "ip_ctrl", 0) == 1
            )
            if not is_slack:
                continue
            cub = getattr(elem, "bus1", None)
            term = getattr(cub, "cterm", None) if cub else None
            if term is None:
                continue
            idx = bus_idx_by_fullname.get(term.GetFullName())
            if idx is not None:
                candidates.append((elem.GetClassName(), elem.loc_name, idx))
    if not candidates:
        raise RuntimeError(
            "Could not identify slack bus (no ElmXnet bustp='SL' "
            "and no machine with ip_ctrl=1)."
        )
    if len({c[2] for c in candidates}) > 1:
        for c in candidates:
            if c[0] == "ElmXnet":
                return c[2]
    return candidates[0][2]


def _run_dc_lf(app) -> bool:
    """Run a DC load flow. Returns True on success."""
    com_ldf = app.GetFromStudyCase("ComLdf")
    if com_ldf is None:
        return False
    com_ldf.iopt_net = 2  # DC
    return com_ldf.Execute() == 0


def _collect_pf_objects(app) -> tuple[dict, dict, dict]:
    """Walk the PF network once and return live object references keyed by GetFullName().

    Returns (pf_terminals, pf_branches, pf_couplers) — only in-service objects.
    Intended to complement extract_snapshot so the finite-difference screener
    can look up PF objects by name after the snapshot has been built.
    """
    pf_terminals: dict[str, object] = {}
    for term in app.GetCalcRelevantObjects("*.ElmTerm") or []:
        if getattr(term, "outserv", 0) == 1:
            continue
        try:
            pf_terminals[term.GetFullName()] = term
        except Exception:
            pass

    pf_branches: dict[str, object] = {}
    for cls in ("*.ElmLne", "*.ElmTr2", "*.ElmTr3"):
        for elem in app.GetCalcRelevantObjects(cls) or []:
            if getattr(elem, "outserv", 0) == 1:
                continue
            try:
                pf_branches[elem.GetFullName()] = elem
            except Exception:
                pass

    pf_couplers: dict[str, object] = {}
    for c in app.GetCalcRelevantObjects("*.ElmCoup") or []:
        if getattr(c, "outserv", 0) == 1:
            continue
        try:
            pf_couplers[c.GetFullName()] = c
        except Exception:
            pass

    return pf_terminals, pf_branches, pf_couplers


def extract_snapshot_with_objects(
    app, config: ScreeningConfig | None = None
) -> tuple[NetworkSnapshot, dict, dict, dict]:
    """Like extract_snapshot, but also returns live PowerFactory object dicts.

    The snapshot is fully PF-free (safe to use without PF attached).  The
    returned dicts hold live PF references keyed by GetFullName() and are
    intended for the finite-difference screener which must trip circuits and
    run DC load flows directly inside PowerFactory.

    Returns:
        snap           -- NetworkSnapshot (no PF references)
        pf_terminals   -- {fullname: ElmTerm}
        pf_branches    -- {fullname: ElmLne | ElmTr2 | ElmTr3}
        pf_couplers    -- {fullname: ElmCoup}
    """
    snap = extract_snapshot(app, config)
    pf_terminals, pf_branches, pf_couplers = _collect_pf_objects(app)
    return snap, pf_terminals, pf_branches, pf_couplers


def extract_snapshot(app, config: ScreeningConfig | None = None) -> NetworkSnapshot:
    """One-shot extraction of every input the screening engine needs.

    Returns a NetworkSnapshot with no remaining PowerFactory references —
    safe to pickle, pass between processes, or feed to the engine without
    PF being attached.
    """
    if config is None:
        config = ScreeningConfig()
    s_base = config.s_base_mva

    # ---- Buses ----------------------------------------------------------------
    buses: list[Bus] = []
    bus_idx_by_fullname: dict[str, int] = {}
    for term in app.GetCalcRelevantObjects("*.ElmTerm") or []:
        if getattr(term, "outserv", 0) == 1:
            continue
        try:
            fn = term.GetFullName()
        except Exception:
            continue
        idx = len(buses)
        buses.append(Bus(
            idx=idx,
            name=fn,
            substation=_terminal_substation_name(term),
            v_nom_kv=float(getattr(term, "uknom", 0.0) or 0.0),
            is_busbar=(getattr(term, "iUsage", None) == 0),
            in_service=True,
        ))
        bus_idx_by_fullname[fn] = idx

    # ---- Branches -------------------------------------------------------------
    pf_obj_by_branch_idx: dict[int, object] = {}
    branches: list[Branch] = []

    def _add_branch(elem, cls: str, attr_from: str, attr_to: str,
                    x_pu, s_rated) -> None:
        if x_pu is None or x_pu <= 0 or s_rated is None or s_rated <= 0:
            return
        f_idx = _resolve_bus_idx(getattr(elem, attr_from, None), bus_idx_by_fullname)
        t_idx = _resolve_bus_idx(getattr(elem, attr_to, None), bus_idx_by_fullname)
        if f_idx is None or t_idx is None or f_idx == t_idx:
            return
        bidx = len(branches)
        branches.append(Branch(
            idx=bidx,
            name=elem.GetFullName(),
            cls=cls,
            from_bus=f_idx, to_bus=t_idx,
            x_pu=x_pu, s_rated_mva=s_rated,
            in_service=True,
            local_to_subs=_branch_local_subs(f_idx, t_idx, buses),
        ))
        pf_obj_by_branch_idx[bidx] = elem

    for elem in app.GetCalcRelevantObjects("*.ElmLne") or []:
        if getattr(elem, "outserv", 0) == 1:
            continue
        x_pu, s_rated = _line_x_pu_sys(elem, s_base)
        _add_branch(elem, "ElmLne", "bus1", "bus2", x_pu, s_rated)

    for elem in app.GetCalcRelevantObjects("*.ElmTr2") or []:
        if getattr(elem, "outserv", 0) == 1:
            continue
        x_pu, s_rated = _tr2_x_pu_sys(elem, s_base)
        _add_branch(elem, "ElmTr2", "bushv", "buslv", x_pu, s_rated)

    for elem in app.GetCalcRelevantObjects("*.ElmTr3") or []:
        if getattr(elem, "outserv", 0) == 1:
            continue
        x_pu, s_rated = _tr3_hm_x_pu_sys(elem, s_base)
        _add_branch(elem, "ElmTr3", "bushv", "busmv", x_pu, s_rated)

    # ---- Couplers -------------------------------------------------------------
    couplers: list[Coupler] = []
    for c in app.GetCalcRelevantObjects("*.ElmCoup") or []:
        if getattr(c, "outserv", 0) == 1:
            continue
        a_idx = _resolve_bus_idx(getattr(c, "bus1", None), bus_idx_by_fullname)
        b_idx = _resolve_bus_idx(getattr(c, "bus2", None), bus_idx_by_fullname)
        if a_idx is None or b_idx is None or a_idx == b_idx:
            continue
        couplers.append(Coupler(
            name=c.GetFullName(),
            bus_a=a_idx, bus_b=b_idx,
            closed=(getattr(c, "on_off", 1) == 1),
        ))

    # ---- Slack bus ------------------------------------------------------------
    slack_bus_idx = _find_slack_bus(app, bus_idx_by_fullname)

    # ---- Intact flows: one DC LF, then read m:P:bus1 on every branch ---------
    if not _run_dc_lf(app):
        raise RuntimeError(
            "Initial DC load flow did not converge — cannot extract intact flows."
        )
    intact_flows = np.zeros(len(branches))
    for bidx, elem in pf_obj_by_branch_idx.items():
        try:
            intact_flows[bidx] = float(elem.GetAttribute("m:P:bus1"))
        except Exception:
            intact_flows[bidx] = 0.0

    # ---- Candidate sites ------------------------------------------------------
    sub_to_bus_indices: dict[str, list[int]] = {}
    for b in buses:
        if not b.substation:
            continue
        sub_to_bus_indices.setdefault(b.substation, []).append(b.idx)

    branches_by_sub: dict[str, list[int]] = {}
    for br in branches:
        for s in br.local_to_subs:
            branches_by_sub.setdefault(s, []).append(br.idx)

    candidates: list[CandidateSite] = []
    for sub_name, b_idxs in sorted(sub_to_bus_indices.items()):
        eligible_busbars = [
            buses[i] for i in b_idxs
            if buses[i].is_busbar and buses[i].v_nom_kv >= config.min_busbar_kv
        ]
        if eligible_busbars:
            test_bus = max(eligible_busbars, key=lambda b: b.v_nom_kv).idx
        else:
            fallback = [
                buses[i] for i in b_idxs
                if buses[i].v_nom_kv >= config.min_busbar_kv
            ]
            if not fallback:
                continue
            test_bus = max(fallback, key=lambda b: b.v_nom_kv).idx

        local_idx = branches_by_sub.get(sub_name, [])
        if len(local_idx) < 2:
            continue
        candidates.append(CandidateSite(
            substation=sub_name,
            test_bus=test_bus,
            local_branch_idx=local_idx,
        ))

    if config.max_substations and len(candidates) > config.max_substations:
        candidates = candidates[: config.max_substations]

    try:
        n_subs = len(sub_to_bus_indices)
        n_eligible = sum(
            1 for sub_name, b_idxs in sub_to_bus_indices.items()
            if any(buses[i].is_busbar and buses[i].v_nom_kv >= config.min_busbar_kv
                   for i in b_idxs)
        )
        n_two_plus = sum(
            1 for s in sub_to_bus_indices
            if len(branches_by_sub.get(s, [])) >= 2
        )
        app.PrintInfo(
            f"  Candidate funnel: {n_subs} substations with buses; "
            f"{n_eligible} have a busbar >= {config.min_busbar_kv} kV; "
            f"{n_two_plus} have >= 2 local branches; "
            f"{len(candidates)} pass both filters."
        )
    except Exception:
        pass

    raw_snap = NetworkSnapshot(
        buses=buses,
        branches=branches,
        couplers=couplers,
        candidates=candidates,
        slack_bus_idx=slack_bus_idx,
        intact_flows_mw=intact_flows,
        nodal_injections_mw=np.zeros(len(buses)),
    )

    merged = merge_closed_couplers(raw_snap)
    n_closed = sum(1 for c in raw_snap.couplers if c.closed)
    try:
        app.PrintInfo(
            f"  Closed-coupler merge: {len(raw_snap.buses)} buses + "
            f"{n_closed} closed couplers -> {len(merged.buses)} "
            f"calculation nodes (open couplers remaining: {len(merged.couplers)})"
        )
    except Exception:
        pass

    pruned = prune_to_slack_component(merged)
    if len(pruned.buses) != len(merged.buses):
        try:
            app.PrintInfo(
                f"  Slack-component prune: {len(merged.buses)} -> "
                f"{len(pruned.buses)} buses, {len(merged.branches)} -> "
                f"{len(pruned.branches)} branches "
                f"(dropped {len(merged.buses) - len(pruned.buses)} orphans)"
            )
        except Exception:
            pass

    return pruned
