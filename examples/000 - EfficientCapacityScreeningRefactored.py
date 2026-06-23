"""
DC N-1 Hosting Capacity Screening — analytical PTDF/LODF method (thin wrapper)
===============================================================================

This script is the PowerFactory entry point for the analytical screener.
All screening logic lives in admittance_matrix.screening; this file only
handles:
  - Connecting to PowerFactory
  - Configuring the run via ScreeningConfig
  - Delegating to run_with_coupler_passes / run_screening / save_results_xlsx
  - Reporting timing and any errors back to the PF output window

The analytical method builds a B′ matrix and uses PTDF/LODF algebra to
compute N-1 headroom without re-running a DC load flow per contingency.
For the finite-difference method, see:
  examples/000 - ManualCapacityScreeningRefactored.py

HOW TO RUN FROM INSIDE POWERFACTORY
-------------------------------------
  1. In PowerFactory: Tools > Python > Run Script
  2. Browse to this file and click Execute.

HOW TO RUN FROM A TERMINAL (external)
--------------------------------------
  PowerFactory must be open and have a project active.

      python "000 - EfficientCapacityScreeningRefactored.py" external
      python "000 - EfficientCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2025 SP4/Python/3.11"

CONFIGURATION
-------------
  Edit _make_config() and the constants below it — they are the single
  place for all settings.
"""

import sys
import os
import time
from datetime import datetime

from admittance_matrix.screening import (
    ScreeningConfig,
    run_screening,
    run_with_coupler_passes,
    save_results_xlsx,
)
from admittance_matrix.adapters.powerfactory.dc_extractor import extract_snapshot

# =============================================================================
# CONFIG — edit everything here
# =============================================================================

_PROJECT_NAME   = "Auto_Network_Model_2027"   # "" = use the currently active project
_INJECTION_SIGN = -1.0   # -1 = load headroom, +1 = generation headroom
_COUPLER_PASSES = True   # also test each open bus coupler in the closed position


def _make_config(project_root: str) -> ScreeningConfig:
    return ScreeningConfig(
        s_base_mva             = 100.0,   # system MVA base
        loading_limit_pct      = 90.0,    # branch loading limit for N-1 check (%)
        min_busbar_kv          = 132.0,   # ignore substations below this voltage (kV)
        min_sensitivity_mw     = 1e-3,    # flows smaller than this (MW) treated as zero
        # --- substation selection (mutually exclusive) ---
        target_substations     = [],      # e.g. ["ABHA", "LAGA", "BEAT"] — overrides max_substations
        max_substations        = 10,      # 0 = all; N = first N alphabetically (when target_substations is empty)
        output_folder          = os.path.join(project_root, "output"),
        fallback_output_folder = project_root,
        output_prefix          = "Efficient_Refactored_Local_N1_Capacity_Screening",
    )


# =============================================================================
# HELPERS
# =============================================================================

class _LoggingApp:
    """Proxy that forwards all PowerFactory calls to the real app object
    while capturing every PrintInfo / PrintWarn / PrintError message into
    a list so they can be written to a log file at the end of the run.
    """

    def __init__(self, app, lines: list[str]) -> None:
        self._app   = app
        self._lines = lines

    def PrintInfo(self, msg: str) -> None:
        self._lines.append(f"[INFO]  {msg}")
        self._app.PrintInfo(msg)

    def PrintWarn(self, msg: str) -> None:
        self._lines.append(f"[WARN]  {msg}")
        self._app.PrintWarn(msg)

    def PrintError(self, msg: str) -> None:
        self._lines.append(f"[ERROR] {msg}")
        self._app.PrintError(msg)

    def __getattr__(self, name: str):
        return getattr(self._app, name)


