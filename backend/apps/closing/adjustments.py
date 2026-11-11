"""決算整理（手入力）: 未払金・未収金・前払・家事按分・貸倒など。借方・貸方の科目を選ぶと決算整理仕訳を作る。"""
from __future__ import annotations

from datetime import date

from django.db import transaction

from apps.accounting import books
from apps.accounting.models import Account, TaxPayer
from apps.core.exceptions import ApiError
from apps.core.money import D

from .models import AdjustmentKind, ClosingAdjustment

# 種類ごとの既定の科目（借方, 貸方）。画面の初期値に使い、利用者が変えられる
DEFAULT_ACCOUNTS = {
    "accrued_expense": ("misc_expense", "accrued_expenses"),
    "accrued_revenue": ("accrued_revenue", "sales"),
    "prepaid": ("prepaid_expenses", "rent"),
    "household_split": ("owner_out", "utilities"),
    "bad_debt": ("bad_debt", "customer_receivable"),
    "inventory": ("inventory", "closing_inventory"),
    "depreciation": ("depreciation", "equipment"),
    "opening_cost": ("opening_cost_amort", "deferred_opening_cost"),
    "other": ("misc_expense", "cash"),
}

KIND_HELP = {
    "accrued_expense": "今年中に使ったが支払いは翌年になる費用（12月分の電気代など）。翌年に支払ったときは未払金／預金で消す",
    "accrued_revenue": "今年の売上で入金が翌年のもの（12月のカード売上は売掛金で自動計上されるので不要）",
    "prepaid": "翌年分を今年中に払ったもの（来年1月の家賃など）。費用から外して前払費用にする",
    "household_split": "自宅兼店舗の家賃・光熱費などの家事分を費用から外す（事業主貸）",
    "bad_debt": "回収できないと確定したツケを貸倒金にする（要件は税理士に確認）",
    "inventory": "棚卸は「固定資産・棚卸」画面から確定すると自動で仕訳になる",
    "depreciation": "減価償却は「固定資産・棚卸」画面の計算で自動で仕訳になる",
    "opening_cost": "開業費の償却は固定資産（任意償却）から行う",
    "other": "その他の決算整理",
}


def _account(taxpayer: TaxPayer, key: str) -> Account:
    """事業者の科目を key で引く。標準科目なら無ければ作る。知らないキーはエラー（勝手に科目を増やさない）。"""
    acc = Account.objects.filter(taxpayer=taxpayer, key=key or "").first()
    if acc is not None:
        return acc
    if key in {a[0] for a in books.STANDARD_ACCOUNTS}:
        return books.account_for(taxpayer, key)
    raise ApiError("validation_error", "借方・貸方の科目を選んでください。")


@transaction.atomic
def create(taxpayer: TaxPayer, user, *, year: int, kind: str, description: str, amount, debit_key: str, credit_key: str, issue_date: date | None = None,
           store=None) -> ClosingAdjustment:
    if kind not in AdjustmentKind.values:
        raise ApiError("validation_error", "決算整理の種類を選んでください。")
    amt = D(amount)
    if amt <= 0:
        raise ApiError("validation_error", "金額は1円以上で入力してください。")
    if not (description or "").strip():
        raise ApiError("validation_error", "内容を入力してください。")
    fy = books.get_fiscal_year(taxpayer, year, create=True)
    if fy.is_locked:
        raise ApiError("year_locked", f"{year}年度は締め済みです。再開してから登録してください。", status=409)
    d = issue_date or fy.end_date
    if not (fy.start_date <= d <= fy.end_date):
        raise ApiError("validation_error", "日付は年度の範囲内で入力してください。")
    debit, credit = _account(taxpayer, debit_key), _account(taxpayer, credit_key)
    if debit.id == credit.id:
        raise ApiError("validation_error", "借方と貸方に同じ科目は選べません。")
    adj = ClosingAdjustment.objects.create(taxpayer=taxpayer, fiscal_year=fy, store=store, kind=kind, issue_date=d, description=description.strip()[:200],
                                           amount=amt, debit_account=debit, credit_account=credit, created_by=user)
    lines = [{"side": "debit", "key": debit.key, "amount": str(amt), "memo": adj.description[:100]},
             {"side": "credit", "key": credit.key, "amount": str(amt), "memo": adj.description[:100]}]
    adj.journal = books.create_manual_journal(taxpayer, user, issue_date=d, description=f"決算整理 {adj.get_kind_display()} {adj.description}"[:200], lines=lines,
                                              entry_kind="adjustment", store=store, source_type="closing_adj", source_id=str(adj.id))
    adj.save(update_fields=["journal", "updated_at"])
    return adj


@transaction.atomic
def void(adj: ClosingAdjustment, user, *, reason: str) -> ClosingAdjustment:
    adj = ClosingAdjustment.objects.select_for_update().get(pk=adj.pk)
    if adj.is_voided:
        raise ApiError("already_voided", "すでに取消済みです。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "取消理由を入力してください。")
    if adj.fiscal_year.is_locked:
        raise ApiError("year_locked", "締め済みの年度の決算整理は取り消せません。", status=409)
    if adj.journal_id and not adj.journal.is_void:
        books.void_manual_journal(adj.journal, user, reason=reason)
    adj.is_voided, adj.void_reason = True, reason.strip()[:200]
    adj.save(update_fields=["is_voided", "void_reason", "updated_at"])
    return adj


def payload(a: ClosingAdjustment) -> dict:
    return {
        "id": a.id, "year": a.fiscal_year.year, "kind": a.kind, "kind_label": a.get_kind_display(), "issue_date": a.issue_date, "description": a.description,
        "amount": str(a.amount), "debit_account": {"key": a.debit_account.key, "name": a.debit_account.name},
        "credit_account": {"key": a.credit_account.key, "name": a.credit_account.name}, "store": a.store_id, "store_name": a.store.name if a.store else "",
        "journal_id": a.journal_id, "is_voided": a.is_voided, "void_reason": a.void_reason, "created_at": a.created_at,
    }
