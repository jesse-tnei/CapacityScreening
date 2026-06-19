"""
DC N-1 Hosting Capacity Screening (Refactored)
===============================================

Run this script from inside PowerFactory (Script > Execute) or from a
terminal using GetApplicationExt().

HOW TO RUN FROM INSIDE POWERFACTORY
-------------------------------------
  1. In PowerFactory: Tools > Python > Run Script
  2. Browse to this file and click Execute.
  PowerFactory injects `powerfactory` automatically — no sys.path needed
  as long as the admittance_matrix package is installed in PF's Python.

HOW TO RUN FROM A TERMINAL (external)
--------------------------------------
  Set RUN_EXTERNAL = True below, then:
      python EfficiencientCapacityScreeningRefactored.py
  PowerFactory must already be open with the project active.

CONFIGURATION
-------------
  Edit the CONFIG section below.  All other code should be left as-is.
"""

import sys
import os
from datetime import datetime

# =============================================================================
# CONFIG — edit these values before running
# =============================================================================

RUN_EXTERNAL = False          # True = launched from a terminal; False = inside PF

PF_PYTHON_PATH = r"C:\Program Files\DIgSILENT\PowerFactory 2025 SP4\Python\3.11"
PROJECT_NAME   = "Auto_Network_Model_2027"   # leave "" to use the currently active project

S_BASE_MVA        = 100.0    # system MVA base
LOADING_LIMIT_PCT = 90.0     # branch loading limit for N-1 check (%)
MIN_BUSBAR_KV     = 132.0    # ignore substations below this voltage (kV)
MIN_SENSITIVITY   = 1e-3     # flows smaller than this (MW) treated as zero
MAX_SUBSTATIONS   = 1        # 0 = screen every substation; N = screen first N only

INJECTION_SIGN    = -1.0     # -1 = load headroom, +1 = generation headroom
COUPLER_PASSES    = True     # also test each open bus coupler in the closed position

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FOLDER          = os.path.join(_PROJECT_ROOT, "output")
FALLBACK_OUTPUT_FOLDER = os.path.join(_PROJECT_ROOT, "output")
OUTPUT_PREFIX          = "Efficient_Refactored_Local_N1_Capacity_Screening"

# =============================================================================
# SETUP
# =============================================================================

if RUN_EXTERNAL:
    sys.path.insert(0, PF_PYTHON_PATH)

# =============================================================================
# MAIN
# =============================================================================

