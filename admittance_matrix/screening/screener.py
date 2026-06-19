from __future__ import annotations

import math
from typing import Callable

import numpy as np

from .config import ScreeningConfig
from .engine import PTDFLODFEngine
from .models import CandidateSite, HeadroomResult, NetworkSnapshot
from .topology import with_coupler_closed


def assess_candidate(
    snap: NetworkSnapshot,
    engine: PTDFLODFEngine,
    site: CandidateSite,
    config: ScreeningConfig,
    injection_sign: float = +1.0,
) -> tuple[HeadroomResult, list[dict]]:
    """Compute N-1 headroom for one candidate substation."""
    if len(site.local_branch_idx) < 2:
        return (
            HeadroomResult(0.0, None, None, None, None, None,
                           status="Skipped: <2 local in-service circuits"),
            [],
        )

    psi_test = engine.ptdf_column(site.test_bus)
    lodf = engine.lodf_columns(site.local_branch_idx)

    F = snap.intact_flows_mw
    branches = snap.branches

    detail_rows = []
    per_outage = []

    for jj, c_idx in enumerate(site.local_branch_idx):
        c_br = branches[c_idx]

        if np.isnan(lodf[0, jj]):
            detail_rows.append({
                "Substation": site.substation,
                "Outage (Tripped Circuit)": c_br.name,
                "Constraining?": "Islanding outage — skipped",
            })
            continue

        F_c = F[c_idx]
        outage_cap = math.inf
        outage_binding = (None, None, None, None)

        for m_idx in site.local_branch_idx:
            if m_idx == c_idx:
                continue
            m_br = branches[m_idx]

            F_m_intact = F[m_idx]
            F_m_post = F_m_intact + lodf[m_idx, jj] * F_c
            a = injection_sign * (psi_test[m_idx] + lodf[m_idx, jj] * psi_test[c_idx])

            P_lim = (config.loading_limit_pct / 100.0) * m_br.s_rated_mva

            if abs(a) < config.min_sensitivity_mw:
                hm = math.inf
            elif a > 0:
                hm = (P_lim - F_m_post) / a
            else:
                hm = (-P_lim - F_m_post) / a
            hm = max(hm, 0.0)

            base_pct = 100.0 * abs(F_m_intact) / m_br.s_rated_mva
            post_pct = 100.0 * abs(F_m_post) / m_br.s_rated_mva
            sens_pct_per_mw = 100.0 * a / m_br.s_rated_mva

            detail_rows.append({
                "Substation": site.substation,
                "Outage (Tripped Circuit)": c_br.name,
                "Outage Class": c_br.cls,
                "Monitored Circuit": m_br.name,
                "Monitored Class": m_br.cls,
                "Base Intact Loading (%)": round(base_pct, 2),
                "Post-Contingency Loading (%)": round(post_pct, 2),
                "Sensitivity (%/MW)": round(sens_pct_per_mw, 6),
                "Headroom (MW)": "Non-constraining" if hm == math.inf else round(hm, 1),
                "Constraining?": abs(a) >= config.min_sensitivity_mw,
            })

            if hm < outage_cap:
                outage_cap = hm
                outage_binding = (m_br.name, base_pct, post_pct, sens_pct_per_mw)

        per_outage.append((outage_cap, c_br.name, *outage_binding))

    if not per_outage:
        return (
            HeadroomResult(0.0, None, None, None, None, None,
                           status="Failed: all outages islanded"),
            detail_rows,
        )

    per_outage.sort(key=lambda t: t[0])
    cap, out_name, mon_name, base_pct, post_pct, sens = per_outage[0]
    status = (
        "Unconstrained" if cap == math.inf
        else "Already overloaded" if cap == 0.0
        else "OK"
    )
    return (
        HeadroomResult(cap, out_name, mon_name, base_pct, post_pct, sens, status=status),
        detail_rows,
    )


