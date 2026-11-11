"""
銀行の業務処理: 取込（プレビュー・確定・取消）、照合の確定・解除、対象外、振替、集計。仕訳は元データ側の経路で作る。

- カード入金の確定 → accounting.CardSettlement を作り journals.build で仕訳（預金＋支払手数料／売掛金）。
- 自社口座間の振替 → 仕訳 預金(入金側口座)／預金(出金側口座)（source_type=bank_transfer）。build の差し込み口で作り直せる。
- それ以外（経費・振込・預入・資金移動・納付）は既存の元データの仕訳があるので、照合は紐付けだけ（二重計上しない）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting import books, journals
from apps.accounting.models import CardSettlement
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO

from . import importer, matching
from .models import BankAccount, BankImportProfile, BankReconciliation, BankStatementImport, BankTransaction, ImportStatus, MatchType, RecStatus, TxStatus


# --- 口座 ---------------------------------------------------------------------------------------
def ensure_bank_account_account(acc: BankAccount) -> BankAccount:
    if acc.account_id is None:
        acc.account = books.account_for(acc.taxpayer, "bank")
        acc.save(update_fields=["account", "updated_at"])
    return acc


def previous_closing_balance(acc: BankAccount) -> Decimal | None:
    last = BankStatementImport.objects.filter(bank_account=acc, status=ImportStatus.COMMITTED, closing_balance__isnull=False).order_by("-period_end", "-id").first()
    if last is not None:
        return last.closing_balance
    if acc.opening_balance_date is not None:
        return acc.opening_balance
    return None


# --- 取込 ---------------------------------------------------------------------------------------
def _profile_kwargs(profile: BankImportProfile | None, overrides: dict | None) -> dict:
    kw = {"column_map": None, "encoding": None, "delimiter": None, "header_rows": None, "footer_rows": 0, "amount_rules": None}
    if profile is not None:
        kw.update({"column_map": profile.column_map or None, "encoding": profile.encoding if profile.encoding != "auto" else None,
                   "delimiter": profile.delimiter if profile.delimiter != "auto" else None, "header_rows": profile.header_rows or None,
                   "footer_rows": profile.footer_rows or 0, "amount_rules": profile.amount_rules or None})
    for k, v in (overrides or {}).items():
        if k in kw and v not in (None, "", {}):
            kw[k] = v
    return kw


def find_profile(acc: BankAccount, header: list[str]) -> BankImportProfile | None:
    sig = importer.header_signature(header) if header else ""
    q = BankImportProfile.objects.filter(taxpayer=acc.taxpayer)
    if sig:
        p = q.filter(header_signature=sig).filter(bank_account=acc).first() or q.filter(header_signature=sig, bank_account__isnull=True).first()
        if p:
            return p
    return q.filter(bank_account=acc).order_by("-last_used_at", "-id").first()


def preview(acc: BankAccount, data: bytes, *, profile: BankImportProfile | None = None, overrides: dict | None = None, reference: date | None = None) -> dict:
    """保存せずに解析結果を返す。列対応が未確定なら推測を返す。"""
    if profile is None:
        first = importer.parse_statement(data, bank_account_id=acc.id, reference=reference)
        profile = find_profile(acc, first.header)
    res = importer.parse_statement(data, bank_account_id=acc.id, reference=reference, **_profile_kwargs(profile, overrides))
    existing = set(BankTransaction.objects.filter(bank_account=acc, row_hash__in=[r.row_hash for r in res.rows]).values_list("row_hash", flat=True))
    # 連続性は「今回新しく入る行」で確かめる（重なっている行は前回取込済み）
    check, note, opening, closing = importer.balance_continuity([r for r in res.rows if r.row_hash not in existing], previous_closing_balance(acc))
    ps, pe = res.period
    return {
        "encoding": res.encoding, "delimiter": res.delimiter, "header_row": res.header_row, "header": res.header, "column_map": res.column_map,
        "guessed": res.guessed, "profile_id": profile.id if profile else None, "descending": res.descending, "preview": res.preview,
        "columns": [{"key": k, "label": v} for k, v in importer.COLUMN_LABELS.items()],
        "rows_total": res.rows_total, "rows_parsed": len(res.rows), "rows_duplicate": sum(1 for r in res.rows if r.row_hash in existing), "errors": res.errors[:50],
        "period_start": ps, "period_end": pe, "balance_check": check, "balance_note": note, "opening_balance": str(opening) if opening is not None else None,
        "closing_balance": str(closing) if closing is not None else None,
        "sample": [{"row_no": r.row_no, "date": r.date, "description": r.description, "counterparty": r.counterparty, "amount_in": str(r.amount_in),
                    "amount_out": str(r.amount_out), "balance": str(r.balance) if r.balance is not None else None, "duplicate": r.row_hash in existing} for r in res.rows[:20]],
    }


@transaction.atomic
def create_import(acc: BankAccount, user, f, data: bytes, *, profile: BankImportProfile | None = None, overrides: dict | None = None, save_profile: bool = True,
                  reference: date | None = None) -> BankStatementImport:
    """CSV を解析して明細を作る（status=parsed。確定は commit）。同じファイルは取り込めない。"""
    import hashlib

    sha = hashlib.sha256(data).hexdigest()
    if BankStatementImport.objects.filter(bank_account=acc, sha256=sha).exclude(status=ImportStatus.ROLLED_BACK).exists():
        raise ApiError("duplicate_file", "このファイルはすでに取り込まれています。", status=409)
    if profile is None:
        first = importer.parse_statement(data, bank_account_id=acc.id, reference=reference)
        profile = find_profile(acc, first.header)
    res = importer.parse_statement(data, bank_account_id=acc.id, reference=reference, **_profile_kwargs(profile, overrides))
    if res.errors and not res.rows:
        raise ApiError("parse_error", res.errors[0]["message"], status=400, details=res.errors[:20])
    if not res.rows:
        raise ApiError("validation_error", "取り込める明細行がありません。列の対応を確認してください。")
    existing = set(BankTransaction.objects.filter(bank_account=acc, row_hash__in=[r.row_hash for r in res.rows]).values_list("row_hash", flat=True))
    new_rows = [r for r in res.rows if r.row_hash not in existing]
    check, note, opening, closing = importer.balance_continuity(new_rows, previous_closing_balance(acc))
    ps, pe = res.period
    imp = BankStatementImport(
        bank_account=acc, profile=profile, sha256=sha, original_name=getattr(f, "name", "statement.csv")[:200], encoding_detected=res.encoding,
        column_map=res.column_map, rows_total=res.rows_total, period_start=ps, period_end=pe, opening_balance=opening, closing_balance=closing,
        balance_check=check, balance_note=note[:200], error_log=res.errors[:100], imported_by=user,
    )
    f.seek(0)
    imp.file.save(f"b{acc.id}-{sha[:16]}.csv", f, save=False)
    imp.save()
    rows = []
    dup = 0
    for r in res.rows:
        if r.row_hash in existing:
            dup += 1
            continue
        rows.append(BankTransaction(bank_account=acc, statement_import=imp, row_no=r.row_no, date=r.date, description=r.description, counterparty=r.counterparty,
                                    amount_in=r.amount_in, amount_out=r.amount_out, balance=r.balance, raw=r.raw, row_hash=r.row_hash))
    BankTransaction.objects.bulk_create(rows)
    imp.rows_imported, imp.rows_duplicate, imp.rows_error = len(rows), dup, len(res.errors)
    imp.save(update_fields=["rows_imported", "rows_duplicate", "rows_error", "updated_at"])
    if save_profile and (profile is None or (overrides and overrides.get("column_map"))):
        profile = BankImportProfile.objects.create(
            taxpayer=acc.taxpayer, bank_account=acc, name=f"{acc.name} の CSV", encoding=res.encoding, delimiter=res.delimiter, header_rows=(res.header_row + 1) if res.header_row is not None else 0,
            column_map=res.column_map, header_signature=importer.header_signature(res.header) if res.header else "", sample_hash=sha, last_used_at=timezone.now(),
        )
        imp.profile = profile
        imp.save(update_fields=["profile", "updated_at"])
    elif profile is not None:
        profile.last_used_at = timezone.now()
        profile.save(update_fields=["last_used_at", "updated_at"])
    return imp


@transaction.atomic
def commit_import(imp: BankStatementImport, user) -> BankStatementImport:
    imp = BankStatementImport.objects.select_for_update().get(pk=imp.pk)
    if imp.status != ImportStatus.PARSED:
        raise ApiError("invalid_status", "確認待ちの取込だけ確定できます。", status=409)
    imp.status = ImportStatus.COMMITTED
    imp.committed_at = timezone.now()
    imp.save(update_fields=["status", "committed_at", "updated_at"])
    matching.scan(imp.bank_account.taxpayer, imp.period_start, imp.period_end)
    return imp


@transaction.atomic
def rollback_import(imp: BankStatementImport, user, *, reason: str = "") -> BankStatementImport:
    imp = BankStatementImport.objects.select_for_update().get(pk=imp.pk)
    if imp.status == ImportStatus.ROLLED_BACK:
        raise ApiError("invalid_status", "すでに取り消されています。", status=409)
    if BankReconciliation.objects.filter(bank_transaction__statement_import=imp, status=RecStatus.CONFIRMED).exists():
        raise ApiError("has_confirmed", "照合を確定した明細があるため取り消せません。先に消込を解除してください。", status=409)
    imp.transactions.all().delete()
    imp.status = ImportStatus.ROLLED_BACK
    imp.rolled_back_at = timezone.now()
    imp.save(update_fields=["status", "rolled_back_at", "updated_at"])
    return imp


# --- 照合 ---------------------------------------------------------------------------------------
def _refresh_tx_status(tx: BankTransaction) -> None:
    confirmed = tx.reconciliations.filter(status=RecStatus.CONFIRMED)
    if tx.status in (TxStatus.IGNORED,):
        return
    if confirmed.filter(match_type=MatchType.TRANSFER).exists():
        tx.status = TxStatus.TRANSFER
    elif confirmed.exists():
        allocated = sum((r.amount - r.fee_amount if r.match_type == MatchType.CARD_SETTLEMENT else r.amount for r in confirmed), ZERO)
        tx.status = TxStatus.MATCHED if allocated >= tx.amount else TxStatus.PARTIAL
    else:
        tx.status = TxStatus.SUGGESTED if tx.reconciliations.filter(status=RecStatus.SUGGESTED).exists() else TxStatus.UNMATCHED
    tx.save(update_fields=["status", "updated_at"])


def _check_target(tx: BankTransaction, tp, match_type: str, target_id) -> tuple[Decimal, object]:
    """対象が同じ事業者のものか確かめ、(対象の金額, 対象) を返す。"""
    from apps.accounting.models import Expense, OwnerFundTransfer, StoreTaxProfile, TaxRemittance
    from apps.cash.models import BankDeposit
    from apps.staff.models import EmployeePayment

    stores = list(StoreTaxProfile.objects.filter(taxpayer=tp).values_list("store_id", flat=True))
    if match_type == MatchType.BANK_DEPOSIT:
        o = BankDeposit.objects.filter(id=target_id, store_id__in=stores).first()
        return (o.amount, o) if o else (None, None)
    if match_type == MatchType.EMPLOYEE_PAYMENT:
        o = EmployeePayment.objects.filter(id=target_id, store_id__in=stores, is_voided=False).first()
        return (o.amount, o) if o else (None, None)
    if match_type == MatchType.EXPENSE:
        o = Expense.objects.filter(id=target_id, taxpayer=tp, is_voided=False).first()
        return (o.amount, o) if o else (None, None)
    if match_type == MatchType.OWNER_TRANSFER:
        o = OwnerFundTransfer.objects.filter(id=target_id, taxpayer=tp, is_voided=False).first()
        return (o.amount, o) if o else (None, None)
    if match_type == MatchType.TAX_REMITTANCE:
        o = TaxRemittance.objects.filter(id=target_id, taxpayer=tp, is_voided=False).first()
        return (o.amount, o) if o else (None, None)
    if match_type == MatchType.PAYABLE_PAYMENT:
        try:
            from apps.expenses.models import PayablePayment

            o = PayablePayment.objects.filter(id=target_id, payable__taxpayer=tp, is_voided=False).first()
            return (o.amount, o) if o else (None, None)
        except Exception:  # noqa: BLE001
            return None, None
    if match_type == MatchType.TRANSFER:
        o = BankTransaction.objects.filter(id=target_id, bank_account__taxpayer=tp).exclude(bank_account_id=tx.bank_account_id).first()
        if o is None or o.status in (TxStatus.IGNORED, TxStatus.MATCHED, TxStatus.TRANSFER):
            return None, None
        return (o.amount, o)
    if match_type == MatchType.MANUAL_JOURNAL:
        o = journals.Journal.objects.filter(id=target_id, taxpayer=tp, is_void=False).first()
        return (D(sum(D(ln["amount"]) for ln in o.lines if ln["side"] == "debit")) if o else None, o)
    return None, None


def _already_confirmed(match_type: str, target_id) -> bool:
    f = matching.TARGET_FIELD.get(match_type)
    return bool(f) and BankReconciliation.objects.filter(status=RecStatus.CONFIRMED, match_type=match_type, **{f: target_id}).exists()


@transaction.atomic
def confirm(tx: BankTransaction, user, allocations: list[dict]) -> list[BankReconciliation]:
    """
    照合の確定。allocations: [{match_type, target_id, amount, fee_amount, store_id, period_start, period_end, items:[{target_id, amount}]}]
    配分の合計（カード入金は amount − fee）が明細の金額と一致しなければ部分照合（partially_matched）になる。超えていればエラー。
    """
    tx = BankTransaction.objects.select_for_update().get(pk=tx.pk)
    if tx.status in (TxStatus.IGNORED,):
        raise ApiError("invalid_status", "対象外にした明細です。先に対象外を解除してください。", status=409)
    tp = tx.bank_account.taxpayer
    if not allocations:
        raise ApiError("validation_error", "照合する対象を選んでください。")
    # 合算候補の items を個別の配分に展開する
    flat: list[dict] = []
    for a in allocations:
        if a.get("items"):
            for it in a["items"]:
                flat.append({**a, "items": None, "target_id": it["target_id"], "amount": it["amount"]})
        else:
            flat.append(a)
    already = sum((r.amount - r.fee_amount if r.match_type == MatchType.CARD_SETTLEMENT else r.amount for r in tx.reconciliations.filter(status=RecStatus.CONFIRMED)), ZERO)
    created: list[BankReconciliation] = []
    total = already
    for a in flat:
        mtype = a.get("match_type")
        if mtype not in MatchType.values:
            raise ApiError("validation_error", "照合の種類が不正です。")
        fee = D(a.get("fee_amount") or 0)
        if fee < 0:
            raise ApiError("validation_error", "手数料は 0 以上で入力してください。")
        rec = BankReconciliation(bank_transaction=tx, match_type=mtype, status=RecStatus.CONFIRMED, rule=(a.get("rule") or "manual")[:40],
                                 label=(a.get("label") or "")[:200], confirmed_by=user, confirmed_at=timezone.now(), created_by=user, fee_amount=fee)
        if mtype == MatchType.CARD_SETTLEMENT:
            if not tx.amount_in:
                raise ApiError("validation_error", "カード入金は入金の明細にだけ照合できます。")
            sales = D(a.get("amount") or 0)
            if sales <= 0 or sales - fee <= 0:
                raise ApiError("validation_error", "対象の売上額と手数料を確認してください。")
            store_id = a.get("store_id")
            ps = date.fromisoformat(a["period_start"]) if a.get("period_start") else None
            pe = date.fromisoformat(a["period_end"]) if a.get("period_end") else None
            cs = CardSettlement.objects.create(
                company=tp.company, taxpayer=tp, store_id=store_id, deposit_date=tx.date, sales_amount=sales, fee_amount=fee, deposit_amount=sales - fee,
                provider_name=(tx.counterparty or tx.description)[:60], period_start=ps, period_end=pe, bank_account_ref=tx.bank_account_id, created_by=user,
                note=f"銀行明細 #{tx.id} から照合",
            )
            rec.card_settlement = cs
            rec.amount, rec.store_id, rec.period_start, rec.period_end = sales, store_id, ps, pe
            journals.build(tp, tx.date, tx.date)
            rec.journal = journals.Journal.objects.filter(sync_key=f"{tp.id}:card_settlement:{cs.id}").first()
            total += sales - fee
        else:
            target_amount, obj = _check_target(tx, tp, mtype, a.get("target_id"))
            if obj is None:
                raise ApiError("not_found", "照合する対象が見つかりません（別の事業者のもの、または取消済み）。", status=404)
            if _already_confirmed(mtype, a.get("target_id")):
                raise ApiError("already_reconciled", "その対象はすでに別の明細と照合されています。", status=409)
            amount = D(a.get("amount") or target_amount)
            if amount <= 0:
                raise ApiError("validation_error", "配分額は 1 円以上で入力してください。")
            matching._set_target(rec, mtype, a.get("target_id"))
            rec.amount = amount
            if mtype == MatchType.TRANSFER:
                other: BankTransaction = obj
                if other.amount != tx.amount:
                    raise ApiError("validation_error", "振替の相手の明細は同じ金額である必要があります。")
                rec.journal = _transfer_journal(tp, tx, other, user)
                BankReconciliation.objects.create(bank_transaction=other, match_type=MatchType.TRANSFER, counter_transaction=tx, amount=amount, status=RecStatus.CONFIRMED,
                                                  rule="transfer", label=f"{tx.bank_account.name} との振替"[:200], confirmed_by=user, confirmed_at=timezone.now(), created_by=user, journal=rec.journal)
                other.status = TxStatus.TRANSFER
                other.save(update_fields=["status", "updated_at"])
            total += amount
        rec.save()
        created.append(rec)
    if total > tx.amount:
        raise ApiError("over_allocated", f"配分の合計 {total:,.0f}円 が明細の金額 {tx.amount:,.0f}円 を超えています。", status=400)
    tx.reconciliations.filter(status=RecStatus.SUGGESTED).delete()
    _refresh_tx_status(tx)
    return created


def _transfer_journal(tp, tx: BankTransaction, other: BankTransaction, user):
    src, dst = (other, tx) if tx.amount_in else (tx, other)  # src: 出金側, dst: 入金側
    lines = [journals._line("debit", "bank", dst.amount, memo=f"{dst.bank_account.name} へ入金", bank_account_id=dst.bank_account_id),
             journals._line("credit", "bank", src.amount, memo=f"{src.bank_account.name} から出金", bank_account_id=src.bank_account_id)]
    return journals.upsert(tp, store=None, source_type="bank_transfer", source_id=f"{src.id}-{dst.id}", issue_date=dst.date,
                           description=f"自社口座間の振替 {src.bank_account.name} → {dst.bank_account.name}", lines=lines, created_by=user)


def rebuild_transfer_journals(tp, start: date, end: date) -> int:
    """journals.build の差し込み口: 確定済みの振替の仕訳を作り直す（解除したものは取消）。"""
    n = 0
    for rec in BankReconciliation.objects.filter(match_type=MatchType.TRANSFER, bank_transaction__bank_account__taxpayer=tp, bank_transaction__amount_in__gt=0,
                                                 bank_transaction__date__gte=start, bank_transaction__date__lte=end).select_related("bank_transaction__bank_account", "counter_transaction__bank_account"):
        tx, other = rec.bank_transaction, rec.counter_transaction
        if other is None:
            continue
        if rec.status == RecStatus.CONFIRMED:
            _transfer_journal(tp, tx, other, None)
        else:
            journals.upsert(tp, store=None, source_type="bank_transfer", source_id=f"{other.id}-{tx.id}", issue_date=tx.date, description="自社口座間の振替（解除）",
                            lines=[], void=True)
        n += 1
    return n


journals.register_build_hook(rebuild_transfer_journals)


@transaction.atomic
def void(rec: BankReconciliation, user, *, reason: str) -> BankReconciliation:
    rec = BankReconciliation.objects.select_for_update().get(pk=rec.pk)
    if rec.status != RecStatus.CONFIRMED:
        raise ApiError("invalid_status", "確定した照合だけ解除できます。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "解除の理由を入力してください。")
    tp = rec.bank_transaction.bank_account.taxpayer
    rec.status, rec.voided_by, rec.voided_at, rec.void_reason = RecStatus.VOIDED, user, timezone.now(), reason[:200]
    rec.save()
    if rec.match_type == MatchType.CARD_SETTLEMENT and rec.card_settlement_id:
        cs = rec.card_settlement
        if not cs.is_voided:
            cs.is_voided, cs.void_reason = True, f"照合の解除: {reason}"[:200]
            cs.save(update_fields=["is_voided", "void_reason", "updated_at"])
            journals.build(tp, cs.deposit_date, cs.deposit_date)
    elif rec.match_type == MatchType.TRANSFER and rec.counter_transaction_id:
        other = rec.counter_transaction
        BankReconciliation.objects.filter(bank_transaction=other, counter_transaction=rec.bank_transaction, status=RecStatus.CONFIRMED).update(
            status=RecStatus.VOIDED, voided_by=user, voided_at=timezone.now(), void_reason=reason[:200])
        tx = rec.bank_transaction
        src, dst = (other, tx) if tx.amount_in else (tx, other)
        journals.upsert(tp, store=None, source_type="bank_transfer", source_id=f"{src.id}-{dst.id}", issue_date=dst.date, description="自社口座間の振替（解除）", lines=[], void=True)
        other.status = TxStatus.UNMATCHED
        other.save(update_fields=["status", "updated_at"])
        _refresh_tx_status(other)
    tx = rec.bank_transaction
    tx.status = TxStatus.UNMATCHED
    tx.save(update_fields=["status", "updated_at"])
    _refresh_tx_status(tx)
    return rec


@transaction.atomic
def ignore(tx: BankTransaction, user, *, reason: str, undo: bool = False) -> BankTransaction:
    tx = BankTransaction.objects.select_for_update().get(pk=tx.pk)
    if undo:
        if tx.status != TxStatus.IGNORED:
            raise ApiError("invalid_status", "対象外の明細ではありません。", status=409)
        tx.status, tx.ignore_reason = TxStatus.UNMATCHED, ""
        tx.save(update_fields=["status", "ignore_reason", "updated_at"])
        _refresh_tx_status(tx)
        return tx
    if tx.reconciliations.filter(status=RecStatus.CONFIRMED).exists():
        raise ApiError("invalid_status", "照合済みの明細は対象外にできません。先に消込を解除してください。", status=409)
    if not (reason or "").strip():
        raise ApiError("validation_error", "対象外にする理由を入力してください（家事費・個人の支出など）。")
    tx.reconciliations.filter(status=RecStatus.SUGGESTED).delete()
    tx.status, tx.ignore_reason = TxStatus.IGNORED, reason[:200]
    tx.save(update_fields=["status", "ignore_reason", "updated_at"])
    return tx


def summary(tp, start: date, end: date) -> dict:
    qs = BankTransaction.objects.filter(bank_account__taxpayer=tp, date__gte=start, date__lte=end)
    by_status = {s: qs.filter(status=s).count() for s, _ in TxStatus.choices}
    unmatched = qs.filter(status__in=[TxStatus.UNMATCHED, TxStatus.SUGGESTED, TxStatus.PARTIAL])
    agg = unmatched.aggregate(i=Sum("amount_in"), o=Sum("amount_out"))
    accounts = []
    for acc in BankAccount.objects.filter(taxpayer=tp, is_active=True):
        last_tx = BankTransaction.objects.filter(bank_account=acc, balance__isnull=False).order_by("-date", "-row_no").first()
        from apps.accounting.ledgers import bank_book

        book = bank_book(tp, start, end, bank_account_ref=acc.id)
        accounts.append({"id": acc.id, "name": acc.name, "statement_balance": str(last_tx.balance) if last_tx else None, "statement_balance_date": last_tx.date if last_tx else None,
                         "book_balance": book["closing"], "unmatched": BankTransaction.objects.filter(bank_account=acc, status__in=["unmatched", "suggested", "partially_matched"]).count()})
    return {"from": start.isoformat(), "to": end.isoformat(), "by_status": by_status, "unmatched_count": unmatched.count(),
            "unmatched_in": str(agg["i"] or ZERO), "unmatched_out": str(agg["o"] or ZERO), "accounts": accounts}
