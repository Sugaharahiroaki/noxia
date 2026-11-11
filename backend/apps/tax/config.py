"""年度別税制設定の読み込み（data/year_YYYY.json → TaxYearConfiguration）。既存の行は更新しない（運営者が画面で更新する）。"""
from __future__ import annotations

import json
from pathlib import Path

from .models import TaxYearConfiguration

DATA_DIR = Path(__file__).resolve().parent / "data"


def available_years() -> list[int]:
    years = []
    for p in DATA_DIR.glob("year_*.json"):
        try:
            years.append(int(p.stem.split("_")[1]))
        except ValueError:
            continue
    return sorted(years)


def load_year_json(year: int) -> dict | None:
    p = DATA_DIR / f"year_{year}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def ensure_config(year: int, *, update: bool = False) -> TaxYearConfiguration | None:
    data = load_year_json(year)
    cfg = TaxYearConfiguration.objects.filter(year=year).first()
    if data is None:
        return cfg
    guide = json.loads((DATA_DIR / f"guide_{year}.json").read_text(encoding="utf-8")) if (DATA_DIR / f"guide_{year}.json").exists() else {}
    fields = {"status": data.get("status", "draft"), "income_tax": data["income_tax"], "surtaxes": data["surtaxes"], "deductions": data["deductions"],
              "statement_lines": data["statement_lines"], "guide": guide, "forms": data.get("forms", {}), "sources": data.get("sources", []),
              "note": data.get("note", "")}
    if cfg is None:
        cfg = TaxYearConfiguration.objects.create(year=year, **fields)
    elif update:
        for k, v in fields.items():
            setattr(cfg, k, v)
        cfg.save()
    return cfg
