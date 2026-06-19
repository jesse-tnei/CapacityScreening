import sys
import os
sys.path.insert(0, r"C:\Program Files\DIgSILENT\PowerFactory 2025 SP4\Python\3.11")

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import powerfactory as pf
import numpy as np
import pandas as pd
from admittance_matrix import Network
from admittance_matrix.utils import init_project

# Connect to the running PowerFactory instance
app = pf.GetApplicationExt()
app.Show()

# Activate the project
init_project(app, r"Test_9_Bus")

# Build network and admittance matrices
net = Network(app, base_mva=100.0)

# Run load flow
success = net.run_load_flow()
print("Load flow converged:", success)

# Print matrix shapes
print("Y_lf shape:      ", net.Y_lf_matrix.shape)
print("Y_stab shape:    ", net.Y_stab_matrix.shape)
print("Y_reduced shape: ", net.Y_reduced_matrix.shape)

# Print detected sources
print("Sources:", net.source_names)

# Build power distribution ratios table (rows = tripped generator, columns = receiving generators)
rows = {}
for gen in net.source_names:
    ratios, names, types = net.calculate_power_ratios(gen, MODE=1)
    rows[gen] = {name: round(ratio * 100, 2) for name, ratio in zip(names, ratios)}

df_ratios = pd.DataFrame(rows).T
df_ratios.index.name = "Tripped Generator"
df_ratios.columns.name = "Receiving Generator"

output_path = os.path.join(_PROJECT_ROOT, "output", "power_distribution_ratios.xlsx")
with pd.ExcelWriter(output_path, engine="openpyxl") as writer:

    # Full Y-matrices (real and imaginary parts separately)
    for sheet_name, matrix in [("Y_lf", net.Y_lf_matrix), ("Y_stab", net.Y_stab_matrix)]:
        pd.DataFrame(np.real(matrix), index=net.bus_names, columns=net.bus_names).to_excel(writer, sheet_name=f"{sheet_name}_real")
        pd.DataFrame(np.imag(matrix), index=net.bus_names, columns=net.bus_names).to_excel(writer, sheet_name=f"{sheet_name}_imag")

    # Reduced Y-matrix
    pd.DataFrame(np.real(net.Y_reduced_matrix), index=net.source_names, columns=net.source_names).to_excel(writer, sheet_name="Y_reduced_real")
    pd.DataFrame(np.imag(net.Y_reduced_matrix), index=net.source_names, columns=net.source_names).to_excel(writer, sheet_name="Y_reduced_imag")

    # Power distribution ratios
    df_ratios.to_excel(writer, sheet_name="Distribution Ratios")

print(f"\nSaved to {output_path}")
print(df_ratios)