def _save_log(path: str, lines: list[str]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as exc:
        print(f"Warning: could not save log to '{path}': {exc}")


# =============================================================================
# CORE DRIVER
# =============================================================================

def _run(app) -> None:
    """Core driver — works regardless of how *app* was obtained."""
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = _make_config(project_root)

    start_dt = datetime.now()
    timestamp = start_dt.strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(config.output_folder,
                            f"{config.output_prefix}_{timestamp}.xlsx")
    log_path = os.path.join(config.output_folder,
                            f"{config.output_prefix}_{timestamp}.txt")

    log_lines: list[str] = []
    lapp = _LoggingApp(app, log_lines)

    lapp.PrintInfo("=" * 70)
    lapp.PrintInfo("DC N-1 Hosting Capacity Screening (Refactored Package)")
    lapp.PrintInfo("=" * 70)
    lapp.PrintInfo(f"Project:        {_PROJECT_NAME or '(active)'}")
    lapp.PrintInfo(f"S_base:         {config.s_base_mva} MVA")
    lapp.PrintInfo(f"Loading limit:  {config.loading_limit_pct}%")
    lapp.PrintInfo(f"Min busbar kV:  {config.min_busbar_kv} kV")
    lapp.PrintInfo(f"Mode:           {'LOAD' if _INJECTION_SIGN < 0 else 'GENERATION'} headroom")
    lapp.PrintInfo(f"Coupler passes: {_COUPLER_PASSES}")
    lapp.PrintInfo(f"Output:         {filename}")
    lapp.PrintInfo(f"Start time:     {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    lapp.PrintInfo("=" * 70)

    summary_rows: list = []
    detail_rows:  list = []
    try:
        t_snap = time.perf_counter()
        snap = extract_snapshot(lapp, config)
        lapp.PrintInfo(f"Snapshot extraction: {time.perf_counter() - t_snap:.3f}s")
        lapp.PrintInfo(
            f"Snapshot ready: {len(snap.buses)} buses, {len(snap.branches)} branches, "
            f"{len(snap.couplers)} open/closed couplers, "
            f"{len(snap.candidates)} candidate substation(s)"
        )

        if _COUPLER_PASSES:
            summary_rows, detail_rows = run_with_coupler_passes(
                snap, None, config,
                injection_sign=_INJECTION_SIGN,
                app=lapp,
            )
        else:
            summary_rows, detail_rows = run_screening(
                snap, config,
                injection_sign=_INJECTION_SIGN,
            )

        lapp.PrintInfo("")
        lapp.PrintInfo(f"{'Substation':<30}  {'Capacity (MW)':>15}  {'Status':<25}  Binding contingency")
        lapp.PrintInfo("-" * 100)
        for row in summary_rows:
            cap = row.get("Estimated Additional Capacity (MW)", "")
            lapp.PrintInfo(
                f"{row.get('Substation', ''):<30}  {str(cap):>15}  "
                f"{row.get('Status', ''):<25}  {row.get('Binding N-1 Contingency') or ''}"
            )
        lapp.PrintInfo("")
        lapp.PrintInfo(
            f"{len(summary_rows)} substation(s) assessed, "
            f"{len(detail_rows)} circuit pair(s) evaluated."
        )

        written = save_results_xlsx(filename, summary_rows, detail_rows, config, app=lapp)
        end_dt    = datetime.now()
        elapsed_s = (end_dt - start_dt).total_seconds()
        lapp.PrintInfo(f"Results saved to: {written}")
        lapp.PrintInfo("=" * 70)
        lapp.PrintInfo(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        lapp.PrintInfo(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        lapp.PrintInfo(f"Runtime:     {elapsed_s:.1f} s")
        lapp.PrintInfo("=" * 70)

    except Exception as e:
        end_dt    = datetime.now()
        elapsed_s = (end_dt - start_dt).total_seconds()
        lapp.PrintError(f"Error: {e}")
        lapp.PrintInfo("=" * 70)
        lapp.PrintInfo(f"Start time:  {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        lapp.PrintInfo(f"Finish time: {end_dt.strftime('%Y-%m-%d %H:%M:%S')} (aborted)")
        lapp.PrintInfo(f"Runtime:     {elapsed_s:.1f} s")
        lapp.PrintInfo("=" * 70)
        if summary_rows or detail_rows:
            try:
                written = save_results_xlsx(
                    filename, summary_rows, detail_rows, config, app=lapp
                )
                lapp.PrintInfo(f"Emergency save of partial results successful: {written}")
            except Exception as save_err:
                lapp.PrintError(f"Emergency save failed: {save_err}")
        raise
    finally:
        _save_log(log_path, log_lines)


# =============================================================================
# ENTRY POINTS
# =============================================================================

def main_pf() -> None:
    """Entry point when running as a script inside PowerFactory.

    PowerFactory injects the `powerfactory` module and calls this function
    (or the module-level code) directly.
    """
    import powerfactory  # noqa: F401  (PF injects this at runtime)

    app = powerfactory.GetApplication()
    if app is None:
        raise RuntimeError("Could not connect to PowerFactory")
    app.ClearOutputWindow()
    _run(app)


def main_external(pf_install_dir: str | None = None) -> None:
    """Entry point when running from an external Python process.

    PowerFactory must be open and have a project active. The `powerfactory`
    module is loaded from *pf_install_dir* (e.g. the PF installation folder);
    if omitted it must already be importable (i.e. on sys.path).

    Typical usage from a terminal::

        python "000 - EfficientCapacityScreeningRefactored.py" external
        python "000 - EfficientCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2025 SP4/Python/3.11"
    """
    if pf_install_dir and pf_install_dir not in sys.path:
        sys.path.insert(0, pf_install_dir)

    import powerfactory  # noqa: F401

    app = powerfactory.GetApplicationExt()
    if app is None:
        raise RuntimeError(
            "Could not connect to PowerFactory. "
            "Make sure PowerFactory is running and a project is active."
        )
    if _PROJECT_NAME:
        from admittance_matrix.utils import init_project
        init_project(app, _PROJECT_NAME)
    _run(app)


if __name__ == "__main__":
    # python "000 - EfficientCapacityScreeningRefactored.py"              -> inside PF
    # python "000 - EfficientCapacityScreeningRefactored.py" external     -> external Python
    # python "000 - EfficientCapacityScreeningRefactored.py" external "C:/PF/path"
    if len(sys.argv) > 1 and sys.argv[1] == "external":
        main_external(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        main_pf()
