"""
帳簿の基盤: 勘定科目マスタ（事業者ごと）・仕訳行の正規化・会計年度（締め）・手入力仕訳。

- 標準科目（STANDARD_ACCOUNTS）の key は journals.ACCOUNTS のキーと同じ値で、既存の仕訳と連続する。
- statement_line は収支内訳書（一般用）・青色申告決算書（一般用）の行キー（tax_calculation_rules.md §4）。
- 帳簿（元帳・現金出納帳など）は JournalLine の絞り込みで作り、別テーブルを持たない（ledgers.py）。
- 締めた年度（FiscalYear.status=closed/filed）の仕訳は作成・変更・取消を拒否し、元データの変更は drift_warnings に残す。
"""
from __future__ import annotations

import calendar
import uuid
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO, tax_from_included

from .models import Account, AccountCategory, FiscalYear, FiscalYearEvent, FiscalYearStatus, Journal, JournalEntryKind, JournalLine, TaxPayer

# key, code, 個人事業主の名前, 法人の名前, category, statement_line, default_tax, flags
STANDARD_ACCOUNTS: list[tuple] = [
    ("cash", "101", "現金", "現金", "asset", "", "out_of_scope", {"is_cash": True}),
    ("bank", "102", "普通預金", "普通預金", "asset", "", "out_of_scope", {"is_bank": True}),
    ("card_receivable", "111", "売掛金（カード）", "売掛金（カード）", "asset", "", "out_of_scope", {}),
    ("customer_receivable", "112", "売掛金（ツケ）", "売掛金（ツケ）", "asset", "", "out_of_scope", {}),
    ("accrued_revenue", "113", "未収入金", "未収入金", "asset", "", "out_of_scope", {}),
    ("prepaid_expenses", "114", "前払費用", "前払費用", "asset", "", "out_of_scope", {}),
    ("inventory", "121", "商品（棚卸資産）", "商品（棚卸資産）", "asset", "", "out_of_scope", {}),
    ("equipment", "131", "器具備品", "器具備品", "asset", "", "out_of_scope", {}),
    ("deferred_opening_cost", "141", "開業費（繰延資産）", "創立費・開業費", "asset", "", "out_of_scope", {}),
    ("suspense_debit", "191", "仮払金", "仮払金", "asset", "", "out_of_scope", {}),
    ("accounts_payable", "201", "買掛金", "買掛金", "liability", "", "out_of_scope", {}),
    ("accrued_card", "202", "未払金", "未払金", "liability", "", "out_of_scope", {}),
    ("accrued_expenses", "203", "未払費用", "未払費用", "liability", "", "out_of_scope", {}),
    ("withholding_payable", "211", "預り金", "預り金", "liability", "", "out_of_scope", {}),
    ("suspense_credit", "291", "仮受金", "仮受金", "liability", "", "out_of_scope", {}),
    ("owner_in", "301", "事業主借", "役員借入金", "owner", "", "out_of_scope", {}),
    ("owner_out", "302", "事業主貸", "役員借入金（返済）", "owner", "", "out_of_scope", {}),
    ("opening_equity", "311", "元入金", "資本金", "equity", "", "out_of_scope", {}),
    ("sales", "401", "売上高", "売上高", "revenue", "sales", "sales_10", {}),
    ("household_consumption", "402", "家事消費", "家事消費", "revenue", "household_consumption", "sales_10", {}),
    ("other_income", "411", "雑収入", "雑収入", "revenue", "other_income", "out_of_scope", {}),
    ("other_deduction_income", "412", "雑収入（報酬からの控除）", "雑収入（報酬からの控除）", "revenue", "other_income", "out_of_scope", {}),
    ("cash_overage", "413", "雑収入（現金過剰）", "雑収入（現金過剰）", "revenue", "other_income", "out_of_scope", {}),
    ("opening_inventory", "501", "期首商品棚卸高", "期首商品棚卸高", "cogs", "opening_inventory", "out_of_scope", {}),
    ("purchases", "502", "仕入高", "仕入高", "cogs", "purchases", "purchase_10", {}),
    ("closing_inventory", "503", "期末商品棚卸高", "期末商品棚卸高", "cogs", "closing_inventory", "out_of_scope", {}),
    ("salary", "601", "給料賃金", "給料手当", "expense", "salaries", "out_of_scope", {}),
    ("cast_fee", "602", "支払報酬料（外注工賃）", "支払報酬料", "expense", "outsourcing", "out_of_scope", {}),
    ("outsourcing", "603", "外注工賃", "外注費", "expense", "outsourcing", "purchase_10", {}),
    ("depreciation", "604", "減価償却費", "減価償却費", "expense", "depreciation", "out_of_scope", {}),
    ("bad_debt", "605", "貸倒金", "貸倒損失", "expense", "bad_debt", "out_of_scope", {}),
    ("rent", "606", "地代家賃", "地代家賃", "expense", "rent", "purchase_10", {}),
    ("interest", "607", "利子割引料", "支払利息", "expense", "interest", "non_taxable", {}),
    ("taxes_dues", "611", "租税公課", "租税公課", "expense", "taxes_dues", "out_of_scope", {}),
    ("freight", "612", "荷造運賃", "荷造運賃", "expense", "freight", "purchase_10", {}),
    ("utilities", "613", "水道光熱費", "水道光熱費", "expense", "utilities", "purchase_10", {}),
    ("travel", "614", "旅費交通費", "旅費交通費", "expense", "travel", "purchase_10", {}),
    ("communication", "615", "通信費", "通信費", "expense", "communication", "purchase_10", {}),
    ("advertising", "616", "広告宣伝費", "広告宣伝費", "expense", "advertising", "purchase_10", {}),
    ("entertainment", "617", "接待交際費", "接待交際費", "expense", "entertainment", "purchase_10", {}),
    ("insurance", "618", "損害保険料", "保険料", "expense", "insurance", "non_taxable", {}),
    ("repair", "619", "修繕費", "修繕費", "expense", "repair", "purchase_10", {}),
    ("supplies", "620", "消耗品費", "消耗品費", "expense", "supplies", "purchase_10", {}),
    ("welfare", "621", "福利厚生費", "福利厚生費", "expense", "welfare", "purchase_10", {}),
    ("fees", "631", "支払手数料", "支払手数料", "expense", "custom", "purchase_10", {}),
    ("card_fee", "632", "支払手数料（決済手数料）", "支払手数料（決済手数料）", "expense", "custom", "non_taxable", {}),
    ("cash_shortage", "633", "雑損失（現金不足）", "雑損失（現金不足）", "expense", "custom", "out_of_scope", {}),
    ("opening_cost_amort", "634", "開業費償却", "開業費償却", "expense", "depreciation", "out_of_scope", {}),
    ("misc_expense", "699", "雑費", "雑費", "expense", "misc_expense", "purchase_10", {}),
]