def run_screening(
    snap: NetworkSnapshot,
    config: ScreeningConfig,
    *,
    injection_sign: float = -1.0,
) -> tuple[list[dict], list[dict]]:
    """Screen all candidate sites for a given snapshot."""
    engine = PTDFLODFEngine(snap)
    summary_rows = []
    detail_rows = []
    for site in snap.candidates:
        result, drows = assess_candidate(snap, engine, site, config,
                                         injection_sign=injection_sign)
        summary_rows.append({
            "Substation": site.substation,
            "Estimated Additional Capacity (MW)": (
                round(result.capacity_mw, 1)
                if math.isfinite(result.capacity_mw) else "Unconstrained"
            ),
            "Binding Monitored Element": result.binding_monitor,
            "Binding N-1 Contingency": result.binding_outage,
            "Base Loading at Binding Element (%)": result.base_loading_pct,
            "Post-Contingency Loading at Binding Element (%)": result.post_loading_pct,
            "Sensitivity at Binding Element (%/MW)": result.sensitivity_pct_per_mw,
            "Status": result.status,
        })
        detail_rows.extend(drows)
    return summary_rows, detail_rows


def run_with_coupler_passes(
    base_snap: NetworkSnapshot,
    refresh_intact_flows: Callable[[NetworkSnapshot], NetworkSnapshot] | None,
    config: ScreeningConfig,
    *,
    injection_sign: float = -1.0,
    app=None,
) -> tuple[list[dict], list[dict]]:
    """As-is screening + one pass per open coupler local to a candidate substation."""
    candidate_subs = {c.substation for c in base_snap.candidates}
    all_summary = []
    all_detail = []

    s_asis, d_asis = run_screening(base_snap, config, injection_sign=injection_sign)
    for row in s_asis:
        row["Bus Coupler State"] = "As-is"
    for row in d_asis:
        row["Bus Coupler State"] = "As-is"
    all_summary.extend(s_asis)
    all_detail.extend(d_asis)

    n_relevant = 0
    n_skipped = 0
    for coup in base_snap.couplers:
        if coup.closed:
            continue
        sub_a = base_snap.buses[coup.bus_a].substation
        sub_b = base_snap.buses[coup.bus_b].substation
        if sub_a not in candidate_subs and sub_b not in candidate_subs:
            n_skipped += 1
            continue
        n_relevant += 1
        merged = with_coupler_closed(base_snap, coup.name)
        merged = refresh_intact_flows(merged) if refresh_intact_flows else merged
        s_c, d_c = run_screening(merged, config, injection_sign=injection_sign)
        label = f"Closed: {coup.name}"
        for row in s_c:
            row["Bus Coupler State"] = label
        for row in d_c:
            row["Bus Coupler State"] = label
        all_summary.extend(s_c)
        all_detail.extend(d_c)

    if app is not None:
        try:
            app.PrintInfo(
                f"  Coupler passes: ran {n_relevant} per-coupler pass(es); "
                f"skipped {n_skipped} open coupler(s) not local to any candidate."
            )
        except Exception:
            pass

    collapsed_summary = _collapse_summary_by_substation(all_summary)
    return collapsed_summary, all_detail


def _is_skipped_few_circuits(summary: dict) -> bool:
    status = summary.get("Status") or ""
    return status.startswith("Skipped:")


def _capacity_for_compare(summary: dict):
    cap = summary.get("Estimated Additional Capacity (MW)")
    if isinstance(cap, (int, float)):
        return float(cap)
    if isinstance(cap, str) and "Unconstrained" in cap:
        return float("inf")
    return None


def _collapse_summary_by_substation(summary_rows: list[dict]) -> list[dict]:
    """One row per substation: prefer as-is; fall back to worst per-coupler."""
    by_sub: dict[str, dict] = {}
    for row in summary_rows:
        sub = row.get("Substation")
        if sub is None:
            continue
        group = by_sub.setdefault(sub, {"as_is": None, "per_coupler": []})
        if row.get("Bus Coupler State") == "As-is":
            group["as_is"] = row
        else:
            group["per_coupler"].append(row)

    collapsed = []
    for group in by_sub.values():
        as_is = group["as_is"]
        per_coupler = group["per_coupler"]
        if as_is is not None and not _is_skipped_few_circuits(as_is):
            collapsed.append(as_is)
            continue
        valid = [
            (s, _capacity_for_compare(s))
            for s in per_coupler
            if _capacity_for_compare(s) is not None
        ]
        if valid:
            valid.sort(key=lambda t: t[1])
            collapsed.append(valid[0][0])
        elif as_is is not None:
            collapsed.append(as_is)
    return collapsed
