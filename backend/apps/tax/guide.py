"""国税庁かんたん入力ガイド: 年度のガイド定義（データ）に計算値を当てて、作成コーナーの入力順に並べる。"""
from __future__ import annotations

import json
from pathlib import Path

from .models import GuideProgress, TaxReturn

DATA_DIR = Path(__file__).resolve().parent / "data"


def load_guide(year: int) -> dict:
    p = DATA_DIR / f"guide_{year}.json"
    if not p.exists():
        return {"year": year, "sections": [], "items": [], "links": [], "sources": [], "basis": "この年度のガイド定義はまだありません。"}
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve(path: str | None, ctx: dict):
    """ドット区切りのパスで値を引く。計算値のキー自体にドットが入るもの（例 business.income）は、残りのパス全体でも探す。"""
    if not path:
        return None
    parts = path.split(".")
    cur = ctx
    for i, part in enumerate(parts):
        if not isinstance(cur, dict):
            return None
        rest = ".".join(parts[i:])
        if rest in cur:
            return cur[rest]
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def build_guide(tr: TaxReturn, filing: str | None = None) -> dict:
    cfg_guide = tr.config.guide or {}
    defn = cfg_guide if cfg_guide.get("items") else load_guide(tr.config.year)
    filing = filing or ("blue" if tr.filing_type.startswith("blue") else "white")
    ctx = {"summary": tr.business_summary or {}, "computed": tr.computed or {}, "inputs": tr.inputs, "unsupported": tr.unsupported_items}
    # computed.deductions を key で引けるように
    if tr.computed:
        ctx["computed"] = {**tr.computed, "deductions": {d["key"]: d["amount"] for d in tr.computed.get("deductions", [])}}
    progress = {p.item_key: p for p in GuideProgress.objects.filter(tax_return=tr)}
    items = []
    for it in defn.get("items", []):
        if it.get("filing", "both") not in ("both", filing):
            continue
        val = _resolve(it.get("source"), ctx)
        if it["key"] == "unsupported":
            val = len(tr.unsupported_items or [])
        p = progress.get(it["key"])
        items.append({
            **it, "value": val, "official_label": it.get("official_label"), "verified": bool(it.get("verified", False)),
            "checked": bool(p and p.checked), "checked_at": p.checked_at if p else None, "copied_at": p.copied_at if p else None, "note": p.note if p else "",
        })
    total = len(items)
    done = sum(1 for i in items if i["checked"])
    return {
        "year": tr.config.year, "filing": filing, "basis": defn.get("basis", ""), "sources": defn.get("sources", []), "links": defn.get("links", []),
        "sections": defn.get("sections", []), "items": items, "progress": {"done": done, "total": total},
        "unsupported": tr.unsupported_items, "warnings": tr.warnings, "status": tr.status, "computed_at": (tr.computed or {}).get("computed_at"),
        "all_verified": all(i["verified"] for i in items) if items else False,
        "notice": "作成コーナーは他社ソフトのデータ（.xtx）を取り込めません。この画面の金額を見ながら入力してください。項目名が国税庁の画面と違うときは「要確認」の印の項目です。",
    }
