# N-1 Hosting Capacity Screening for PowerFactory

A Python toolkit for performing local N-1 hosting capacity screening on transmission networks modelled in DIgSILENT PowerFactory. It implements and compares two approaches:

- **Efficient method** — analytically computes headroom using DC Power Transfer Distribution Factors (PTDFs) and Line Outage Distribution Factors (LODFs) derived from the admittance matrix. Requires only a single network snapshot; no repeated load flows.
- **Manual / finite-difference method** — numerically estimates the same sensitivities by running two DC load flows per N-1 outage (at 0 MW and at a test increment). Serves as a reference to validate the efficient method.

Both methods produce identical results by design; the key difference is speed and computational cost.

## Notice

 **This library is under active development.**

Some components require further refinement — particularly the proper handling of voltage tap settings for 2-winding and 3-winding transformers. If you encounter any issues or would like to request new functionality, please [open an issue](https://github.com/jesse-tnei/CapacityScreening/issues).

## Features

- Extract a DC network snapshot (buses, branches, couplers) from a live PowerFactory session
- Build the admittance matrix and compute PTDF/LODF factors analytically
- Screen every candidate substation for N-1 load or generation headroom
- Optionally repeat the screen with each open bus coupler in the closed position
- Export results to Excel and a plain-text log
- Finite-difference screener for result validation

## Installation

### From GitHub

```bash
pip install git+https://github.com/jesse-tnei/CapacityScreening.git
```

To update to the latest version:

```bash
pip install --upgrade git+https://github.com/jesse-tnei/CapacityScreening.git
```

### Local Development

```bash
pip install -e .
```

**Dependencies:** `numpy`, `scipy`, `pandas`, `openpyxl`. PowerFactory must be installed separately.

## Quick Start

All configuration lives in two places at the top of each script: a handful of module-level constants and the `_make_config()` function. There is no separate config file.

### Efficient method (PTDF/LODF)

Open `examples/000 - EfficientCapacityScreeningRefactored.py` and edit the constants and `_make_config()`:

```python
# --- module-level constants ---
_PROJECT_NAME   = "Auto_Network_Model_2027"   # "" = use the currently active project
_INJECTION_SIGN = -1.0   # -1 = load headroom, +1 = generation headroom
_COUPLER_PASSES = True   # also test each open bus coupler in the closed position

def _make_config(project_root):
    return ScreeningConfig(
        loading_limit_pct  = 90.0,    # branch loading limit for N-1 check (%)
        min_busbar_kv      = 132.0,   # ignore substations below this voltage (kV)
        target_substations = [],      # e.g. ["ABHA", "LAGA"] — overrides max_substations
        max_substations    = 10,      # 0 = all; N = first N alphabetically
        output_prefix      = "Efficient_Refactored_Local_N1_Capacity_Screening",
        ...
    )
```

Run from inside PowerFactory (Tools > Python > Run Script), or from a terminal:

```bash
python "examples/000 - EfficientCapacityScreeningRefactored.py" external
python "examples/000 - EfficientCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2025 SP4/Python/3.11"
```

### Manual / finite-difference method

Open `examples/000 - ManualCapacityScreeningRefactored.py` and edit `_make_config()` the same way. There are no module-level constants in this script — all settings are inside the function.

```bash
# Inside PowerFactory
python "examples/000 - ManualCapacityScreeningRefactored.py"

# From an external terminal
python "examples/000 - ManualCapacityScreeningRefactored.py" external
python "examples/000 - ManualCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2024"
```

Results (Excel + plain-text log) are written to the `output/` folder.

## Comparing the Two Methods

Both methods should produce the same capacity values. To verify:

1. Set identical `target_substations` (or `max_substations`) in both scripts.
2. Run the Efficient script, then the Manual script against the same active PowerFactory project.
3. Open the two `.xlsx` files side by side — the **Summary** sheet capacity column should match within rounding.
4. Compare runtimes in the two `.txt` logs. The Efficient method is typically 10–100× faster because it builds the B′ matrix once and uses PTDF/LODF algebra instead of running a DC load flow per contingency.

| Criterion | Efficient (PTDF/LODF) | Manual (finite-difference) |
|---|---|---|
| DC load flows required | 1 (snapshot only) | 2 per N-1 outage per substation |
| Speed | Fast | Slow for large networks |
| Accuracy | Exact DC (same assumptions) | Matches Efficient to rounding |
| Coupler passes | Yes | Yes |
| When to use | Production runs | Validating the Efficient method |

## Module Structure

```
admittance_matrix/
├── adapters/
│   └── powerfactory/
│       ├── dc_extractor.py   # Extract DC network snapshot from PowerFactory
│       ├── extractor.py      # Full AC network element extraction
│       ├── loadflow.py       # Load flow execution & results
│       ├── naming.py         # Bus naming utilities
│       └── results.py        # Result dataclasses
├── core/
│   ├── elements.py           # BranchElement, ShuntElement classes
│   ├── network.py            # High-level Network wrapper
│   └── reductionEngine.py    # Network reduction engine
├── matrices/
│   ├── builder.py            # build_admittance_matrix()
│   ├── reducer.py            # Kron reduction
│   ├── analysis.py           # Power distribution ratio calculations
│   └── topology.py           # Network simplification
├── screening/
│   ├── config.py             # ScreeningConfig dataclass
│   ├── engine.py             # PTDFLODFEngine (admittance-matrix-based)
│   ├── finite_diff.py        # Finite-difference screener (manual method)
│   ├── models.py             # NetworkSnapshot, HeadroomResult dataclasses
│   ├── results.py            # Excel / log export
│   ├── screener.py           # run_screening(), run_with_coupler_passes()
│   └── topology.py           # Coupler topology helpers
└── utils/
    └── helpers.py            # Utility functions

examples/
├── 000 - EfficientCapacityScreeningRefactored.py   # Efficient PTDF/LODF method
└── 000 - ManualCapacityScreeningRefactored.py      # Finite-difference reference method

output/                        # Generated Excel reports and logs
```

## Logging

By default, the library produces no console output. To enable logging:

```python
import logging
logging.getLogger("admittance_matrix").setLevel(logging.WARNING)
```

For detailed debug output:

```python
logging.getLogger("admittance_matrix").setLevel(logging.INFO)
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).

## Citation

If you use this library in academic work, please cite it using [CITATION.cff](CITATION.cff).