STATEMENT_LINE_LABELS = {
    "sales": "売上（収入）金額", "household_consumption": "家事消費", "other_income": "その他の収入（雑収入）",
    "opening_inventory": "期首商品棚卸高", "purchases": "仕入金額", "closing_inventory": "期末商品棚卸高",
    "salaries": "給料賃金", "outsourcing": "外注工賃", "depreciation": "減価償却費", "bad_debt": "貸倒金", "rent": "地代家賃",
    "interest": "利子割引料", "taxes_dues": "租税公課", "freight": "荷造運賃", "utilities": "水道光熱費", "travel": "旅費交通費",
    "communication": "通信費", "advertising": "広告宣伝費", "entertainment": "接待交際費", "insurance": "損害保険料", "repair": "修繕費",
    "supplies": "消耗品費", "welfare": "福利厚生費", "custom": "その他の経費（任意の科目）", "misc_expense": "雑費",
}

TAX_CATEGORIES = {
    "sales_10": "課税売上10%", "sales_8": "課税売上8%（軽減）", "purchase_10": "課税仕入10%", "purchase_8": "課税仕入8%（軽減）",
    "non_taxable": "非課税", "out_of_scope": "対象外",
}
PL_CATEGORIES = (AccountCategory.REVENUE, AccountCategory.COGS, AccountCategory.EXPENSE)


def _rate_of(tax_category: str) -> Decimal | None:
    if tax_category in ("sales_10", "purchase_10"):
        return Decimal(10)
    if tax_category in ("sales_8", "purchase_8"):
        return Decimal(8)
    return None


