# Examples

This folder contains runnable scripts for N-1 hosting capacity screening using DIgSILENT PowerFactory. Both scripts screen the same network and produce the same results — they differ only in the underlying method and runtime cost.

## Prerequisites

1. **PowerFactory** must be installed and open with a project active
2. **Python packages**: `numpy`, `scipy`, `pandas`, `openpyxl`
3. The `admittance_matrix` package must be installed (`pip install -e .` from the repo root)

---

## Scripts

### `000 - EfficientCapacityScreeningRefactored.py` — Efficient method (PTDF/LODF)

Computes N-1 hosting capacity analytically using Power Transfer Distribution Factors (PTDFs) and Line Outage Distribution Factors (LODFs) derived from the network admittance matrix. A single DC network snapshot is extracted from PowerFactory; no repeated load flows are needed during the screen itself.

#### Configuration

Edit the constants and `_make_config()` near the top of the file — these are the only places you need to change anything:

```python
# Module-level constants
_PROJECT_NAME   = "Auto_Network_Model_2027"   # "" = use the currently active project
_INJECTION_SIGN = -1.0   # -1 = load headroom, +1 = generation headroom
_COUPLER_PASSES = True   # also test each open bus coupler in the closed position

def _make_config(project_root):
    return ScreeningConfig(
        s_base_mva             = 100.0,
        loading_limit_pct      = 90.0,    # branch loading limit (%)
        min_busbar_kv          = 132.0,   # ignore substations below this voltage (kV)
        min_sensitivity_mw     = 1e-3,
        target_substations     = [],      # e.g. ["ABHA", "LAGA"] — overrides max_substations
        max_substations        = 10,      # 0 = all; N = first N alphabetically
        output_prefix          = "Efficient_Refactored_Local_N1_Capacity_Screening",
    )
```

**Substation selection — two options (mutually exclusive):**

| Setting | Effect |
|---|---|
| `target_substations = ["ABHA", "LAGA"]` | Screen only those named substations |
| `target_substations = []` + `max_substations = 10` | Screen the first N alphabetically |
| `target_substations = []` + `max_substations = 0` | Screen all candidate substations |

#### Running

```bash
# From inside PowerFactory (Tools > Python > Run Script)
# — just open the file and click Execute

# From an external terminal (PowerFactory must be open)
python "000 - EfficientCapacityScreeningRefactored.py" external
python "000 - EfficientCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2025 SP4/Python/3.11"
```

---

### `000 - ManualCapacityScreeningRefactored.py` — Finite-difference method

Computes the same N-1 headroom numerically by running two DC load flows per contingency (one at 0 MW injection and one at a test increment). This serves as the reference implementation to validate the efficient method.

#### Configuration

Edit `_make_config()` near the top of the file. There are no module-level constants in this script — all settings are inside that function:

```python
_TEST_INCREMENT_MW = 50.0   # perturbation size for finite-difference sensitivity

def _make_config(project_root):
    return ScreeningConfig(
        s_base_mva             = 100.0,
        loading_limit_pct      = 90.0,
        min_busbar_kv          = 132.0,
        min_sensitivity_mw     = 1e-3,
        target_substations     = [],      # e.g. ["ABHA", "LAGA"] — overrides max_substations
        max_substations        = 10,      # 0 = all; N = first N alphabetically
        output_prefix          = "Manual_Refactored_Local_N1_Capacity_Screening",
    )
```

Substation selection works identically to the Efficient script (see table above).

#### Running

```bash
# From inside PowerFactory
python "000 - ManualCapacityScreeningRefactored.py"

# From an external terminal (PowerFactory must already be open)
python "000 - ManualCapacityScreeningRefactored.py" external
python "000 - ManualCapacityScreeningRefactored.py" external "C:/Program Files/DIgSILENT/PowerFactory 2024"
```

---

## Running a Side-by-Side Comparison

To verify that both methods agree:

1. Set the same `target_substations` list in both scripts (e.g. `["ABHA", "LAGA", "BEAT"]`).
2. Run the Efficient script, then the Manual script against the same active PowerFactory project.
3. Open the two `.xlsx` files side by side — the **Summary** sheet's capacity column should match within rounding.
4. Compare runtimes in the two `.txt` logs — the Efficient method is typically 10–100× faster for large screens.

| Criterion | Efficient | Manual |
|---|---|---|
| DC load flows required | 1 (snapshot only) | 2 per N-1 outage per substation |
| Speed | Fast | Slow for large networks |
| Accuracy | Exact DC | Matches Efficient to rounding |
| Coupler passes | Yes | Yes |
| Typical use | Production runs | Validating the Efficient method |

---

## Output

Both scripts write results to the `output/` folder in the repository root:

- **`.xlsx`** — Excel workbook with a **Summary** sheet (one row per substation) and a **Detail** sheet (one row per circuit pair evaluated)
- **`.txt`** — Plain-text log including the results table, timing breakdown, and any warnings

## Grid Models

The `grid_models/` subfolder contains PowerFactory `.pfd` files used for testing:

| File | Description |
|---|---|
| `Test_9_Bus.pfd` | Small 9-bus test network |
| `Radial System.pfd` | Simple radial network |