def main():
    # ------------------------------------------------------------------
    # Connect to PowerFactory
    # ------------------------------------------------------------------
    if RUN_EXTERNAL:
        import powerfactory as pf
        app = pf.GetApplicationExt()
        if app is None:
            raise RuntimeError("Could not connect to PowerFactory via GetApplicationExt(). "
                               "Make sure PowerFactory is open.")
        app.Show()
        if PROJECT_NAME:
            from admittance_matrix.utils import init_project
            init_project(app, PROJECT_NAME)
    else:
        import powerfactory as pf          # injected by PF at runtime
        app = pf.GetApplication()
        if app is None:
            raise RuntimeError("Could not get PowerFactory application handle.")
        app.ClearOutputWindow()

    # ------------------------------------------------------------------
    # Build config
    # ------------------------------------------------------------------
    from admittance_matrix.screening import (
        ScreeningConfig,
        run_screening,
        run_with_coupler_passes,
        save_results_xlsx,
    )
    from admittance_matrix.adapters.powerfactory.dc_extractor import extract_snapshot

    config = ScreeningConfig(
        s_base_mva=S_BASE_MVA,
        loading_limit_pct=LOADING_LIMIT_PCT,
        min_busbar_kv=MIN_BUSBAR_KV,
        min_sensitivity_mw=MIN_SENSITIVITY,
        max_substations=MAX_SUBSTATIONS,
        output_folder=OUTPUT_FOLDER,
        fallback_output_folder=FALLBACK_OUTPUT_FOLDER,
        output_prefix=OUTPUT_PREFIX,
    )

    start_dt = datetime.now()
    timestamp = start_dt.strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(config.output_folder,
                            f"{config.output_prefix}_{timestamp}.xlsx")
    log_path = os.path.join(config.output_folder,
                            f"{config.output_prefix}_{timestamp}.txt")

    log_lines = []

    def log(msg, level="INFO"):
        log_lines.append(f"[{level}] {msg}")
        print(msg)
        try:
            if level == "ERROR":
                app.PrintError(msg)
            elif level == "WARN":
                app.PrintWarn(msg)
            else:
                app.PrintInfo(msg)
        except Exception:
            pass

    def save_log():
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("\n".join(log_lines) + "\n")
        except Exception as exc:
            print(f"Warning: could not save log to '{log_path}': {exc}")

    log("=" * 70)
    log("DC N-1 Hosting Capacity Screening (Refactored Package)")
    log("=" * 70)
    log(f"Project:        {PROJECT_NAME or '(active)'}")
    log(f"S_base:         {S_BASE_MVA} MVA")
    log(f"Loading limit:  {LOADING_LIMIT_PCT}%")
    log(f"Min busbar kV:  {MIN_BUSBAR_KV} kV")
    log(f"Mode:           {'LOAD' if INJECTION_SIGN < 0 else 'GENERATION'} headroom")
    log(f"Coupler passes: {COUPLER_PASSES}")
    log(f"Output:         {filename}")
    log(f"Start time:     {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 70)

    # ------------------------------------------------------------------
    # Extract snapshot
    # ------------------------------------------------------------------
    snap = extract_snapshot(app, config)
    log(f"Snapshot ready: {len(snap.buses)} buses, {len(snap.branches)} branches, "
        f"{len(snap.couplers)} open/closed couplers, "
        f"{len(snap.candidates)} candidate substation(s)")

    # ------------------------------------------------------------------
    # Run screening
    # ------------------------------------------------------------------
    summary_rows: list = []
    detail_rows:  list = []
    try:
        if COUPLER_PASSES:
            summary_rows, detail_rows = run_with_coupler_passes(
                snap, None, config,
                injection_sign=INJECTION_SIGN,
                app=app,
            )
        else:
            summary_rows, detail_rows = run_screening(
                snap, config,
                injection_sign=INJECTION_SIGN,
            )

        # ------------------------------------------------------------------
        # Print results to console / PF output window
        # ------------------------------------------------------------------
        log("")
        log(f"{'Substation':<30}  {'Capacity (MW)':>15}  {'Status':<25}  Binding contingency")
        log("-" * 100)
        for row in summary_rows:
            cap = row.get("Estimated Additional Capacity (MW)", "")
            log(f"{row.get('Substation',''):<30}  {str(cap):>15}  "
                f"{row.get('Status',''):<25}  {row.get('Binding N-1 Contingency') or ''}")
        log("")
        log(f"{len(summary_rows)} substation(s) assessed, {len(detail_rows)} circuit pair(s) evaluated.")

        # ------------------------------------------------------------------
        # Save to Excel
        # ------------------------------------------------------------------
        written = save_results_xlsx(filename, summary_rows, detail_rows, config, app=app)
        end_dt    = datetime.now()
        elapsed_s = (end_dt - start_dt).total_seconds()
        log(f"Results saved to: {written}")
        log("=" * 70)
        log(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"Runtime:     {elapsed_s:.1f} s")
        log("=" * 70)

    except Exception as e:
        end_dt  = datetime.now()
        elapsed_s = (end_dt - start_dt).total_seconds()
        log(f"ERROR: {e}", level="ERROR")
        log("=" * 70)
        log(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        log(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')} (aborted)")
        log(f"Runtime:     {elapsed_s:.1f} s")
        log("=" * 70)
        if summary_rows or detail_rows:
            try:
                written = save_results_xlsx(
                    filename, summary_rows, detail_rows, config, app=app
                )
                log(f"Emergency save of partial results: {written}")
            except Exception as save_err:
                log(f"Emergency save also failed: {save_err}", level="ERROR")
        raise
    finally:
        save_log()


if __name__ == "__main__":
    main()