# --- 勘定科目 ----------------------------------------------------------------------------
def ensure_accounts(taxpayer: TaxPayer) -> dict[str, Account]:
    """標準科目を無ければ作る（既存の科目は変えない）。戻り値は key → Account。"""
    existing = {a.key: a for a in Account.objects.filter(taxpayer=taxpayer)}
    is_corp = taxpayer.entity_type == "corporation"
    created = []
    for i, (key, code, name_sp, name_corp, category, line, tax, flags) in enumerate(STANDARD_ACCOUNTS):
        if key in existing:
            # 事業者の種類（法人／個人）が変わり、標準名のままの科目があれば名前を合わせる（利用者が変えた名前は触らない）
            acc = existing[key]
            want, other = (name_corp, name_sp) if is_corp else (name_sp, name_corp)
            if acc.is_system and acc.name == other and want != other:
                acc.name = want
                acc.save(update_fields=["name", "updated_at"])
            continue
        created.append(Account(
            taxpayer=taxpayer, key=key, code=code, name=name_corp if is_corp else name_sp, category=category, statement_line=line,
            default_tax=tax, is_cash=flags.get("is_cash", False), is_bank=flags.get("is_bank", False), is_system=True, sort_order=i * 10,
        ))
    if created:
        Account.objects.bulk_create(created, ignore_conflicts=True)
        existing = {a.key: a for a in Account.objects.filter(taxpayer=taxpayer)}
    return existing


def account_for(taxpayer: TaxPayer, key: str) -> Account:
    """key の科目を返す。無ければ標準科目を作る。標準にも無いキーは「任意科目（経費）」として作る（隠さず、名前は key）。"""
    acc = Account.objects.filter(taxpayer=taxpayer, key=key).first()
    if acc is not None:
        return acc
    accounts = ensure_accounts(taxpayer)
    if key in accounts:
        return accounts[key]
    return Account.objects.create(taxpayer=taxpayer, key=key, name=key, category=AccountCategory.EXPENSE, statement_line="custom",
                                  default_tax="purchase_10", is_system=False, sort_order=9000)


def account_by_key(store, key: str) -> Account | None:
    """店舗の事業者の科目（事業者が未設定なら None）。"""
    from .compensation import taxpayer_for

    tp = taxpayer_for(store)
    if tp is None:
        return None
    return account_for(tp, key)


def account_names(taxpayer: TaxPayer) -> dict[str, str]:
    return {a.key: a.name for a in ensure_accounts(taxpayer).values()}


def expense_account_keys() -> list[str]:
    return [a[0] for a in STANDARD_ACCOUNTS if a[4] in ("expense", "cogs")]


def account_payload(a: Account) -> dict:
    return {
        "id": a.id, "key": a.key, "code": a.code, "name": a.name, "category": a.category, "category_label": a.get_category_display(),
        "statement_line": a.statement_line, "statement_line_label": STATEMENT_LINE_LABELS.get(a.statement_line, ""),
        "default_tax": a.default_tax, "default_tax_label": TAX_CATEGORIES.get(a.default_tax, a.default_tax),
        "is_cash": a.is_cash, "is_bank": a.is_bank, "is_system": a.is_system, "is_active": a.is_active, "sort_order": a.sort_order, "note": a.note,
    }


# --- 会計年度 ---------------------------------------------------------------------------
def year_bounds(taxpayer: TaxPayer, year: int) -> tuple[date, date]:
    """年度 year（終了日の属する年）の開始日・終了日。個人事業主は暦年。法人は決算月で区切る。"""
    m = 12 if taxpayer.entity_type != "corporation" else int(taxpayer.fiscal_year_end_month or 12)
    end = date(year, m, calendar.monthrange(year, m)[1])
    if m == 12:
        return date(year, 1, 1), end
    start = date(year - 1, m + 1, 1)
    return start, end


def year_of(taxpayer: TaxPayer, d: date) -> int:
    m = 12 if taxpayer.entity_type != "corporation" else int(taxpayer.fiscal_year_end_month or 12)
    if m == 12 or d.month <= m:
        return d.year
    return d.year + 1


def get_fiscal_year(taxpayer: TaxPayer, year: int, *, create: bool = False) -> FiscalYear | None:
    fy = FiscalYear.objects.filter(taxpayer=taxpayer, year=year).first()
    if fy is None and create:
        start, end = year_bounds(taxpayer, year)
        filing = "white"
        prof = getattr(taxpayer, "individual_profile", None)
        if prof is not None and getattr(prof, "blue_approval_status", "") == "approved" and (prof.blue_approved_from_year or 9999) <= year:
            filing = "blue_10"
        fy, _ = FiscalYear.objects.get_or_create(taxpayer=taxpayer, year=year, defaults={"start_date": start, "end_date": end, "filing_type": filing})
    return fy


def fiscal_year_for_date(taxpayer: TaxPayer, d: date, *, create: bool = False) -> FiscalYear | None:
    return get_fiscal_year(taxpayer, year_of(taxpayer, d), create=create)


