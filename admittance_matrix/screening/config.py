from dataclasses import dataclass


@dataclass
class ScreeningConfig:
    s_base_mva: float = 100.0
    loading_limit_pct: float = 90.0
    min_busbar_kv: float = 132.0
    min_sensitivity_mw: float = 1e-3
    max_substations: int = 0           # 0 = no limit
    output_folder: str = "output"
    fallback_output_folder: str = ""
    output_prefix: str = "Local_N1_Capacity_Screening"
