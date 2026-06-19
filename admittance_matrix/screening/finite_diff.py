"""Finite-difference N-1 hosting capacity screening.

Mirrors the public API of screener.py but uses two PowerFactory DC load
flow calls per outage (at 0 MW and +test_increment_mw) to derive branch
sensitivities numerically, rather than computing them analytically via
PTDF/LODF.

Public entry points:
    run_finite_diff_screening          -- one pass, no coupler management
    run_finite_diff_with_coupler_passes -- as-is + per-coupler passes

Both return (summary_rows, detail_rows) in the same column format as
run_screening / run_with_coupler_passes so results can be compared directly.
"""
from __future__ import annotations

import math
from typing import Any

from .config import ScreeningConfig
from .models import CandidateSite, NetworkSnapshot
from .topology import with_coupler_closed


# ---------------------------------------------------------------------------
# PowerFactory interaction helpers
# ---------------------------------------------------------------------------

def _run_dc_lf(app) -> bool:
    com_ldf = app.GetFromStudyCase("ComLdf")
    if com_ldf is None:
        return False
    com_ldf.iopt_net = 2  # DC
    return com_ldf.Execute() == 0


def _read_loading_pct(pf_obj) -> float | None:
    try:
        val = pf_obj.GetAttribute("m:loading")
        if val is not None:
            return float(val)
    except Exception:
        pass
    return None


def _load_parent_container(busbar_pf):
    """Walk up from a busbar terminal to find a suitable parent for ElmLod creation.

    Convention: create loads in the parent of ElmSubstat (the grid folder),
    matching the behaviour of the original manual script.
    """
    p = busbar_pf.GetParent()
    while p is not None:
        cls = p.GetClassName()
        if cls == "ElmSubstat":
            return p.GetParent()
        if cls in ("ElmNet", "IntPrjfolder", "ElmSite"):
            return p
        p = p.GetParent()
    return None


def _create_test_load(busbar_pf, site_name: str):
    """Create a temporary ElmLod + StaCubic at *busbar_pf*.

    Returns (load_pf, cubicle_pf) on success, or (None, None) on failure.
    """
    safe = site_name.replace(" ", "_").replace("/", "_").replace(".", "_")
    cubicle_pf = busbar_pf.CreateObject("StaCubic", f"_FD_Cubicle__{safe}")
    if cubicle_pf is None:
        return None, None

    parent = _load_parent_container(busbar_pf)
    if parent is None:
        try:
            cubicle_pf.Delete()
        except Exception:
            pass
        return None, None

    load_pf = parent.CreateObject("ElmLod", f"_FD_Load__{safe}")
    if load_pf is None:
        try:
            cubicle_pf.Delete()
        except Exception:
            pass
        return None, None

    load_pf.bus1 = cubicle_pf
    load_pf.uknom = float(getattr(busbar_pf, "uknom", 0.0) or 0.0)
    load_pf.plini = 0.0
    load_pf.qlini = 0.0
    load_pf.outserv = 0
    return load_pf, cubicle_pf


def _set_load_mw(load_pf, mw: float) -> None:
    if load_pf is None:
        return
    load_pf.plini = float(mw)
    load_pf.qlini = 0.0
    load_pf.outserv = 0


def _delete_test_load(load_pf, cubicle_pf) -> None:
    for obj in (load_pf, cubicle_pf):
        if obj is None:
            continue
        try:
            obj.Delete()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _r(val, ndp: int):
    return round(val, ndp) if isinstance(val, (int, float)) else val


def _failed_outage_row(sub_name: str, outage_name: str, outage_cls: str, reason: str) -> dict:
    return {
        "Substation": sub_name,
        "Outage (Tripped Circuit)": outage_name,
        "Outage Class": outage_cls,
        "Monitored Circuit": "",
        "Monitored Class": "",
        "Base Intact Loading (%)": "",
        "Post-Contingency Loading (%)": "",
        "Sensitivity (%/MW)": "",
        "Headroom (MW)": "",
        "Constraining?": reason,
    }


# ---------------------------------------------------------------------------
# Per-site finite-difference assessment
# ---------------------------------------------------------------------------

