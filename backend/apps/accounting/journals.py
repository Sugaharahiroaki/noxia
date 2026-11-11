"""
NOXIA の記録から仕訳を作る（freee への連携・仕訳CSVの元データ）。

- 1つの元データ（締めた営業日・銀行入金・納付・経費・資金移動・カード入金・振込の支払い）から1件の仕訳。
  sync_key = 事業者:種類:ID で一意にし、何度作り直しても二重にならない。
- 元データが変わった・取り消された場合は、同じ仕訳の内容が変わる（revision が上がる）／is_void になる。
- 店舗の日次仕訳は「締め済み」の営業日だけ作る（締め前は数字が動くため）。
- 経営者の個人資金の投入・引出しは売上・経費にせず、個人事業主は事業主借／事業主貸、法人は役員借入金で仕訳する。
- 借方と貸方が合わない場合は仮勘定を入れて warnings に理由を残す（自動で辻褄を合わせて隠さない）。
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q

from apps.core.business_date import business_date_for, store_tz
from apps.core.money import D, ZERO

from .models import (
    AccountMapping, CardSettlement, EntityType, Expense, FreeeSync, FreeeSyncStatus, Journal, OwnerFundTransfer, StoreTaxProfile,
    TaxPayer, TaxRemittance, WithholdingRecord,
)

# 科目キー → (法人の既定名, 個人事業主の既定名)
ACCOUNTS: dict[str, tuple[str, str]] = {
    "cash": ("現金", "現金"),
    "bank": ("普通預金", "普通預金"),
    "card_receivable": ("売掛金（カード）", "売掛金（カード）"),
    "customer_receivable": ("売掛金（ツケ）", "売掛金（ツケ）"),
    "sales": ("売上高", "売上高"),
    "cast_fee": ("支払報酬料", "支払報酬料"),
    "salary": ("給料手当", "給料賃金"),
    "withholding_payable": ("預り金", "預り金"),
    "other_deduction_income": ("雑収入", "雑収入"),
    "card_fee": ("支払手数料", "支払手数料"),
    "owner_in": ("役員借入金", "事業主借"),
    "owner_out": ("役員借入金", "事業主貸"),
    "accrued_card": ("未払金", "未払金"),
    "accounts_payable": ("買掛金", "買掛金"),
    "cash_shortage": ("雑損失", "雑損失"),
    "cash_overage": ("雑収入", "雑収入"),
    "suspense_debit": ("仮払金", "仮払金"),
    "suspense_credit": ("仮受金", "仮受金"),
    # 経費
    "purchases": ("仕入高", "仕入高"),
    "rent": ("地代家賃", "地代家賃"),
    "utilities": ("水道光熱費", "水道光熱費"),
    "supplies": ("消耗品費", "消耗品費"),
    "advertising": ("広告宣伝費", "広告宣伝費"),
    "communication": ("通信費", "通信費"),
    "entertainment": ("接待交際費", "接待交際費"),
    "travel": ("旅費交通費", "旅費交通費"),
    "repair": ("修繕費", "修繕費"),
    "fees": ("支払手数料", "支払手数料"),
    "welfare": ("福利厚生費", "福利厚生費"),
    "misc_expense": ("雑費", "雑費"),
}
# 経費として登録できる科目キー（勘定科目マスタの費用・売上原価の標準科目）
from .books import STANDARD_ACCOUNTS as _STD  # noqa: E402

EXPENSE_KEYS = [a[0] for a in _STD if a[4] in ("expense", "cogs")]

# 税区分キー → 表示名（freee の税区分コードは事業所ごとに対応付けで設定する）
TAXES: dict[str, str] = {
    "sales_10": "課税売上10%",
    "sales_8": "課税売上8%（軽減）",
    "purchase_10": "課税仕入10%",
    "purchase_8": "課税仕入8%（軽減）",
    "non_taxable": "非課税",
    "out_of_scope": "対象外",
}


def account_names(taxpayer: TaxPayer) -> dict[str, str]:
    """科目キー → 表示名。勘定科目マスタ（books.Account）の名前を使い、freee 用の AccountMapping で上書きできる。"""
    from . import books

    idx = 0 if taxpayer.entity_type == EntityType.CORPORATION else 1
    names = {k: v[idx] for k, v in ACCOUNTS.items()}
    names.update(books.account_names(taxpayer))
    for m in AccountMapping.objects.filter(taxpayer=taxpayer).exclude(key__startswith="tax:"):
        if m.account_name:
            names[m.key] = m.account_name
    return names


def _line(side: str, key: str, amount, tax: str = "out_of_scope", memo: str = "", **extra) -> dict:
    """仕訳行。extra: store_id / employee_id / counterparty / bank_account_id（帳簿の補助項目）。"""
    ln = {"side": side, "key": key, "amount": str(D(amount)), "tax": tax, "memo": memo[:100]}
    for k in ("store_id", "employee_id", "counterparty", "bank_account_id"):
        if extra.get(k) not in (None, ""):
            ln[k] = extra[k]
    return ln


def _account_tax(taxpayer: TaxPayer, key: str, fallback: str = "purchase_10") -> str:
    """科目の既定の税区分（勘定科目マスタ）。カード手数料など科目で決まるもの用。"""
    from .models import Account

    acc = Account.objects.filter(taxpayer=taxpayer, key=key).only("default_tax").first()
    return acc.default_tax if acc is not None and acc.default_tax else fallback


def _rate_tax(rate) -> str:
    try:
        r = D(rate)
    except Exception:
        return "sales_10"
    return "sales_8" if r == D(8) else "sales_10"


def _purchase_tax(rate) -> str:
    r = D(rate)
    if r == 0:
        return "out_of_scope"
    return "purchase_8" if r == D(8) else "purchase_10"


def _balance(lines: list[dict], warnings: list[str]) -> list[dict]:
    lines = [ln for ln in lines if D(ln["amount"]) != 0]
    for ln in lines:  # マイナスは反対側へ
        if D(ln["amount"]) < 0:
            ln["side"] = "credit" if ln["side"] == "debit" else "debit"
            ln["amount"] = str(-D(ln["amount"]))
    dr = sum((D(ln["amount"]) for ln in lines if ln["side"] == "debit"), ZERO)
    cr = sum((D(ln["amount"]) for ln in lines if ln["side"] == "credit"), ZERO)
    if dr != cr:
        diff = dr - cr
        warnings.append(f"借方と貸方が {abs(diff):,.0f}円 合わないため仮勘定に入れました。元の記録を確認してください。")
        lines.append(_line("credit", "suspense_credit", diff, memo="差額（要確認）") if diff > 0 else _line("debit", "suspense_debit", -diff, memo="差額（要確認）"))
    return lines


def _hash(issue_date: date, description: str, lines: list[dict], void: bool) -> str:
    payload = json.dumps({"d": issue_date.isoformat(), "desc": description, "lines": lines, "void": void}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


class YearLocked(Exception):
    """締めた年度の仕訳を変えようとした（build は飛ばして drift に記録する）。"""


def upsert(taxpayer: TaxPayer, *, store, source_type: str, source_id, issue_date: date, description: str, lines: list[dict],
           warnings: list[str] | None = None, void: bool = False, entry_kind: str = "auto", created_by=None, strict_lock: bool = False) -> Journal:
    """
    仕訳を作る・更新する（sync_key で一意）。JournalLine も作り直す。
    締めた年度（FiscalYear.is_locked）に属する仕訳は変更せず、内容が変わっていれば drift_warnings に記録して既存のまま返す
    （strict_lock=True なら YearLocked を投げる）。
    """
    from . import books

    warnings = list(warnings or [])
    lines = _balance(lines, warnings)
    if not lines:
        void = True
    key = f"{taxpayer.id}:{source_type}:{source_id}"
    h = _hash(issue_date, description, lines, void)
    j = Journal.objects.select_for_update().filter(sync_key=key).first()
    locked_new = books.locked_year(taxpayer, issue_date)
    if j is None:
        if locked_new is not None:
            books.record_drift(locked_new, key, f"締め後に新しい元データが見つかりました: {description[:80]}")
            if strict_lock:
                raise YearLocked(key)
            # 締め済み年度には作らない（元データの存在だけ記録）
            return Journal(taxpayer=taxpayer, sync_key=key, issue_date=issue_date, description=description[:200], lines=lines, is_void=True)
        fy = books.fiscal_year_for_date(taxpayer, issue_date, create=True)
        j = Journal.objects.create(
            company=taxpayer.company, taxpayer=taxpayer, store=store, fiscal_year=fy, source_type=source_type, source_id=str(source_id), sync_key=key,
            issue_date=issue_date, description=description[:200], lines=lines, warnings=warnings, content_hash=h, is_void=void,
            entry_kind=entry_kind, created_by=created_by,
        )
        FreeeSync.objects.create(journal=j, status=FreeeSyncStatus.DELETED if void else FreeeSyncStatus.PENDING)
        books.sync_lines(j)
        return j
    if j.content_hash != h:
        locked_old = books.locked_year(taxpayer, j.issue_date)
        locked = locked_old or locked_new
        if locked is not None:
            books.record_drift(locked, key, f"締め後に元データが変わりました: {description[:80]}")
            if strict_lock:
                raise YearLocked(key)
            return j
        j.issue_date, j.description, j.lines, j.warnings, j.is_void = issue_date, description[:200], lines, warnings, void
        j.content_hash = h
        j.revision += 1
        j.fiscal_year = books.fiscal_year_for_date(taxpayer, issue_date, create=True)
        if store is not None:
            j.store = store
        j.save()
        sync = FreeeSync.objects.select_for_update().get(journal=j)
        if sync.freee_id:
            sync.status = FreeeSyncStatus.NEEDS_DELETE if void else FreeeSyncStatus.NEEDS_UPDATE
        else:
            sync.status = FreeeSyncStatus.DELETED if void else FreeeSyncStatus.PENDING
        sync.save(update_fields=["status", "updated_at"])
        books.sync_lines(j)
    elif j.fiscal_year_id is None or not j.entry_lines.exists():
        # 旧データ（行テーブル導入前）の補完
        j.fiscal_year = books.fiscal_year_for_date(taxpayer, issue_date, create=True)
        j.save(update_fields=["fiscal_year", "updated_at"])
        books.sync_lines(j)
    return j


# --- 元データごとの仕訳 -----------------------------------------------------------------
def daily_store_lines(day, taxpayer: TaxPayer | None = None) -> tuple[list[dict], list[str]]:
    """締め済み営業日の店舗の日次仕訳: 売上・返金・未収回収・現金経費・現金過不足・キャストへの現金支払い。"""
    from apps.cash.models import CashMovement
    from apps.sales.models import Checkout, Payment, ReceivableCollection, Refund
    from apps.staff.models import EmployeePayment

    store = day.store
    lines: list[dict] = []
    warnings: list[str] = []
    if taxpayer is None:
        from .compensation import taxpayer_for

        taxpayer = taxpayer_for(store)
    # 売上: この営業日に確定した会計（あとで取り消したものも含む。取消は取消した日の返金として別に計上する）
    checkouts = list(Checkout.objects.filter(store=store, business_date=day.date))
    by_tax: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for co in checkouts:
        allocated = ZERO
        for row in co.tax_breakdown or []:
            amt = D(row.get("total_incl") or 0)
            by_tax[_rate_tax(row.get("rate"))] += amt
            allocated += amt
        rest = D(co.total) - allocated  # 端数調整や内訳のない会計
        if rest:
            main = max(by_tax, key=lambda k: by_tax[k]) if by_tax else "sales_10"
            by_tax[main] += rest
    for tax, amt in sorted(by_tax.items()):
        lines.append(_line("credit", "sales", amt, tax, f"売上 {TAXES[tax]}"))
    pays = Payment.objects.filter(store=store, checkout__in=checkouts)
    method_key = {"cash": "cash", "card": "card_receivable", "receivable": "customer_receivable"}
    for method, key in method_key.items():
        amt = sum((p.amount for p in pays if p.method == method), ZERO)
        if amt:
            lines.append(_line("debit", key, amt, memo={"cash": "現金売上", "card": "カード売上", "receivable": "ツケ売上"}[method]))
    # 返金（会計取消・一部返金）
    for r in Refund.objects.filter(store=store, business_date=day.date).select_related("checkout"):
        lines.append(_line("debit", "sales", r.amount, _main_tax(r.checkout), f"返金 会計#{r.checkout.number}"))
        lines.append(_line("credit", method_key.get(r.method, "cash"), r.amount, memo="返金"))
    # ツケの会計を取り消した分（返金記録がないため、取消日の営業日で売上と売掛金を戻す）
    sw = store.settings.business_day_switch_time if hasattr(store, "settings") else None
    for p in Payment.objects.filter(store=store, method="receivable", status="refunded", checkout__status="voided").select_related("checkout"):
        if p.checkout.voided_at and sw is not None and business_date_for(p.checkout.voided_at, sw, store_tz(store)) == day.date:
            lines.append(_line("debit", "sales", p.amount, _main_tax(p.checkout), f"ツケ会計の取消 会計#{p.checkout.number}"))
            lines.append(_line("credit", "customer_receivable", p.amount, memo="ツケ会計の取消"))
    for c in ReceivableCollection.objects.filter(store=store, business_date=day.date):
        lines.append(_line("debit", method_key.get(c.method, "cash"), c.amount, memo="ツケ回収"))
        lines.append(_line("credit", "customer_receivable", c.amount, memo="ツケ回収"))
    # レジ現金の入出金（日払いは下でまとめる。納付・資金移動に紐づくものはそれぞれの仕訳で計上）
    unassigned_expense = 0
    for mv in CashMovement.objects.filter(business_day=day, is_voided=False).select_related("employee", "account"):
        if hasattr(mv, "tax_remittance") or hasattr(mv, "owner_transfer") or mv.kind == "daily_pay":
            continue
        if mv.kind == "expense":
            # 承認済みの領収書などで勘定科目が付いていればその科目・税区分。無ければ雑費（警告）
            if mv.account_id and mv.account is not None:
                key, tax = mv.account.key, (mv.account.default_tax if mv.account.default_tax in TAXES else "purchase_10")
                receipt = getattr(mv, "expense_receipt", None)
                if receipt is not None and getattr(receipt, "approved_tax_category", ""):
                    tax = receipt.approved_tax_category
                counterparty = getattr(receipt, "approved_vendor", "") if receipt is not None else ""
            else:
                key, tax, counterparty = "misc_expense", "purchase_10", ""
                unassigned_expense += 1
            lines.append(_line("debit", key, mv.amount, tax, f"現金経費 {mv.reason}", counterparty=counterparty))
            lines.append(_line("credit", "cash", mv.amount, memo=f"現金経費 {mv.reason}"))
        elif mv.kind == "other_in":
            lines.append(_line("debit", "cash", mv.amount, memo=f"その他入金 {mv.reason}"))
            lines.append(_line("credit", "suspense_credit", mv.amount, memo=f"その他入金 {mv.reason}"))
            warnings.append(f"レジのその他入金 {mv.amount:,.0f}円（{mv.reason}）は内容がわからないため仮受金にしました。勘定科目を確認してください。")
        elif mv.kind == "other_out":
            lines.append(_line("debit", "suspense_debit", mv.amount, memo=f"その他出金 {mv.reason}"))
            lines.append(_line("credit", "cash", mv.amount, memo=f"その他出金 {mv.reason}"))
            warnings.append(f"レジのその他出金 {mv.amount:,.0f}円（{mv.reason}）は内容がわからないため仮払金にしました。勘定科目を確認してください。")
        # 途中回収・釣銭補充は現金の置き場所が変わるだけなので仕訳しない
    # キャストへの現金支払い（日払い）: 報酬総額 = 渡した額 + 源泉所得税（預り金）+ その他控除
    if unassigned_expense:
        warnings.append(f"勘定科目が未設定の現金経費が {unassigned_expense} 件あります（雑費として仮に計上）。領収書を承認すると科目が付きます。")
    eps = EmployeePayment.objects.filter(cash_movement__business_day=day, is_voided=False).select_related("withholding", "employee")
    totals = defaultdict(lambda: ZERO)
    per_employee: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    undetermined = 0
    for ep in eps:
        rec = getattr(ep, "withholding", None)
        if rec is not None and rec.tax_category == "salary":
            key = "salary"
        elif rec is not None and rec.tax_category == "outsourcing_fee":
            key = "outsourcing"
        else:
            key = "cast_fee"
        if rec is None or rec.status == "needs_review":
            undetermined += 1
        gross = ep.amount + ep.withholding_amount + ep.deduction_amount
        per_employee[(key, ep.employee_id, ep.employee.display_name)] += gross
        totals["cash"] += ep.amount
        totals["withholding"] += ep.withholding_amount
        totals["deduction"] += ep.deduction_amount
    labels = {"cast_fee": "キャスト報酬（日払い）", "salary": "給与（日払い）", "outsourcing": "業務委託費（日払い）"}
    for (key, emp_id, name), gross in sorted(per_employee.items(), key=lambda x: (x[0][0], x[0][1])):
        lines.append(_line("debit", key, gross, memo=f"{labels[key]} {name}", employee_id=emp_id))
    if totals["cash"]:
        lines.append(_line("credit", "cash", totals["cash"], memo="日払い 手渡し額"))
    if totals["withholding"]:
        lines.append(_line("credit", "withholding_payable", totals["withholding"], memo="源泉所得税（預り金）"))
    if totals["deduction"]:
        lines.append(_line("credit", "other_deduction_income", totals["deduction"], memo="厚生費など（報酬からの控除）"))
    if undetermined:
        warnings.append(f"税務区分が要確認の支払いが {undetermined} 件あります。支払報酬料として仮に計上しています。")
    # 現金過不足（実際の現金 − 理論上の現金）
    diff = D(day.difference or 0)
    if diff < 0:
        lines.append(_line("debit", "cash_shortage", -diff, memo=f"現金不足 {day.difference_reason}"))
        lines.append(_line("credit", "cash", -diff, memo="現金不足"))
    elif diff > 0:
        lines.append(_line("debit", "cash", diff, memo="現金過剰"))
        lines.append(_line("credit", "cash_overage", diff, memo=f"現金過剰 {day.difference_reason}"))
    return lines, warnings


def _main_tax(checkout) -> str:
    rows = checkout.tax_breakdown or []
    if not rows:
        return "sales_10"
    top = max(rows, key=lambda r: D(r.get("total_incl") or 0))
    return _rate_tax(top.get("rate"))


def _owner_in_out(direction: str) -> str:
    return "owner_in" if direction == "in" else "owner_out"


@transaction.atomic
def build(taxpayer: TaxPayer, start: date, end: date) -> dict:
    """
    期間内の元データから仕訳を作る・更新する（何度実行しても同じ結果）。
    締めた年度の仕訳は変更せず、元データが変わっていれば年度の drift_warnings に記録する。
    再開した営業日（締め→再開）の日次仕訳は、締め直すまで取消扱いにする（締め前の数字は動くため）。
    """
    from apps.cash.models import BankDeposit, BusinessDay
    from apps.staff.models import EmployeePayment

    from . import books

    books.ensure_accounts(taxpayer)
    store_ids = list(StoreTaxProfile.objects.filter(taxpayer=taxpayer).values_list("store_id", flat=True))
    count = 0
    for day in BusinessDay.objects.filter(store_id__in=store_ids, date__gte=start, date__lte=end).select_related("store__settings"):
        if day.status != "closed":
            # 再開された営業日の古い仕訳があれば取消にする（締めるとまた作る）
            old = Journal.objects.filter(sync_key=f"{taxpayer.id}:daily:{day.id}", is_void=False).first()
            if old is not None:
                upsert(taxpayer, store=day.store, source_type="daily", source_id=day.id, issue_date=day.date, description=old.description, lines=old.lines,
                       warnings=["営業日が再開されたため、締め直すまで仕訳を取消にしています。"], void=True)
            continue
        lines, warnings = daily_store_lines(day, taxpayer)
        upsert(taxpayer, store=day.store, source_type="daily", source_id=day.id, issue_date=day.date,
               description=f"{day.store.name} {day.date.isoformat()} 日次（売上・現金・日払い）", lines=lines, warnings=warnings)
        count += 1
    for dep in BankDeposit.objects.filter(store_id__in=store_ids, business_day__date__gte=start, business_day__date__lte=end).select_related("store", "business_day"):
        upsert(taxpayer, store=dep.store, source_type="bank_deposit", source_id=dep.id, issue_date=dep.business_day.date,
               description=f"{dep.store.name} 売上金の銀行入金", lines=[_line("debit", "bank", dep.amount), _line("credit", "cash", dep.amount)])
        count += 1
    # 振込などレジ以外のキャスト・従業員への支払い
    for ep in EmployeePayment.objects.filter(store_id__in=store_ids, cash_movement__isnull=True, business_date__gte=start, business_date__lte=end).select_related("withholding", "employee", "store"):
        rec = getattr(ep, "withholding", None)
        if rec is not None and rec.tax_category == "salary":
            key = "salary"
        elif rec is not None and rec.tax_category == "outsourcing_fee":
            key = "outsourcing"
        else:
            key = "cast_fee"
        gross = ep.amount + ep.withholding_amount + ep.deduction_amount
        lines = [_line("debit", key, gross, memo=f"{ep.employee.display_name} {ep.get_kind_display()}", employee_id=ep.employee_id), _line("credit", "bank", ep.amount)]
        if ep.withholding_amount:
            lines.append(_line("credit", "withholding_payable", ep.withholding_amount, memo="源泉所得税（預り金）"))
        if ep.deduction_amount:
            lines.append(_line("credit", "other_deduction_income", ep.deduction_amount))
        upsert(taxpayer, store=ep.store, source_type="employee_payment", source_id=ep.id, issue_date=ep.business_date,
               description=f"{ep.employee.display_name} への支払い（{ep.get_kind_display()}）", lines=lines, void=ep.is_voided)
        count += 1
    for r in TaxRemittance.objects.filter(taxpayer=taxpayer, paid_on__gte=start, paid_on__lte=end).select_related("month"):
        credit = {"bank": "bank", "cash_register": "cash", "owner_personal": "owner_in"}.get(r.paid_from, "bank")
        upsert(taxpayer, store=None, source_type="tax_remittance", source_id=r.id, issue_date=r.paid_on,
               description=f"源泉所得税の納付（{r.month.period_start:%Y年%m月}分）",
               lines=[_line("debit", "withholding_payable", r.amount), _line("credit", credit, r.amount)], void=r.is_voided)
        count += 1
    for e in Expense.objects.filter(taxpayer=taxpayer, date__gte=start, date__lte=end).select_related("account"):
        credit = {"bank": "bank", "card": "accrued_card", "office_cash": "cash", "owner_personal": "owner_in"}[e.paid_from]
        upsert(taxpayer, store=e.store, source_type="expense", source_id=e.id, issue_date=e.date,
               description=f"経費 {e.counterparty} {e.description}".strip(),
               lines=expense_lines(e) + [_line("credit", credit, e.amount)], void=e.is_voided)
        count += 1
    for t in OwnerFundTransfer.objects.filter(taxpayer=taxpayer, date__gte=start, date__lte=end):
        money = "cash" if t.method == "cash_register" else "bank"
        if t.direction == "in":
            lines = [_line("debit", money, t.amount, memo="経営者からの資金投入"), _line("credit", "owner_in", t.amount)]
        else:
            lines = [_line("debit", "owner_out", t.amount, memo="経営者への資金引出し"), _line("credit", money, t.amount)]
        upsert(taxpayer, store=t.store, source_type="owner_transfer", source_id=t.id, issue_date=t.date,
               description="経営者との資金移動（売上・経費ではありません）", lines=lines, void=t.is_voided)
        count += 1
    fee_tax = _account_tax(taxpayer, "card_fee", "non_taxable")
    for s in CardSettlement.objects.filter(taxpayer=taxpayer, deposit_date__gte=start, deposit_date__lte=end):
        lines = [_line("debit", "bank", s.deposit_amount, counterparty=s.provider_name, bank_account_id=getattr(s, "bank_account_ref", None)),
                 _line("credit", "card_receivable", s.sales_amount, memo=f"{s.provider_name} カード売上の入金", counterparty=s.provider_name)]
        if s.fee_amount:
            lines.append(_line("debit", "card_fee", s.fee_amount, fee_tax, "カード決済手数料", counterparty=s.provider_name))
        upsert(taxpayer, store=s.store, source_type="card_settlement", source_id=s.id, issue_date=s.deposit_date,
               description=f"カード売上の入金 {s.provider_name}", lines=lines, void=s.is_voided)
        count += 1
    for hook in _BUILD_HOOKS:
        count += hook(taxpayer, start, end) or 0
    drift = 0
    for fy in books.FiscalYear.objects.filter(taxpayer=taxpayer, start_date__lte=end, end_date__gte=start):
        drift += len(fy.drift_warnings or []) if fy.is_locked else 0
    return {"built": count, "drift_warnings": drift}


# 他アプリ（expenses: 買掛金、closing: 決算整理）が自分の元データの仕訳を登録するための差し込み口
_BUILD_HOOKS: list = []


def register_build_hook(fn) -> None:
    if fn not in _BUILD_HOOKS:
        _BUILD_HOOKS.append(fn)


def expense_lines(e: Expense) -> list[dict]:
    """経費の借方行。8%/10% の内訳があれば税率ごとに分ける。科目は Account（無ければ account_key）。"""
    key = e.account.key if getattr(e, "account", None) is not None else e.account_key
    cp = e.counterparty
    if e.tax_breakdown:
        out = []
        allocated = ZERO
        for row in e.tax_breakdown:
            amt = D(row.get("taxable_incl") or row.get("amount") or 0)
            if amt <= 0:
                continue
            out.append(_line("debit", key, amt, _purchase_tax(row.get("rate")), e.description, counterparty=cp))
            allocated += amt
        rest = e.amount - allocated
        if rest:
            out.append(_line("debit", key, rest, _purchase_tax(e.tax_rate), e.description, counterparty=cp))
        return out
    return [_line("debit", key, e.amount, _purchase_tax(e.tax_rate), e.description, counterparty=cp)]


def journals_for(taxpayer: TaxPayer, start: date, end: date):
    return Journal.objects.filter(taxpayer=taxpayer, issue_date__gte=start, issue_date__lte=end).select_related("freee_sync", "store").order_by("issue_date", "id")


def journal_csv_rows(taxpayer: TaxPayer, journals) -> list[list[str]]:
    """
    仕訳CSV（借方・貸方の列が分かれた形式）。freee の「他社会計ソフト仕訳のインポート」では列を割り当てて取り込む。
    freee 公式のテンプレートと列の並びが同じであることは保証しない（取込時に列の割当てを確認する）。
    """
    names = account_names(taxpayer)
    rows = []
    for j in journals:
        if j.is_void:
            continue
        debits = [ln for ln in j.lines if ln["side"] == "debit"]
        credits = [ln for ln in j.lines if ln["side"] == "credit"]
        for i in range(max(len(debits), len(credits))):
            dr = debits[i] if i < len(debits) else None
            cr = credits[i] if i < len(credits) else None
            rows.append([
                j.issue_date.isoformat(), j.sync_key,
                names.get(dr["key"], dr["key"]) if dr else "", TAXES.get(dr["tax"], "") if dr else "", dr["amount"] if dr else "",
                names.get(cr["key"], cr["key"]) if cr else "", TAXES.get(cr["tax"], "") if cr else "", cr["amount"] if cr else "",
                (dr or cr)["memo"] or j.description,
            ])
    return rows


JOURNAL_CSV_HEADER = ["取引日", "NOXIA仕訳ID", "借方勘定科目", "借方税区分", "借方金額", "貸方勘定科目", "貸方税区分", "貸方金額", "摘要"]

# freee の税区分名（他社会計ソフト仕訳のインポートで「税区分の対応付け」に出る名前に寄せる。非課税は売上側・仕入側で名前が違う）
FREEE_TAXES: dict[str, str] = {
    "sales_10": "課税売上10%",
    "sales_8": "課税売上8%（軽）",
    "purchase_10": "課対仕入10%",
    "purchase_8": "課対仕入8%（軽）",
    "out_of_scope": "対象外",
}
FREEE_CSV_HEADER = ["伝票番号", "取引日", "借方勘定科目", "借方税区分", "借方金額", "貸方勘定科目", "貸方税区分", "貸方金額", "摘要", "NOXIA仕訳ID"]


def _freee_tax(tax: str, side: str) -> str:
    if tax == "non_taxable":
        return "非課仕入" if side == "debit" else "非課売上"
    return FREEE_TAXES.get(tax, "対象外")


def _freee_amount(v) -> str:
    d = D(v)
    return str(int(d)) if d == d.to_integral_value() else str(d)


def freee_csv_rows(taxpayer: TaxPayer, journals) -> list[list[str]]:
    """
    freee の「取引入力 → 振替伝票 → インポート → 他社会計ソフトインポート」で取り込む形。
    1 行に借方と貸方を並べ、借方・貸方の行数が違う仕訳は同じ伝票番号で複数行にする（freee は伝票番号が同じ行を 1 つの仕訳にまとめる）。
    勘定科目名は AccountMapping（freee 用の名前）があればそれを使う。金額は税込、消費税額は freee 側で税区分から計算させる。
    """
    names = account_names(taxpayer)
    rows = []
    for j in journals:
        if j.is_void:
            continue
        debits = [ln for ln in j.lines if ln["side"] == "debit"]
        credits = [ln for ln in j.lines if ln["side"] == "credit"]
        for i in range(max(len(debits), len(credits))):
            dr = debits[i] if i < len(debits) else None
            cr = credits[i] if i < len(credits) else None
            memo = (dr or cr).get("memo") or j.description
            rows.append([
                str(j.id), j.issue_date.strftime("%Y/%m/%d"),
                names.get(dr["key"], dr["key"]) if dr else "", _freee_tax(dr["tax"], "debit") if dr else "", _freee_amount(dr["amount"]) if dr else "",
                names.get(cr["key"], cr["key"]) if cr else "", _freee_tax(cr["tax"], "credit") if cr else "", _freee_amount(cr["amount"]) if cr else "",
                (memo or "")[:200], j.sync_key,
            ])
    return rows


def reconcile(taxpayer: TaxPayer, start: date, end: date) -> dict:
    """売上・現金・カード入金・源泉所得税・freee 連携の照合。"""
    from django.db.models import Sum

    from apps.cash.models import BusinessDay
    from apps.sales.models import Checkout, Payment, Refund

    from .models import WithholdingAdjustment

    store_ids = list(StoreTaxProfile.objects.filter(taxpayer=taxpayer).values_list("store_id", flat=True))
    # 仕訳と同じ考え方: 会計した日に売上、取り消した日に返金（ツケの会計の取消は取消日に売上を戻す）
    pos_sales = Checkout.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end).aggregate(s=Sum("total"))["s"] or ZERO
    refunds = Refund.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end).aggregate(s=Sum("amount"))["s"] or ZERO
    for p in Payment.objects.filter(store_id__in=store_ids, method="receivable", status="refunded", checkout__status="voided").select_related("checkout", "store__settings"):
        vd = business_date_for(p.checkout.voided_at, p.store.settings.business_day_switch_time, store_tz(p.store)) if p.checkout.voided_at else None
        if vd and start <= vd <= end:
            refunds += p.amount
    journal_sales = ZERO
    js = list(journals_for(taxpayer, start, end).filter(is_void=False))
    for j in js:
        for ln in j.lines:
            if ln["key"] == "sales":
                journal_sales += D(ln["amount"]) if ln["side"] == "credit" else -D(ln["amount"])
    days = BusinessDay.objects.filter(store_id__in=store_ids, date__gte=start, date__lte=end)
    open_days = days.filter(status="open").count()
    cash_diff = days.filter(status="closed").aggregate(s=Sum("difference"))["s"] or ZERO
    card_sales = Payment.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end, method="card").aggregate(s=Sum("amount"))["s"] or ZERO
    card_refunds = Refund.objects.filter(store_id__in=store_ids, business_date__gte=start, business_date__lte=end, method="card").aggregate(s=Sum("amount"))["s"] or ZERO
    card_settled = CardSettlement.objects.filter(taxpayer=taxpayer, deposit_date__gte=start, deposit_date__lte=end, is_voided=False).aggregate(s=Sum("sales_amount"))["s"] or ZERO
    withheld = WithholdingRecord.objects.filter(taxpayer=taxpayer, paid_on__gte=start, paid_on__lte=end, is_voided=False).aggregate(s=Sum("withholding_amount"))["s"] or ZERO
    adj = WithholdingAdjustment.objects.filter(taxpayer=taxpayer, applies_on__gte=start, applies_on__lte=end, is_voided=False).aggregate(s=Sum("amount_delta"))["s"] or ZERO
    remitted = TaxRemittance.objects.filter(taxpayer=taxpayer, month__period_start__gte=start.replace(day=1), month__period_start__lte=end, is_voided=False).aggregate(s=Sum("amount"))["s"] or ZERO
    sync_counts = defaultdict(int)
    for j in js:
        sync_counts[j.freee_sync.status] += 1
    checks = [
        {"code": "sales", "label": "POS の売上（返金差引）と仕訳の売上", "left": str(pos_sales - refunds), "right": str(journal_sales),
         "ok": (pos_sales - refunds) == journal_sales and open_days == 0,
         "note": f"締めていない営業日が {open_days} 日あります（締めた日だけ仕訳になります）。" if open_days else ""},
        {"code": "cash_difference", "label": "レジ締めの現金差額の合計", "left": str(cash_diff), "right": "0", "ok": cash_diff == 0,
         "note": "差額は雑損失／雑収入として仕訳しています。原因を確認してください。" if cash_diff else ""},
        {"code": "card", "label": "カード売上（返金差引）と記録したカード入金の対象売上", "left": str(card_sales - card_refunds), "right": str(card_settled),
         "ok": (card_sales - card_refunds) == card_settled, "note": "入金のタイミングで月をまたぐことがあります。"},
        {"code": "withholding", "label": "源泉所得税（徴収額＋訂正）と納付額", "left": str(withheld + adj), "right": str(remitted),
         "ok": (withheld + adj) == remitted, "note": "当月分は翌月10日までに納付します。"},
    ]
    return {"from": start.isoformat(), "to": end.isoformat(), "checks": checks, "freee_sync": dict(sync_counts), "journal_count": len(js)}


def profit_and_loss(taxpayer: TaxPayer, start: date, end: date, *, store_id: int | None = None) -> dict:
    """
    損益（仕訳行からの集計）。収益・売上原価・経費の科目だけを集め、資金移動・預り金・売掛金などの動きは入れない。
    store_id を指定すると店舗別の内訳（JournalLine.store）。
    """
    from .ledgers import pl_summary

    return pl_summary(taxpayer, start, end, store_id=store_id)


def stores_of(taxpayer: TaxPayer):
    from apps.core.models import Store

    return Store.objects.filter(Q(tax_profile__taxpayer=taxpayer))
