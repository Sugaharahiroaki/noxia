"""CSV / XLSX の出力部品。金額セルは数値、数式は書かない。先頭の =+-@ は無害化する（CSV インジェクション対策）。"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse

from apps.core.audit import audit

_DANGEROUS = ("=", "+", "-", "@")


def safe_text(v):
    if isinstance(v, str) and v[:1] in _DANGEROUS and not _looks_numeric(v):
        return " " + v
    return v


def _looks_numeric(s: str) -> bool:
    try:
        Decimal(s.replace(",", ""))
        return True
    except Exception:
        return False


def _cell(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return safe_text(v)


def csv_bytes(header: list[str], rows, *, encoding: str = "utf-8-sig") -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(header)
    for r in rows:
        w.writerow([_cell(c) if not isinstance(c, (int, float)) else c for c in r])
    data = buf.getvalue()
    if encoding == "cp932":
        return data.encode("cp932", errors="replace")
    return data.encode("utf-8-sig")


def xlsx_bytes(sheets: list[tuple[str, list[str], list]]) -> bytes:
    """sheets: [(シート名, 見出し, 行)]。金額は数値セル、日付は文字列。"""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    first = True
    for name, header, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        first = False
        ws.title = (name or "Sheet")[:31]
        ws.append(header)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([_cell(c) for c in r])
        for i, h in enumerate(header, 1):
            width = max([len(str(h))] + [len(str(row[i - 1])) for row in rows[:200] if i - 1 < len(row)]) if rows else len(str(h))
            ws.column_dimensions[get_column_letter(i)].width = min(max(width * 1.6, 8), 50)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                if isinstance(c.value, (int, float)):
                    c.number_format = "#,##0"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def csv_response(request, filename: str, header: list[str], rows, action: str, *, encoding: str = "utf-8-sig") -> HttpResponse:
    resp = HttpResponse(csv_bytes(header, rows, encoding=encoding), content_type=f"text/csv; charset={'shift_jis' if encoding == 'cp932' else 'utf-8'}")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["X-Content-Type-Options"] = "nosniff"
    audit(request, action, changes={"filename": filename})
    return resp


def xlsx_response(request, filename: str, sheets: list[tuple[str, list[str], list]], action: str) -> HttpResponse:
    resp = HttpResponse(xlsx_bytes(sheets), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp["X-Content-Type-Options"] = "nosniff"
    audit(request, action, changes={"filename": filename})
    return resp


def file_response(request, filename: str, content: bytes, content_type: str, action: str, *, inline: bool = False) -> HttpResponse:
    resp = HttpResponse(content, content_type=content_type)
    resp["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    audit(request, action, changes={"filename": filename})
    return resp
