from __future__ import annotations

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import splu

from .models import NetworkSnapshot


class PTDFLODFEngine:
    """Builds B', factors it once, returns PTDF and LODF columns on demand.

    Sign convention: positive injection at a bus = +1 MW generation
    (equivalently, -1 MW load).
    """

    def __init__(self, snapshot: NetworkSnapshot) -> None:
        self.snap = snapshot
        self._build_b_prime()
        self._factor()
        self._psi_cache: dict[int, np.ndarray] = {}

    def _build_b_prime(self) -> None:
        n_bus = len(self.snap.buses)
        rows, cols, data = [], [], []
        for br in self.snap.branches:
            if not br.in_service:
                continue
            b = 1.0 / br.x_pu
            i, j = br.from_bus, br.to_bus
            rows += [i, j, i, j]
            cols += [i, j, j, i]
            data += [b, b, -b, -b]
        B = sp.csr_matrix((data, (rows, cols)), shape=(n_bus, n_bus))
        keep = np.ones(n_bus, dtype=bool)
        keep[self.snap.slack_bus_idx] = False
        self._keep_mask = keep
        self._B_red = B[keep][:, keep].tocsc()
        self._n_bus = n_bus

    def _factor(self) -> None:
        self._lu = splu(self._B_red)

    def ptdf_column(self, bus_idx: int) -> np.ndarray:
        """Sensitivity of every branch flow (MW) to +1 MW net injection at bus_idx."""
        if bus_idx in self._psi_cache:
            return self._psi_cache[bus_idx]

        if bus_idx == self.snap.slack_bus_idx:
            col = np.zeros(len(self.snap.branches))
            self._psi_cache[bus_idx] = col
            return col

        rhs = np.zeros(self._n_bus)
        rhs[bus_idx] = 1.0
        rhs_red = rhs[self._keep_mask]
        theta_red = self._lu.solve(rhs_red)
        theta = np.zeros(self._n_bus)
        theta[self._keep_mask] = theta_red

        col = np.zeros(len(self.snap.branches))
        for br in self.snap.branches:
            if not br.in_service:
                continue
            col[br.idx] = (theta[br.from_bus] - theta[br.to_bus]) / br.x_pu
        self._psi_cache[bus_idx] = col
        return col

    def lodf_columns(self, outage_idx: list[int]) -> np.ndarray:
        """LODF[:, k] for k in outage_idx. Shape (n_branches, n_outages).

        NaN-flagged if denominator ~0 (outage causes islanding).
        """
        n_br = len(self.snap.branches)
        out = np.zeros((n_br, len(outage_idx)))
        for jj, k in enumerate(outage_idx):
            cbr = self.snap.branches[k]
            psi_from = self.ptdf_column(cbr.from_bus)
            psi_to = self.ptdf_column(cbr.to_bus)
            tdf = psi_from - psi_to
            denom = 1.0 - tdf[k]
            if abs(denom) < 1e-9:
                out[:, jj] = np.nan
            else:
                out[:, jj] = tdf / denom
        return out