def locked_year(taxpayer: TaxPayer, d: date) -> FiscalYear | None:
    fy = fiscal_year_for_date(taxpayer, d)
    return fy if fy is not None and fy.is_locked else None


def record_drift(fy: FiscalYear, sync_key: str, message: str) -> None:
    """締めた年度の仕訳の元データが変わった記録（同じ sync_key は1件にまとめる）。"""
    warnings = [w for w in (fy.drift_warnings or []) if w.get("sync_key") != sync_key]
    warnings.append({"sync_key": sync_key, "detected_at": timezone.now().isoformat(), "message": message[:300]})
    fy.drift_warnings = warnings[-200:]
    fy.save(update_fields=["drift_warnings", "updated_at"])


@transaction.atomic
def close_year(fy: FiscalYear, user, *, reason: str = "") -> FiscalYear:
    fy = FiscalYear.objects.select_for_update().get(pk=fy.pk)
    if fy.is_locked:
        raise ApiError("already_closed", "この年度はすでに締め済みです。", status=409)
    fy.status = FiscalYearStatus.CLOSED
    fy.closed_at = timezone.now()
    fy.closed_by = user
    fy.save()
    FiscalYearEvent.objects.create(fiscal_year=fy, action="close", reason=reason[:300], performed_by=user)
    return fy


