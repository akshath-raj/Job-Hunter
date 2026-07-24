"""Export jobs to an Excel workbook the user can browse and pick from."""

from __future__ import annotations

from pathlib import Path

from . import config
from .models import Job

_COLUMNS = [
    ("id", "ID", 16),
    ("title", "Title", 34),
    ("company", "Company", 24),
    ("location", "Location", 20),
    ("workplace_type", "Workplace", 12),
    ("about", "About the company", 44),
    ("salary", "Salary", 22),
    ("qualifications", "Qualifications", 44),
    ("status", "Status", 12),
    ("ineligible_reason", "Why skipped", 24),
    ("url", "Link", 40),
    ("enrichment_source", "Source", 24),
]


def to_excel(jobs: list[Job], path: str | Path | None = None) -> str:
    """Write jobs to an .xlsx and return the path."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    config.ensure_dirs()
    out = Path(path) if path else config.HOME / "jobs.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Jobs"

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(bold=True, color="FFFFFF")
    for col_idx, (_, header, width) in enumerate(_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.fill = header_fill
        cell.font = header_font
        ws.column_dimensions[cell.column_letter].width = width
    ws.freeze_panes = "A2"

    wrap = Alignment(wrap_text=True, vertical="top")
    for row_idx, job in enumerate(jobs, start=2):
        for col_idx, (attr, _, _) in enumerate(_COLUMNS, start=1):
            value = getattr(job, attr, None)
            value = value.value if hasattr(value, "value") else value  # StrEnum
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = wrap

    wb.save(out)
    return str(out)
