"""One-off profiler for the real data drop in /data. Writes data_profile.txt."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "data_profile.txt"


def detect_header(raw: pd.DataFrame) -> int:
    """Pick the row that looks most like a header (most short non-null strings)."""
    best, best_score = 0, -1
    for i in range(min(12, len(raw))):
        vals = raw.iloc[i].tolist()
        score = sum(
            1 for v in vals
            if isinstance(v, str) and 0 < len(v.strip()) <= 60
        )
        if score > best_score:
            best, best_score = i, score
    return best


def profile_sheet(xl: pd.ExcelFile, sheet: str, out: list[str]):
    raw = xl.parse(sheet, header=None, nrows=400)
    if raw.empty or raw.shape[1] == 0:
        out.append(f"  [{sheet}] EMPTY")
        return
    hdr = detect_header(raw)
    df = xl.parse(sheet, header=hdr)
    df = df.dropna(how="all").dropna(axis=1, how="all")
    cols = [str(c).replace("\n", " ").strip() for c in df.columns]
    out.append(f"  [{sheet}] header_row={hdr} rows={len(df)} cols={len(cols)}")
    out.append(f"    columns: {cols}")
    # sample first data row, truncated
    if len(df):
        row = {str(k).strip()[:24]: (str(v)[:28] if pd.notna(v) else "") for k, v in df.iloc[0].items()}
        out.append(f"    first row: {row}")


def main():
    out = []
    files = sorted(p for p in DATA_DIR.iterdir() if p.suffix.lower() in (".xlsx", ".xlsm"))
    for path in files:
        out.append(f"\n{'=' * 90}\nFILE: {path.name}")
        try:
            xl = pd.ExcelFile(path)
            out.append(f"sheets: {xl.sheet_names}")
            for sheet in xl.sheet_names:
                try:
                    profile_sheet(xl, sheet, out)
                except Exception as e:
                    out.append(f"  [{sheet}] ERROR: {e}")
        except Exception as e:
            out.append(f"  CANNOT OPEN: {e}")
    OUT.write_text("\n".join(out), encoding="utf-8")
    print(f"profile written: {OUT} ({len(out)} lines, {len(files)} files)")


if __name__ == "__main__":
    main()