@transaction.atomic
def reopen_year(fy: FiscalYear, user, *, reason: str) -> FiscalYear:
    fy = FiscalYear.objects.select_for_update().get(pk=fy.pk)
    if not fy.is_locked:
        raise ApiError("not_closed", "締めていない年度は再開できません。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "再開の理由を入力してください。")
    fy.status = FiscalYearStatus.OPEN
    fy.reopen_count += 1
    fy.save()
    FiscalYearEvent.objects.create(fiscal_year=fy, action="reopen", reason=reason[:300], performed_by=user)
    return fy


def year_payload(fy: FiscalYear) -> dict:
    return {
        "id": fy.id, "year": fy.year, "start_date": fy.start_date, "end_date": fy.end_date, "filing_type": fy.filing_type, "status": fy.status,
        "status_label": fy.get_status_display(), "closed_at": fy.closed_at, "filed_at": fy.filed_at, "reopen_count": fy.reopen_count,
        "drift_warnings": fy.drift_warnings, "note": fy.note, "is_locked": fy.is_locked,
        "events": [{"action": e.action, "reason": e.reason, "by": str(e.performed_by) if e.performed_by else "", "at": e.performed_at} for e in fy.events.all()[:20]],
    }


# --- 仕訳行の正規化 ----------------------------------------------------------------------
def sync_lines(journal: Journal) -> None:
    """journal.lines（JSON）から JournalLine を作り直す。行の store は指定が無ければ仕訳の店舗。"""
    tp = journal.taxpayer
    accounts = ensure_accounts(tp)
    JournalLine.objects.filter(journal=journal).delete()
    rows = []
    for i, ln in enumerate(journal.lines or [], 1):
        key = ln.get("key") or "suspense_debit"
        acc = accounts.get(key) or account_for(tp, key)
        tax = ln.get("tax") or acc.default_tax or "out_of_scope"
        amount = D(ln.get("amount") or 0)
        rate = _rate_of(tax)
        rows.append(JournalLine(
            journal=journal, taxpayer=tp, date=journal.issue_date, line_no=i, side=ln.get("side") or "debit", account=acc, amount=amount,
            tax_category=tax, tax_amount=tax_from_included(amount, rate) if rate else None,
            store_id=ln.get("store_id") or journal.store_id, employee_id=ln.get("employee_id"), bank_account_ref=ln.get("bank_account_id"),
            counterparty=(ln.get("counterparty") or "")[:100], memo=(ln.get("memo") or "")[:120],
        ))
    if rows:
        JournalLine.objects.bulk_create(rows)


def validate_lines(lines: list[dict]) -> tuple[Decimal, Decimal]:
    """手入力仕訳の検証（貸借一致・正の金額・キーあり）。戻り値は借方合計・貸方合計。"""
    if not lines:
        raise ApiError("validation_error", "仕訳の行がありません。")
    dr = cr = ZERO
    for ln in lines:
        if ln.get("side") not in ("debit", "credit"):
            raise ApiError("validation_error", "各行は借方（debit）か貸方（credit）を指定してください。")
        if not ln.get("key"):
            raise ApiError("validation_error", "各行の勘定科目を選んでください。")
        amt = D(ln.get("amount") or 0)
        if amt <= 0:
            raise ApiError("validation_error", "金額は1円以上で入力してください。")
        if ln.get("tax") and ln["tax"] not in TAX_CATEGORIES:
            raise ApiError("validation_error", "税区分が不正です。")
        if ln["side"] == "debit":
            dr += amt
        else:
            cr += amt
    if dr != cr:
        raise ApiError("unbalanced", f"借方合計 {dr:,.0f}円 と貸方合計 {cr:,.0f}円 が一致しません。", status=400, details={"debit": str(dr), "credit": str(cr)})
    return dr, cr


def _norm_lines(taxpayer: TaxPayer, lines: list[dict]) -> list[dict]:
    out = []
    for ln in lines:
        acc = account_for(taxpayer, ln["key"])
        out.append({
            "side": ln["side"], "key": acc.key, "amount": str(D(ln["amount"])), "tax": ln.get("tax") or acc.default_tax or "out_of_scope",
            "memo": (ln.get("memo") or "")[:100], "store_id": ln.get("store_id"), "counterparty": (ln.get("counterparty") or "")[:100],
            "employee_id": ln.get("employee_id"), "bank_account_id": ln.get("bank_account_id"),
        })
    return out


@transaction.atomic
def create_manual_journal(taxpayer: TaxPayer, user, *, issue_date: date, description: str, lines: list[dict], entry_kind: str = "manual",
                          store=None, source_type: str | None = None, source_id: str | None = None) -> Journal:
    """手入力・決算整理の仕訳。貸借が合わなければ作らない（仮勘定で埋めない）。締めた年度には作れない。"""
    from . import journals

    if entry_kind not in JournalEntryKind.values:
        raise ApiError("validation_error", "仕訳の種類が不正です。")
    validate_lines(lines)
    if locked_year(taxpayer, issue_date) is not None:
        raise ApiError("year_locked", f"{year_of(taxpayer, issue_date)}年度は締め済みのため仕訳を追加できません。再開してから登録してください。", status=409)
    norm = _norm_lines(taxpayer, lines)
    stype = source_type or entry_kind
    sid = source_id or uuid.uuid4().hex[:20]
    j = journals.upsert(taxpayer, store=store, source_type=stype, source_id=sid, issue_date=issue_date, description=(description or "")[:200],
                        lines=norm, entry_kind=entry_kind, created_by=user)
    return j


@transaction.atomic
def update_manual_journal(journal: Journal, user, *, issue_date: date | None = None, description: str | None = None, lines: list[dict] | None = None) -> Journal:
    from . import journals

    if journal.entry_kind == JournalEntryKind.AUTO:
        raise ApiError("auto_journal", "自動で作った仕訳は直接変更できません。元の記録を直してから「仕訳を作る」を実行してください。", status=409)
    if journal.is_void:
        raise ApiError("voided", "取消済みの仕訳は変更できません。", status=409)
    new_date = issue_date or journal.issue_date
    for d in {journal.issue_date, new_date}:
        if locked_year(journal.taxpayer, d) is not None:
            raise ApiError("year_locked", "締め済みの年度の仕訳は変更できません。", status=409)
    new_lines = _norm_lines(journal.taxpayer, lines) if lines is not None else journal.lines
    if lines is not None:
        validate_lines(lines)
    return journals.upsert(journal.taxpayer, store=journal.store, source_type=journal.source_type, source_id=journal.source_id, issue_date=new_date,
                           description=(description if description is not None else journal.description)[:200], lines=new_lines,
                           entry_kind=journal.entry_kind, created_by=user)


@transaction.atomic
def void_manual_journal(journal: Journal, user, *, reason: str) -> Journal:
    from . import journals

    if journal.entry_kind == JournalEntryKind.AUTO:
        raise ApiError("auto_journal", "自動で作った仕訳は取り消せません。元の記録を取り消してください。", status=409)
    if locked_year(journal.taxpayer, journal.issue_date) is not None:
        raise ApiError("year_locked", "締め済みの年度の仕訳は取り消せません。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "取消理由を入力してください。")
    j = journals.upsert(journal.taxpayer, store=journal.store, source_type=journal.source_type, source_id=journal.source_id, issue_date=journal.issue_date,
                        description=journal.description, lines=journal.lines, warnings=[f"取消: {reason[:100]}"], void=True, entry_kind=journal.entry_kind,
                        created_by=user)
    return j
