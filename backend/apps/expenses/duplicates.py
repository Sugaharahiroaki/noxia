"""
領収書の重複検出。候補を付けるだけで登録は止めない（承認時に理由なしでは承認できない）。

- 同一ファイル: Evidence.sha256 が一致
- 類似画像: 知覚ハッシュ（dHash 64bit）のハミング距離 ≤ 6
- 同じ取引: 日付 ±3 日・金額一致・支払先の類似度 ≥ 0.85
- 既存のレジ出金: 同じ店舗・営業日に金額一致の現金経費（領収書なし）→ 結合候補（重複ではない）
"""
from __future__ import annotations

import re
import unicodedata
from datetime import timedelta
from difflib import SequenceMatcher

from apps.accounting.uploads import hamming

PHASH_THRESHOLD = 6
_CORP = re.compile(r"(株式会社|有限会社|合同会社|\(株\)|（株）|\(有\)|（有）|カ\)|ｶ\)|ユ\)|ﾕ\)|ふりこみ|ﾌﾘｼﾞ|振込)")


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _CORP.sub("", s)
    s = re.sub(r"[\s　・･\-－—‐,.、。()（）\[\]「」『』]", "", s)
    # カタカナ → ひらがな
    s = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)
    return s.lower()


def name_similarity(a: str, b: str) -> float:
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.9
    return SequenceMatcher(None, na, nb).ratio()


def find_candidates(receipt) -> list[dict]:
    """同じ会社の他の領収書との重複候補。"""
    from .models import ExpenseReceipt

    ev = receipt.evidence
    qs = ExpenseReceipt.objects.filter(company=receipt.company).exclude(id=receipt.id).exclude(status__in=["voided", "rejected"]).select_related("evidence")
    date = receipt.effective_date
    amount = receipt.effective_amount
    vendor = receipt.effective_vendor
    out: dict[int, dict] = {}

    def add(other, reason, **extra):
        c = out.setdefault(other.id, {"receipt_id": other.id, "reasons": [], "status": other.status, "date": other.effective_date.isoformat() if other.effective_date else None,
                                     "amount": str(other.effective_amount) if other.effective_amount is not None else None, "vendor": other.effective_vendor,
                                     "store_id": other.store_id})
        if reason not in c["reasons"]:
            c["reasons"].append(reason)
        c.update(extra)

    for other in qs.filter(evidence__sha256=ev.sha256):
        add(other, "sha256")
    if ev.phash:
        for other in qs.exclude(evidence__phash="").only("id", "status", "evidence", "store_id", "approved_date", "receipt_date", "ocr_date", "approved_amount",
                                                          "total_amount", "ocr_total", "approved_vendor", "vendor", "ocr_vendor"):
            d = hamming(ev.phash, other.evidence.phash)
            if d <= PHASH_THRESHOLD:
                add(other, "phash", distance=d)
    if date and amount is not None:
        lo, hi = date - timedelta(days=3), date + timedelta(days=3)
        for other in qs:
            od, oa = other.effective_date, other.effective_amount
            if od is None or oa is None or oa != amount or not (lo <= od <= hi):
                continue
            ov = other.effective_vendor
            if not vendor or not ov or name_similarity(vendor, ov) >= 0.85:
                add(other, "date_amount_vendor", similarity=round(name_similarity(vendor, ov), 2) if vendor and ov else None)
    return sorted(out.values(), key=lambda c: -len(c["reasons"]))


def cash_movement_candidates(receipt) -> list[dict]:
    """店舗現金の領収書に対して、同じ店舗・営業日の「領収書なしの現金経費」出金を結合候補として出す。"""
    from apps.cash.models import CashMovement

    if receipt.payment_method != "store_cash":
        return []
    d = receipt.effective_date
    amount = receipt.effective_amount
    if d is None:
        return []
    qs = CashMovement.objects.filter(store=receipt.store, kind="expense", is_voided=False, business_day__date=d, expense_receipt__isnull=True)
    rows = []
    for mv in qs.order_by("occurred_at"):
        rows.append({"cash_movement_id": mv.id, "amount": str(mv.amount), "reason": mv.reason, "occurred_at": mv.occurred_at.isoformat(),
                     "amount_match": amount is not None and mv.amount == amount, "business_day_status": mv.business_day.status})
    rows.sort(key=lambda r: (not r["amount_match"], r["occurred_at"]))
    return rows
