import os
import time
from pathlib import Path
import pandas as pd


class DatasetExporter:
    """Exports mined and enriched product datasets to CSV."""

    @staticmethod
    def export_csv(df: pd.DataFrame, domain: str, output_dir: str = "data") -> str:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        clean_domain = domain.replace(".", "_").replace(":", "_")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        filename = f"{clean_domain}_products_{timestamp}.csv"
        file_path = out_path / filename

        export_df = df.copy()

        # Flatten or stringify complex columns for CSV export
        if "specifications" in export_df.columns:
            export_df["specifications"] = export_df["specifications"].apply(lambda x: str(x) if x else "")
        if "images" in export_df.columns:
            export_df["images"] = export_df["images"].apply(lambda x: ",".join(x) if isinstance(x, list) else str(x or ""))

        export_df.to_csv(file_path, index=False, encoding="utf-8-sig")
        print(f"[EXPORT] Dataset exported to CSV: {file_path} ({len(export_df)} rows, {len(export_df.columns)} columns)")
        return str(file_path.resolve())