def _assess_site(
    app,
    site: CandidateSite,
    snap: NetworkSnapshot,
    pf_terminals: dict[str, Any],
    pf_branches: dict[str, Any],
    config: ScreeningConfig,
    test_increment_mw: float,
) -> tuple[dict, list[dict]]:
    sub_name = site.substation
    local_branch_names = [snap.branches[k].name for k in site.local_branch_idx]

    summary: dict = {
        "Substation": sub_name,
        "Test Increment (MW)": test_increment_mw,
        "Estimated Additional Capacity (MW)": None,
        "Binding Monitored Element": None,
        "Binding N-1 Contingency": None,
        "Base Loading at Binding Element (%)": None,
        "Post-Contingency Loading at Binding Element (%)": None,
        "Sensitivity at Binding Element (%/MW)": None,
        "Status": None,
    }
    detail: list[dict] = []

    if len(site.local_branch_idx) < 2:
        summary["Status"] = "Skipped: <2 local in-service circuits"
        return summary, detail

    test_bus_name = snap.buses[site.test_bus].name
    busbar_pf = pf_terminals.get(test_bus_name)
    if busbar_pf is None:
        summary["Status"] = f"Skipped: busbar PF object not found ({test_bus_name})"
        return summary, detail

    load_pf, cubicle_pf = _create_test_load(busbar_pf, sub_name)
    if load_pf is None:
        summary["Status"] = "Skipped: could not create temporary test load"
        return summary, detail

    try:
        # Intact base case (test load = 0 MW)
        _set_load_mw(load_pf, 0.0)
        if not _run_dc_lf(app):
            summary["Status"] = "Failed: intact DC load flow did not converge"
            return summary, detail

        intact_loading: dict[str, float] = {}
        for br_name in local_branch_names:
            pf_elem = pf_branches.get(br_name)
            if pf_elem is None:
                continue
            val = _read_loading_pct(pf_elem)
            if val is not None:
                intact_loading[br_name] = val

        per_outage: list[tuple] = []
        any_succeeded = False

        for c_k, c_br_name in zip(site.local_branch_idx, local_branch_names):
            c_pf = pf_branches.get(c_br_name)
            if c_pf is None:
                detail.append(_failed_outage_row(sub_name, c_br_name, snap.branches[c_k].cls, "PF object not found"))
                continue

            c_pf.outserv = 1
            try:
                # Post-contingency at 0 MW
                _set_load_mw(load_pf, 0.0)
                if not _run_dc_lf(app):
                    detail.append(_failed_outage_row(sub_name, c_br_name, snap.branches[c_k].cls, "DC LF failed (base)"))
                    continue
                base_post: dict[str, float] = {}
                for m_k, m_br_name in zip(site.local_branch_idx, local_branch_names):
                    if m_br_name == c_br_name:
                        continue
                    pf_elem = pf_branches.get(m_br_name)
                    if pf_elem is None or getattr(pf_elem, "outserv", 0) == 1:
                        continue
                    val = _read_loading_pct(pf_elem)
                    if val is not None:
                        base_post[m_br_name] = val

                # Post-contingency at +test_increment_mw
                _set_load_mw(load_pf, test_increment_mw)
                if not _run_dc_lf(app):
                    detail.append(_failed_outage_row(sub_name, c_br_name, snap.branches[c_k].cls, "DC LF failed (test)"))
                    continue
                test_post: dict[str, float] = {}
                for m_k, m_br_name in zip(site.local_branch_idx, local_branch_names):
                    if m_br_name == c_br_name:
                        continue
                    pf_elem = pf_branches.get(m_br_name)
                    if pf_elem is None or getattr(pf_elem, "outserv", 0) == 1:
                        continue
                    val = _read_loading_pct(pf_elem)
                    if val is not None:
                        test_post[m_br_name] = val

            finally:
                try:
                    c_pf.outserv = 0
                except Exception:
                    pass

            any_succeeded = True
            outage_cap = math.inf
            outage_bind: tuple = (None, None, None, None)

            for m_k, m_br_name in zip(site.local_branch_idx, local_branch_names):
                if m_br_name == c_br_name:
                    continue
                base_p = base_post.get(m_br_name)
                test_p = test_post.get(m_br_name)
                if base_p is None or test_p is None:
                    continue

                sensitivity = (test_p - base_p) / test_increment_mw  # %/MW
                headroom_pct = config.loading_limit_pct - base_p

                if abs(sensitivity) < config.min_sensitivity_mw:
                    hm = math.inf
                elif sensitivity > 0:
                    hm = headroom_pct / sensitivity if headroom_pct > 0 else 0.0
                else:
                    hm = math.inf  # load relieves this element — not constraining

                detail.append({
                    "Substation": sub_name,
                    "Outage (Tripped Circuit)": c_br_name,
                    "Outage Class": snap.branches[c_k].cls,
                    "Monitored Circuit": m_br_name,
                    "Monitored Class": snap.branches[m_k].cls,
                    "Base Intact Loading (%)": _r(intact_loading.get(m_br_name), 2),
                    "Post-Contingency Loading (%)": round(base_p, 2),
                    f"Loading at +{test_increment_mw:.0f}MW (%)": round(test_p, 2),
                    "Sensitivity (%/MW)": round(sensitivity, 6),
                    "Headroom (MW)": "Non-constraining" if hm == math.inf else round(hm, 1),
                    "Constraining?": abs(sensitivity) >= config.min_sensitivity_mw,
                })

                if hm < outage_cap:
                    outage_cap = hm
                    outage_bind = (m_br_name, intact_loading.get(m_br_name), base_p, sensitivity)

            per_outage.append((outage_cap, c_br_name, *outage_bind))

        if not any_succeeded:
            summary["Status"] = "Failed: no outage produced a converged DC load flow"
            return summary, detail

        per_outage.sort(key=lambda t: t[0])
        cap, out_name, mon_name, base_pct, post_pct, sens = per_outage[0]
        status = (
            "Unconstrained" if cap == math.inf
            else "Already overloaded" if cap == 0.0
            else "OK"
        )
        summary.update({
            "Estimated Additional Capacity (MW)": "Unconstrained" if cap == math.inf else round(cap, 1),
            "Binding Monitored Element": mon_name,
            "Binding N-1 Contingency": out_name,
            "Base Loading at Binding Element (%)": _r(base_pct, 2),
            "Post-Contingency Loading at Binding Element (%)": _r(post_pct, 2),
            "Sensitivity at Binding Element (%/MW)": _r(sens, 6),
            "Status": status,
        })
        return summary, detail

    finally:
        try:
            _set_load_mw(load_pf, 0.0)
        except Exception:
            pass
        _delete_test_load(load_pf, cubicle_pf)


