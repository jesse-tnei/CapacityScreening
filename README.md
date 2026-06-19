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

### Efficient method (PTDF/LODF)

Run `examples/000 - EfficientCapacityScreeningRefactored.py` from inside PowerFactory or from a terminal with PowerFactory open.

Edit the `CONFIG` block at the top of the script:

```python
RUN_EXTERNAL      = False          # True = launched from terminal; False = inside PF
PROJECT_NAME      = "Auto_Network_Model_2027"
S_BASE_MVA        = 100.0
LOADING_LIMIT_PCT = 90.0           # branch loading limit for N-1 check (%)
MIN_BUSBAR_KV     = 132.0          # ignore substations below this voltage
INJECTION_SIGN    = -1.0           # -1 = load headroom, +1 = generation headroom
COUPLER_PASSES    = True           # also test each open bus coupler closed
```

Then run from a terminal:

```bash
python "examples/000 - EfficientCapacityScreeningRefactored.py"
```

### Manual / finite-difference method

Run `examples/000 - ManualCapacityScreeningRefactored.py` the same way. Configuration is set inside `_make_config()` near the top of the file.

```bash
# Inside PowerFactory (default)
python "examples/000 - ManualCapacityScreeningRefactored.py"

# From an external terminal
python "examples/000 - ManualCapacityScreeningRefactored.py" external
python "examples/000 - ManualCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2024"
```

Results (Excel + log) are written to the `output/` folder.

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
