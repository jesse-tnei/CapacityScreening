"""
DC N-1 Hosting Capacity Screening — thin wrapper around admittance_matrix.screening
====================================================================================

This script is the PowerFactory entry point.  All screening logic now lives in
the admittance_matrix package; this file only handles:
  - Connecting to PowerFactory
  - Configuring the run via ScreeningConfig
  - Delegating to run_with_coupler_passes / save_results_xlsx
  - Reporting timing and any errors back to the PF output window

To run offline (no PowerFactory) for engine sanity checks, see:
  tests/test_screening_engine.py  (or call _toy_run() from a Python REPL)
"""

from __future__ import annotations

import os
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from admittance_matrix.screening import (
    ScreeningConfig,
    run_with_coupler_passes,
    save_results_xlsx,
)
from admittance_matrix.adapters.powerfactory.dc_extractor import extract_snapshot


def main_pf():
    """End-to-end PowerFactory driver — run from inside a PF script."""
    import powerfactory  # noqa: F401  (PF injects this at runtime)

    app = powerfactory.GetApplication()
    if app is None:
        raise RuntimeError("Could not connect to PowerFactory")
    app.ClearOutputWindow()

    config = ScreeningConfig(
        s_base_mva=100.0,
        loading_limit_pct=90.0,
        min_busbar_kv=132.0,
        min_sensitivity_mw=1e-3,
        max_substations=0,          # 0 = screen all substations
        output_folder=os.path.join(_PROJECT_ROOT, "output"),
        fallback_output_folder=os.path.join(_PROJECT_ROOT, "output"),
        output_prefix="Local_N1_Capacity_Screening",
    )

    start_dt = datetime.now()
    timestamp = start_dt.strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(config.output_folder,
                            f"{config.output_prefix}_{timestamp}.xlsx")

    app.PrintInfo("=" * 80)
    app.PrintInfo("DC N-1 Hosting Capacity Screening — analytical PTDF/LODF")
    app.PrintInfo("=" * 80)
    app.PrintInfo(f"Start time: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    app.PrintInfo(f"Output:     {filename}")
    app.PrintInfo("=" * 80)

    summary_rows: list = []
    detail_rows: list = []
    try:
        snap = extract_snapshot(app, config)
        app.PrintInfo(
            f"Snapshot: {len(snap.buses)} buses, {len(snap.branches)} branches, "
            f"{len(snap.couplers)} couplers, {len(snap.candidates)} candidate sites"
        )

        summary_rows, detail_rows = run_with_coupler_passes(
            snap, None, config,
            injection_sign=-1.0,    # LOAD headroom
            app=app,
        )

        written = save_results_xlsx(filename, summary_rows, detail_rows, config,
                                    app=app)
        end_dt = datetime.now()
        elapsed = end_dt - start_dt
        app.PrintInfo(f"Results saved to: {written}")
        app.PrintInfo(f"Done. {len(summary_rows)} substation(s) assessed.")
        app.PrintInfo(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        app.PrintInfo(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        app.PrintInfo(f"Elapsed:     {elapsed}")

    except Exception as e:
        end_dt = datetime.now()
        elapsed = end_dt - start_dt
        app.PrintError(f"Error: {e}")
        app.PrintInfo(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        app.PrintInfo(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')} (aborted)")
        app.PrintInfo(f"Elapsed:     {elapsed}")
        if summary_rows or detail_rows:
            try:
                written = save_results_xlsx(
                    filename, summary_rows, detail_rows, config, app=app
                )
                app.PrintInfo(f"Emergency save of partial results successful: {written}")
            except Exception as save_err:
                app.PrintError(f"Emergency save failed: {save_err}")
        raise


if __name__ == "__main__":
    main_pf()
