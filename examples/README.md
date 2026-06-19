# Examples

This folder contains runnable scripts for N-1 hosting capacity screening using DIgSILENT PowerFactory. Both scripts screen the same network and produce comparable results — they differ only in the underlying method.

## Prerequisites

1. **PowerFactory** must be installed
2. **Python packages**: `numpy`, `scipy`, `pandas`, `openpyxl`
3. The `admittance_matrix` package must be installed (`pip install -e .` from the repo root)

## Scripts

### 000 - EfficientCapacityScreeningRefactored.py — *Efficient method (PTDF/LODF)*

Computes N-1 hosting capacity analytically using Power Transfer Distribution Factors (PTDFs) and Line Outage Distribution Factors (LODFs) derived from the network admittance matrix. A single DC network snapshot is extracted from PowerFactory; no repeated load flows are needed during the screen itself.

Edit the `CONFIG` block at the top of the script, then run:

```bash
# From inside PowerFactory (Tools > Python > Run Script)
# — no changes needed

# From an external terminal (PowerFactory must be open)
# Set RUN_EXTERNAL = True in the CONFIG block first, then:
python "000 - EfficientCapacityScreeningRefactored.py"
```

**Key config options:**

| Parameter | Description |
|---|---|
| `RUN_EXTERNAL` | `False` = run inside PF; `True` = run from terminal |
| `PROJECT_NAME` | PowerFactory project to activate (leave `""` for currently active) |
| `LOADING_LIMIT_PCT` | Branch loading limit for the N-1 check (%) |
| `MIN_BUSBAR_KV` | Ignore substations below this voltage level (kV) |
| `INJECTION_SIGN` | `-1` = load headroom, `+1` = generation headroom |
| `COUPLER_PASSES` | If `True`, repeat the screen with each open bus coupler closed |

---

### 000 - ManualCapacityScreeningRefactored.py — *Finite-difference method*

Computes the same N-1 headroom numerically by running two DC load flows per contingency (one at 0 MW injection and one at a small test increment). This serves as the reference implementation to validate the efficient method above.

```bash
# From inside PowerFactory
python "000 - ManualCapacityScreeningRefactored.py"

# From an external terminal (PowerFactory must already be open)
python "000 - ManualCapacityScreeningRefactored.py" external
python "000 - ManualCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2024"
```

Configuration is set inside `_make_config()` near the top of the file.

---

## Output

Both scripts write results to the `output/` folder in the repository root:

- **`.xlsx`** — Excel workbook with a summary sheet (one row per substation) and a detail sheet (one row per circuit pair evaluated)
- **`.txt`** — Plain-text log of the run, including timing and any warnings

## Grid Models

The `grid_models/` subfolder contains PowerFactory `.pfd` files used for testing:

| File | Description |
|---|---|
| `Test_9_Bus.pfd` | Small 9-bus test network |
| `Radial System.pfd` | Simple radial network |