# ---------------------------------------------------------------------------
# Summary collapse (mirrors screener.py logic)
# ---------------------------------------------------------------------------

def _is_skipped(summary: dict) -> bool:
    return (summary.get("Status") or "").startswith("Skipped:")


def _capacity_numeric(summary: dict):
    cap = summary.get("Estimated Additional Capacity (MW)")
    if isinstance(cap, (int, float)):
        return float(cap)
    if isinstance(cap, str) and "Unconstrained" in cap:
        return float("inf")
    return None


def _collapse_by_substation(summary_rows: list[dict]) -> list[dict]:
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
        if as_is is not None and not _is_skipped(as_is):
            collapsed.append(as_is)
            continue
        valid = [(s, _capacity_numeric(s)) for s in per_coupler if _capacity_numeric(s) is not None]
        if valid:
            valid.sort(key=lambda t: t[1])
            collapsed.append(valid[0][0])
        elif as_is is not None:
            collapsed.append(as_is)
    return collapsed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_finite_diff_screening(
    snap: NetworkSnapshot,
    pf_terminals: dict[str, Any],
    pf_branches: dict[str, Any],
    app,
    config: ScreeningConfig,
    *,
    test_increment_mw: float = 50.0,
) -> tuple[list[dict], list[dict]]:
    """Finite-difference N-1 screening for all candidate sites in *snap*.

    Runs two PowerFactory DC load flows per outage per candidate site.
    Returns (summary_rows, detail_rows) in the same column format as
    run_screening so results can be compared directly.
    """
    summary_rows: list[dict] = []
    detail_rows: list[dict] = []
    for site in snap.candidates:
        s, d = _assess_site(app, site, snap, pf_terminals, pf_branches, config, test_increment_mw)
        summary_rows.append(s)
        detail_rows.extend(d)
    return summary_rows, detail_rows


def run_finite_diff_with_coupler_passes(
    base_snap: NetworkSnapshot,
    pf_terminals: dict[str, Any],
    pf_branches: dict[str, Any],
    pf_couplers: dict[str, Any],
    app,
    config: ScreeningConfig,
    *,
    test_increment_mw: float = 50.0,
) -> tuple[list[dict], list[dict]]:
    """As-is screening + one pass per open coupler, managing PF coupler state.

    For each open coupler that is local to a candidate substation:
      1. Sets on_off = 1 in PowerFactory (so DC LF sees the closed topology).
      2. Gets a topology-consistent snapshot via with_coupler_closed().
      3. Runs run_finite_diff_screening on that snapshot.
      4. Restores the coupler's original on_off state.

    Returns collapsed (one row per substation) summary_rows and full detail_rows.
    """
    candidate_subs = {c.substation for c in base_snap.candidates}
    all_summary: list[dict] = []
    all_detail: list[dict] = []

    # As-is pass
    s_asis, d_asis = run_finite_diff_screening(
        base_snap, pf_terminals, pf_branches, app, config,
        test_increment_mw=test_increment_mw,
    )
    for row in s_asis:
        row["Bus Coupler State"] = "As-is"
    for row in d_asis:
        row["Bus Coupler State"] = "As-is"
    all_summary.extend(s_asis)
    all_detail.extend(d_asis)

    # Per-coupler passes — only open couplers local to candidate substations
    for coup in base_snap.couplers:
        if coup.closed:
            continue
        sub_a = base_snap.buses[coup.bus_a].substation
        sub_b = base_snap.buses[coup.bus_b].substation
        if sub_a not in candidate_subs and sub_b not in candidate_subs:
            continue

        coup_pf = pf_couplers.get(coup.name)
        if coup_pf is None:
            try:
                app.PrintWarn(f"  Coupler PF object not found: {coup.name} — skipping pass")
            except Exception:
                pass
            continue

        label = f"Closed: {coup_pf.loc_name}"
        orig_on_off = getattr(coup_pf, "on_off", None)
        try:
            coup_pf.on_off = 1
            merged_snap = with_coupler_closed(base_snap, coup.name)
            s_c, d_c = run_finite_diff_screening(
                merged_snap, pf_terminals, pf_branches, app, config,
                test_increment_mw=test_increment_mw,
            )
            for row in s_c:
                row["Bus Coupler State"] = label
            for row in d_c:
                row["Bus Coupler State"] = label
            all_summary.extend(s_c)
            all_detail.extend(d_c)
        finally:
            if orig_on_off is not None:
                try:
                    coup_pf.on_off = orig_on_off
                except Exception:
                    pass

    return _collapse_by_substation(all_summary), all_detail
