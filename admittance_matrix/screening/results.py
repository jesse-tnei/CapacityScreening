from __future__ import annotations

import os

from .config import ScreeningConfig


def _write_xlsx(filename: str, df_sum, df_det) -> None:
    import pandas as pd  # noqa: F401

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        if not df_sum.empty:
            df_sum.to_excel(writer, sheet_name="Summary", index=False)
        if not df_det.empty:
            df_det.to_excel(writer, sheet_name="Detail", index=False)
        for ws in writer.sheets.values():
            for col in ws.columns:
                col_letter = col[0].column_letter
                width = max((len(str(c.value or "")) for c in col), default=0) + 2
                ws.column_dimensions[col_letter].width = min(width, 50)


def save_results_xlsx(
    filename: str,
    summary_rows: list[dict],
    detail_rows: list[dict],
    config: ScreeningConfig,
    app=None,
) -> str:
    """Write summary + detail sheets to Excel. Returns the path actually written.

    Falls back to config.fallback_output_folder if the primary folder does not
    exist or if writing raises PermissionError (typical when PowerFactory runs
    under Parallels and the relative output folder resolves to a non-writable
    location). Output folders are not auto-created — they must exist beforehand.
    """
    import pandas as pd

    df_sum = pd.DataFrame(summary_rows)
    df_det = pd.DataFrame(detail_rows)

    target_dir = os.path.dirname(filename) or "."
    if not os.path.isdir(target_dir):
        fallback = os.path.join(config.fallback_output_folder, os.path.basename(filename))
        msg = (
            f"Output folder '{target_dir}' does not exist. "
            f"Falling back to '{fallback}'"
        )
        if app is not None:
            app.PrintWarn(msg)
        else:
            print(msg)
        filename = fallback

    try:
        _write_xlsx(filename, df_sum, df_det)
        return filename
    except PermissionError as e:
        fallback = os.path.join(config.fallback_output_folder, os.path.basename(filename))
        if os.path.abspath(fallback) == os.path.abspath(filename):
            raise
        msg = (
            f"PermissionError writing to '{filename}': {e}. "
            f"Falling back to '{fallback}'"
        )
        if app is not None:
            app.PrintWarn(msg)
        else:
            print(msg)
        _write_xlsx(fallback, df_sum, df_det)
        return fallback
