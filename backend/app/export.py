"""
Excel export of the Book of Business (upcoming renewals).

Builds an .xlsx whose columns mirror the dashboard table 1:1, with the same
tier colours and the same honesty rule: a genuinely-missing value is left BLANK,
never zero-filled or guessed.
"""

from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# (header, group-dict key, kind, column width). kind drives the cell value/format.
COLUMNS = [
    ("Group", "group_name", "text", 34),
    ("Product Line", "line_of_business", "text", 17),
    ("Renewal Date", "renewal_date", "date", 13),
    ("Renewal Likelihood", "renewal_probability", "pct", 16),
    ("Tier", "risk_tier", "tier", 10),
    ("Enrolled Lives", "group_size", "int", 12),
    ("Annual Premium", "annual_premium", "money", 15),
    ("Expected Lost Premium", "premium_at_risk", "money", 18),
    ("Loss Ratio", "loss_ratio", "pct", 11),
    ("Corridor", "corridor", "pct", 11),
    ("Renewal Increase", "rate_increase_pct", "pct_whole", 14),
    ("Tenure (yrs)", "tenure_years", "int", 11),
    ("State", "state", "text", 8),
    ("Broker", "broker_name", "text", 26),
    ("Account Manager", "am", "text", 18),
    ("RSD", "rsd", "text", 14),
    ("Carrier", "carrier", "text", 18),
    ("TPA", "tpa", "text", 18),
    ("Recent BOR", "recent_bor", "yesno", 11),
    ("Laser at Renewal", "laser_at_renewal", "yesno", 14),
    ("Data Status", "data_basis", "status", 14),
]

# tier -> (font hex, fill hex) matching the dashboard badges
TIER_STYLE = {
    "Secure": ("0f7b54", "d1fae5"),
    "Stable": ("0369a1", "e0f2fe"),
    "Watch": ("92681a", "fef3c7"),
    "Concern": ("c2410c", "fdead3"),
    "Critical": ("b42318", "fee2e2"),
}

_BLANK = {None, "", "—", "Unknown", "n/a"}


def _cell_value(g: dict, key: str, kind: str):
    """Display value for one cell — blank (None) when genuinely missing."""
    v = g.get(key)
    if kind in ("text",):
        return None if v in _BLANK else v
    if kind == "tier":
        return v
    if kind == "status":
        return "Complete" if v != "estimate" else "Needs data"
    if kind == "yesno":
        return None if v is None else ("Yes" if v else "No")
    if v is None:
        return None
    if kind == "date":
        return v  # ISO string; formatted as text, already YYYY-MM-DD
    if kind == "pct":
        return float(v)            # 0.41 -> shown 41% via number format
    if kind == "pct_whole":
        return float(v) / 100.0    # 24.0 (percent units) -> 0.24 -> 24%
    if kind == "money":
        return float(v)
    if kind == "int":
        return int(v)
    return v


_FMT = {"pct": "0%", "pct_whole": "0%", "money": "$#,##0", "int": "#,##0"}


def build_book_xlsx(groups: list[dict], as_of: str | None = None) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Upcoming Renewals"

    ncols = len(COLUMNS)
    thin = Side(style="thin", color="E2E8F0")
    border = Border(bottom=thin)

    # --- title band
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    t = ws.cell(row=1, column=1, value="Horizon — Upcoming Renewals (Book of Business)")
    t.font = Font(bold=True, size=14, color="0F172A")
    sub = (f"As of {as_of}  ·  {len(groups)} renewals in the next two quarters  ·  "
           f"exported {datetime.now():%Y-%m-%d %H:%M}")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    s = ws.cell(row=2, column=1, value=sub)
    s.font = Font(size=10, italic=True, color="64748B")

    # --- header row
    hdr_row = 4
    header_fill = PatternFill("solid", fgColor="0F172A")
    for c, (label, _key, _kind, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=hdr_row, column=c, value=label)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = width

    # --- data rows, sorted by renewal likelihood (lowest first = highest risk)
    rows = sorted(groups, key=lambda g: g.get("renewal_probability", 1))
    for r, g in enumerate(rows, start=hdr_row + 1):
        for c, (_label, key, kind, _w) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=r, column=c, value=_cell_value(g, key, kind))
            cell.border = border
            cell.font = Font(size=10)
            if kind in _FMT:
                cell.number_format = _FMT[kind]
                cell.alignment = Alignment(horizontal="right")
            elif kind in ("tier", "status", "yesno", "date"):
                cell.alignment = Alignment(horizontal="center")
            if kind == "tier" and g.get("risk_tier") in TIER_STYLE:
                fg, bg = TIER_STYLE[g["risk_tier"]]
                cell.font = Font(size=10, bold=True, color=fg)
                cell.fill = PatternFill("solid", fgColor=bg)
            if kind == "status" and g.get("data_basis") == "estimate":
                cell.font = Font(size=10, italic=True, color="92681a")

    ws.freeze_panes = ws.cell(row=hdr_row + 1, column=2)  # freeze header + Group col
    ws.auto_filter.ref = f"A{hdr_row}:{get_column_letter(ncols)}{hdr_row + len(rows)}"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
