"""
伝票・会計のサービス層。金額の確定はすべてここ（サーバー側）で行う。
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.cash.services import require_open_business_day
from apps.core.exceptions import ApiError
from apps.core.models import Store
from apps.core.money import D, ZERO
from apps.inventory.services import record_sale, reverse_sale
from apps.staff import backs as backs_svc
from apps.staff.models import Employee

from .models import (
    Checkout, CheckoutStatus, CourseEntry, CourseEvent, LineStatus, LineType, OrderLine, OrderLineBackTarget, Payment,
    PaymentMethod, Receivable, ReceivableCollection, Refund, Visit, VisitStatus,
)
from .pricing import compute_totals, line_inputs_from_orderlines


# --- 共通 ---------------------------------------------------------------------
def allocate_number(store: Store, field: str) -> int:
    """店舗行をロックして連番を採番する（店舗内で一意）。"""
    locked = Store.objects.select_for_update().get(pk=store.pk)
    number = getattr(locked, field)
    setattr(locked, field, number + 1)
    locked.save(update_fields=[field])
    return number


def store_settings(store):
    from apps.core.models import StoreSettings

    st, _ = StoreSettings.objects.get_or_create(store=store)
    return st


def course_amount(st, course_price: Decimal, guest_count: int) -> Decimal:
    if st.price_basis == "per_person":
        return D(course_price) * Decimal(guest_count)
    return D(course_price)


def _employee_in_store(store: Store, employee_id) -> Employee | None:
    if employee_id in (None, ""):
        return None
    emp = Employee.objects.filter(id=employee_id, company=store.company).first()
    if emp is None:
        raise ApiError("validation_error", "この店舗で利用できない従業員が指定されました。")
    return emp


def _lock_visit(visit_id, store) -> Visit:
    visit = Visit.objects.select_for_update().filter(id=visit_id, store=store).first()
    if visit is None:
        raise ApiError("not_found", "伝票が見つかりません。", status=404)
    return visit


def _require_no_terminal_payment(visit: Visit, exclude_id=None, line_ids=None):
    """決済端末でカード読み取り待ちの伝票は、二重請求を防ぐため手入力の会計や、送った明細の取消をさせない。"""
    from apps.terminals.models import ACTIVE_TERMINAL_STATUSES, TerminalPayment

    qs = TerminalPayment.objects.filter(visit=visit, status__in=ACTIVE_TERMINAL_STATUSES)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    for tp in qs:
        if line_ids is None or set(int(x) for x in line_ids) & set(tp.line_ids or []):
            raise ApiError("terminal_busy", "この伝票は決済端末で処理中です。端末の結果を待つか、会計画面でキャンセルしてください。", status=409)


def _require_open(visit: Visit):
    if visit.status != VisitStatus.OPEN:
        raise ApiError("visit_closed", "この伝票は会計済み（または取消済み）のため変更できません。", status=409)


# --- 入店 ---------------------------------------------------------------------
@transaction.atomic
def open_visit(store: Store, user, data: dict) -> Visit:
    from apps.catalog.models import Course, Table

    st = store_settings(store)
    table = Table.objects.filter(store=store, id=data.get("table_id"), is_active=True).first()
    if table is None:
        raise ApiError("validation_error", "卓を選択してください。")
    entries_in = data.get("entries") or []
    if not entries_in:
        raise ApiError("validation_error", "コースを1つ以上選択してください。")
    day = require_open_business_day(store)
    now = timezone.now()
    opened_at = data.get("opened_at") or now
    primary = _employee_in_store(store, data.get("primary_employee_id"))
    customer = None
    if data.get("customer_id"):
        from apps.customers.services import get_customer_for_store

        customer = get_customer_for_store(store, data["customer_id"])
    guest_count = sum(int(e.get("guest_count") or 1) for e in entries_in)
    visit = Visit.objects.create(
        store=store, business_date=day.date, slip_number=allocate_number(store, "next_slip_number"), table=table,
        opened_at=opened_at, guest_count=guest_count, customer=customer, customer_name=(data.get("customer_name") or "")[:80],
        primary_employee=primary, note=(data.get("note") or "")[:300], created_by=user,
    )
    for e in entries_in:
        course = Course.objects.filter(store=store, id=e.get("course_id"), is_active=True).first()
        if course is None:
            raise ApiError("validation_error", "この店舗で利用できないコースが指定されました。")
        _add_entry(visit, st, course, e, user, default_started_at=opened_at)
    return visit


def _add_entry(visit: Visit, st, course, e: dict, user, default_started_at) -> CourseEntry:
    guest_count = int(e.get("guest_count") or 1)
    if guest_count < 1:
        raise ApiError("validation_error", "人数は1以上で入力してください。")
    started_at = e.get("started_at") or default_started_at
    employee = _employee_in_store(visit.store, e.get("employee_id"))
    planned_end = None if course.duration_minutes is None else started_at + timedelta(minutes=course.duration_minutes)
    entry = CourseEntry.objects.create(
        visit=visit, store=visit.store, label=(e.get("label") or "")[:60], guest_count=guest_count, course=course,
        course_name=course.name, course_price=course.price, extension_price=course.extension_price,
        extension_minutes=course.extension_minutes or 60, started_at=started_at, planned_end_at=planned_end,
        is_unlimited=course.duration_minutes is None, employee=employee, created_by=user,
    )
    amount = course_amount(st, course.price, guest_count)
    OrderLine.objects.create(
        visit=visit, store=visit.store, line_type=LineType.COURSE_SET, course_entry=entry,
        name=f"{course.name}" + (f"（{entry.label}）" if entry.label else ""), unit_price=course.price,
        quantity=Decimal(guest_count if st.price_basis == "per_person" else 1), amount=amount,
        tax_rate=course.tax_rate, tax_included=st.prices_include_tax, category_kind="course", created_by=user,
    )
    CourseEvent.objects.create(course_entry=entry, kind="start", performed_by=user, to_planned_end_at=planned_end, amount=amount)
    return entry


@transaction.atomic
def add_course_entry(store: Store, user, visit_id, data: dict) -> CourseEntry:
    """途中合流・別コースの追加。"""
    from apps.catalog.models import Course

    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    st = store_settings(store)
    course = Course.objects.filter(store=store, id=data.get("course_id"), is_active=True).first()
    if course is None:
        raise ApiError("validation_error", "この店舗で利用できないコースが指定されました。")
    entry = _add_entry(visit, st, course, data, user, default_started_at=timezone.now())
    visit.guest_count = sum(ce.guest_count for ce in visit.course_entries.filter(is_active=True))
    visit.save(update_fields=["guest_count"])
    return entry


# --- 延長・無制限へ変更 --------------------------------------------------------
@transaction.atomic
def extend_entry(store: Store, user, visit_id, entry_id) -> tuple[CourseEntry, OrderLine]:
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    entry = CourseEntry.objects.select_for_update().filter(id=entry_id, visit=visit, is_active=True).first()
    if entry is None:
        raise ApiError("not_found", "対象のコースが見つかりません。", status=404)
    if entry.is_unlimited or entry.planned_end_at is None:
        raise ApiError("not_extendable", "時間無制限のコースは延長できません。", status=409)
    st = store_settings(store)
    minutes = entry.extension_minutes or 60
    from_end = entry.planned_end_at
    # 延長は元の終了予定時刻から加算する（操作時刻ではない）
    to_end = from_end + timedelta(minutes=minutes)
    amount = course_amount(st, entry.extension_price, entry.guest_count)
    line = OrderLine.objects.create(
        visit=visit, store=store, line_type=LineType.EXTENSION, course_entry=entry,
        name=f"延長 {minutes}分" + (f"（{entry.label}）" if entry.label else ""), unit_price=entry.extension_price,
        quantity=Decimal(entry.guest_count if st.price_basis == "per_person" else 1), amount=amount,
        tax_rate=entry.course.tax_rate, tax_included=st.prices_include_tax, category_kind="course", created_by=user,
    )
    entry.planned_end_at = to_end
    entry.extension_count += 1
    entry.save(update_fields=["planned_end_at", "extension_count", "updated_at"])
    CourseEvent.objects.create(course_entry=entry, kind="extend", performed_by=user, from_planned_end_at=from_end, to_planned_end_at=to_end, amount=amount)
    return entry, line


def unlimited_change_price(st, entry: CourseEntry) -> tuple[Decimal, str]:
    """無制限への変更料金（1人あたり／1組あたりの単価）と説明。未設定なら ApiError。"""
    target = st.default_unlimited_course
    if target is None or target.store_id != st.store_id:
        raise ApiError("unlimited_course_unset", "店舗設定で「無制限へ変更」で適用するコースを設定してください。", status=409)
    if entry.extension_count > 0:
        mode = st.unlimited_change_after_ext_mode
        if mode == "unset":
            raise ApiError("unlimited_change_unset", "延長後に無制限へ変更する料金が未設定です。店舗設定で確定してください。", status=409)
        if mode == "fixed":
            if st.unlimited_change_after_ext_price is None:
                raise ApiError("unlimited_change_unset", "延長後の無制限変更料金（固定額）が未設定です。", status=409)
            return D(st.unlimited_change_after_ext_price), "延長後の変更料金（固定額）"
        if mode == "same_as_change":
            return _normal_change_price(st, entry, target)
        if mode == "difference":
            paid = D(entry.course_price) + D(entry.extension_price) * entry.extension_count
            diff = D(target.price) - paid
            return (diff if diff > 0 else ZERO), "無制限料金と支払済み（セット＋延長）との差額"
        raise ApiError("unlimited_change_unset", "延長後の無制限変更料金の設定が不正です。", status=409)
    return _normal_change_price(st, entry, target)


def _normal_change_price(st, entry: CourseEntry, target) -> tuple[Decimal, str]:
    mode = st.unlimited_change_mode
    if mode == "unset":
        raise ApiError("unlimited_change_unset", "無制限へ変更する料金が未設定です。店舗設定で確定してください（0円として処理はしません）。", status=409)
    if mode == "fixed":
        if st.unlimited_change_price is None:
            raise ApiError("unlimited_change_unset", "無制限変更料金（固定額）が未設定です。", status=409)
        return D(st.unlimited_change_price), "無制限への変更料金（固定額）"
    if mode == "difference":
        diff = D(target.price) - D(entry.course_price)
        return (diff if diff > 0 else ZERO), "無制限コース料金との差額"
    raise ApiError("unlimited_change_unset", "無制限変更料金の設定が不正です。", status=409)


@transaction.atomic
def change_to_unlimited(store: Store, user, visit_id, entry_id) -> tuple[CourseEntry, OrderLine]:
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    entry = CourseEntry.objects.select_for_update().filter(id=entry_id, visit=visit, is_active=True).first()
    if entry is None:
        raise ApiError("not_found", "対象のコースが見つかりません。", status=404)
    if entry.is_unlimited:
        raise ApiError("already_unlimited", "既に時間無制限です。", status=409)
    st = store_settings(store)
    unit_price, label = unlimited_change_price(st, entry)
    amount = course_amount(st, unit_price, entry.guest_count)
    target = st.default_unlimited_course
    line = OrderLine.objects.create(
        visit=visit, store=store, line_type=LineType.COURSE_CHANGE, course_entry=entry,
        name=f"{target.name}へ変更" + (f"（{entry.label}）" if entry.label else ""), unit_price=unit_price,
        quantity=Decimal(entry.guest_count if st.price_basis == "per_person" else 1), amount=amount,
        tax_rate=target.tax_rate, tax_included=st.prices_include_tax, category_kind="course", note=label, created_by=user,
    )
    from_end = entry.planned_end_at
    entry.is_unlimited = True
    entry.planned_end_at = None
    entry.save(update_fields=["is_unlimited", "planned_end_at", "updated_at"])
    CourseEvent.objects.create(course_entry=entry, kind="change_unlimited", performed_by=user, from_planned_end_at=from_end, to_planned_end_at=None, amount=amount, note=label)
    return entry, line


# --- 明細 ---------------------------------------------------------------------
def _parse_targets(store: Store, raw_targets, line_amount: Decimal) -> list[dict]:
    targets = []
    for t in raw_targets or []:
        emp = _employee_in_store(store, t.get("employee_id"))
        if emp is None:
            raise ApiError("validation_error", "バック対象の従業員を指定してください。")
        targets.append({"employee": emp, "share_type": t.get("share_type") or "percent", "share_value": t.get("share_value", 100)})
    return backs_svc.validate_targets(targets, line_amount)


def _save_targets(line: OrderLine, targets: list[dict]):
    line.back_targets.all().delete()
    for t in targets:
        OrderLineBackTarget.objects.create(order_line=line, employee=t["employee"], share_type=t["share_type"], share_value=t["share_value"])


@transaction.atomic
def add_product_line(store: Store, user, visit_id, data: dict) -> OrderLine:
    from apps.catalog.models import Product

    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    st = store_settings(store)
    product = Product.objects.select_related("category").filter(store=store, id=data.get("product_id"), is_active=True).first()
    if product is None:
        raise ApiError("validation_error", "この店舗で販売中の商品を選択してください。")
    qty = D(data.get("quantity") or 1)
    if qty <= 0:
        raise ApiError("validation_error", "数量は1以上で入力してください。")
    amount = D(product.price) * qty
    targets = _parse_targets(store, data.get("back_targets"), amount)
    now = timezone.now()
    line = OrderLine.objects.create(
        visit=visit, store=store, line_type=LineType.PRODUCT, product=product, name=product.name, unit_price=product.price,
        quantity=qty, amount=amount, tax_rate=product.tax_rate, tax_included=st.prices_include_tax,
        category_kind=product.category.kind, category_id_snapshot=product.category_id, note=(data.get("note") or "")[:200], created_by=user,
    )
    _save_targets(line, targets)
    record_sale(product, line, qty, now, user)
    backs_svc.create_entries_for_line(line, user=user, business_date=visit.business_date, default_timing=st.default_back_confirm_timing, now=now)
    if data.get("register_bottle_keep") and visit.customer_id and product.is_bottle_keepable:
        from apps.customers.services import create_bottle_keep_from_line

        create_bottle_keep_from_line(visit.customer, line, user, location=data.get("keep_location") or "")
    return line


@transaction.atomic
def add_fee_line(store: Store, user, visit_id, kind: str, data: dict) -> OrderLine:
    """指名・同伴。料金は店舗設定（未設定なら拒否）。"""
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    st = store_settings(store)
    if kind == "nomination":
        fee = st.nomination_fee
        label = "指名料"
        line_type = LineType.NOMINATION
    elif kind == "douhan":
        fee = st.douhan_fee
        label = "同伴料"
        line_type = LineType.DOUHAN
    else:
        raise ApiError("validation_error", "種別が不正です。")
    if fee is None:
        raise ApiError("fee_unset", f"{label}が店舗設定で未設定です。設定してから登録してください。", status=409)
    emp = _employee_in_store(store, data.get("employee_id"))
    if emp is None:
        raise ApiError("validation_error", "対象の従業員を選択してください。")
    qty = D(data.get("quantity") or 1)
    if qty <= 0:
        raise ApiError("validation_error", "数量は1以上で入力してください。")
    amount = D(fee) * qty
    line = OrderLine.objects.create(
        visit=visit, store=store, line_type=line_type, name=f"{label}（{emp.display_name}）", unit_price=fee, quantity=qty,
        amount=amount, tax_rate=st.default_tax_rate, tax_included=st.prices_include_tax, category_kind=kind, created_by=user,
    )
    _save_targets(line, [{"employee": emp, "share_type": "percent", "share_value": Decimal("100")}])
    backs_svc.create_entries_for_line(line, user=user, business_date=visit.business_date, default_timing=st.default_back_confirm_timing)
    return line


@transaction.atomic
def add_discount(store: Store, user, visit_id, data: dict) -> OrderLine:
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    st = store_settings(store)
    amount = D(data.get("amount") or 0)
    reason = (data.get("reason") or "").strip()
    if amount <= 0:
        raise ApiError("validation_error", "値引き額は1円以上で入力してください。")
    if not reason:
        raise ApiError("validation_error", "値引きの理由を入力してください。")
    return OrderLine.objects.create(
        visit=visit, store=store, line_type=LineType.DISCOUNT, name=f"値引き（{reason}）"[:120], unit_price=-amount, quantity=Decimal("1"),
        amount=-amount, tax_rate=st.default_tax_rate, tax_included=st.prices_include_tax, discount_reason=reason[:200], created_by=user,
    )


@transaction.atomic
def cancel_line(store: Store, user, visit_id, line_id, reason: str) -> OrderLine:
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    line = OrderLine.objects.select_for_update().filter(id=line_id, visit=visit).first()
    if line is None:
        raise ApiError("not_found", "明細が見つかりません。", status=404)
    if line.status == LineStatus.CANCELLED:
        raise ApiError("already_cancelled", "この明細は既に取消済みです。", status=409)
    if line.checkout_id is not None:
        raise ApiError("line_checked_out", "会計済みの明細は取り消せません。会計の取消を行ってください。", status=409)
    _require_no_terminal_payment(visit, line_ids=[line.id])
    reason = (reason or "").strip()
    if not reason:
        raise ApiError("validation_error", "取消理由を入力してください。")
    if line.line_type == LineType.COURSE_SET:
        others = visit.lines.filter(status=LineStatus.ACTIVE, course_entry=line.course_entry).exclude(id=line.id).exists()
        if others:
            raise ApiError("validation_error", "延長・変更の明細が残っています。先にそれらを取り消してください。")
    now = timezone.now()
    line.status = LineStatus.CANCELLED
    line.cancel_reason = reason[:200]
    line.cancelled_by = user
    line.cancelled_at = now
    line.save()
    if line.line_type == LineType.PRODUCT:
        reverse_sale(line, now, user, reason=f"注文取消: {reason}")
    backs_svc.reverse_entries_for_line(line, user=user, reason=f"注文取消: {reason}")
    if line.line_type == LineType.EXTENSION and line.course_entry_id:
        entry = line.course_entry
        if entry.planned_end_at is not None and entry.extension_count > 0:
            entry.planned_end_at -= timedelta(minutes=entry.extension_minutes or 60)
            entry.extension_count -= 1
            entry.save(update_fields=["planned_end_at", "extension_count"])
            CourseEvent.objects.create(course_entry=entry, kind="extend_cancel", performed_by=user, to_planned_end_at=entry.planned_end_at, note=reason)
    if line.line_type == LineType.COURSE_SET and line.course_entry_id:
        entry = line.course_entry
        entry.is_active = False
        entry.ended_at = now
        entry.save(update_fields=["is_active", "ended_at"])
        visit.guest_count = sum(ce.guest_count for ce in visit.course_entries.filter(is_active=True)) or visit.guest_count
        visit.save(update_fields=["guest_count"])
    return line


@transaction.atomic
def set_back_targets(store: Store, user, visit_id, line_id, raw_targets) -> OrderLine:
    """担当（バック対象）の変更。既存エントリは打ち消し、新しいエントリを作る。"""
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    line = OrderLine.objects.select_for_update().filter(id=line_id, visit=visit, status=LineStatus.ACTIVE).first()
    if line is None:
        raise ApiError("not_found", "明細が見つかりません。", status=404)
    if line.checkout_id is not None:
        raise ApiError("line_checked_out", "会計済みの明細の担当は変更できません。", status=409)
    if line.line_type not in (LineType.PRODUCT, LineType.NOMINATION, LineType.DOUHAN):
        raise ApiError("validation_error", "この明細はバック対象を設定できません。")
    targets = _parse_targets(store, raw_targets, line.amount)
    backs_svc.reverse_entries_for_line(line, user=user, reason="担当変更")
    _save_targets(line, targets)
    st = store_settings(store)
    backs_svc.create_entries_for_line(line, user=user, business_date=visit.business_date, default_timing=st.default_back_confirm_timing)
    return line


@transaction.atomic
def move_table(store: Store, user, visit_id, table_id) -> Visit:
    from apps.catalog.models import Table

    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    table = Table.objects.filter(store=store, id=table_id, is_active=True).first()
    if table is None:
        raise ApiError("validation_error", "移動先の卓を選択してください。")
    old = visit.table
    visit.table = table
    visit.save(update_fields=["table", "updated_at"])
    for entry in visit.course_entries.filter(is_active=True):
        CourseEvent.objects.create(course_entry=entry, kind="move_table", performed_by=user, note=f"{old.name} → {table.name}")
    return visit


@transaction.atomic
def cancel_visit(store: Store, user, visit_id, reason: str) -> Visit:
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    if visit.checkouts.filter(status=CheckoutStatus.CONFIRMED).exists():
        raise ApiError("has_checkout", "会計済みの伝票は取り消せません。会計の取消を行ってください。", status=409)
    _require_no_terminal_payment(visit)
    reason = (reason or "").strip()
    if not reason:
        raise ApiError("validation_error", "取消理由を入力してください。")
    now = timezone.now()
    for line in visit.lines.filter(status=LineStatus.ACTIVE):
        line.status = LineStatus.CANCELLED
        line.cancel_reason = f"伝票取消: {reason}"[:200]
        line.cancelled_by = user
        line.cancelled_at = now
        line.save()
        if line.line_type == LineType.PRODUCT:
            reverse_sale(line, now, user, reason=f"伝票取消: {reason}")
        backs_svc.reverse_entries_for_line(line, user=user, reason=f"伝票取消: {reason}")
    visit.course_entries.filter(is_active=True).update(is_active=False, ended_at=now)
    visit.status = VisitStatus.CANCELLED
    visit.cancel_reason = reason[:200]
    visit.closed_at = now
    visit.save()
    return visit


# --- 見積（会計前の金額表示） --------------------------------------------------
def quote(visit: Visit, line_ids=None):
    st = store_settings(visit.store)
    qs = visit.lines.filter(status=LineStatus.ACTIVE, checkout__isnull=True)
    if line_ids:
        qs = qs.filter(id__in=line_ids)
    lines = list(qs)
    return compute_totals(line_inputs_from_orderlines(lines), st), lines


# --- 会計確定 -------------------------------------------------------------------
@transaction.atomic
def confirm_checkout(store: Store, user, visit_id, data: dict, *, terminal_payment_id=None) -> Checkout:
    """
    data: {line_ids?: [..], payments: [{method, amount, tendered?, card_reference?, debtor_name?, note?}], note?, close_visit?: bool}
    terminal_payment_id: 決済端末で承認された会計を確定するときだけ（端末の処理中は手入力の会計を止める）
    """
    visit = _lock_visit(visit_id, store)
    _require_open(visit)
    _require_no_terminal_payment(visit, exclude_id=terminal_payment_id)
    st = store_settings(store)
    line_ids = data.get("line_ids")
    remaining = list(visit.lines.select_for_update().filter(status=LineStatus.ACTIVE, checkout__isnull=True))
    if line_ids:
        wanted = {int(x) for x in line_ids}
        lines = [l for l in remaining if l.id in wanted]
        if len(lines) != len(wanted):
            raise ApiError("validation_error", "選択した明細の一部が既に会計済みか取消済みです。画面を更新してください。", status=409)
        # 値引き明細だけの会計は不可
        if all(l.line_type == LineType.DISCOUNT for l in lines):
            raise ApiError("validation_error", "値引きのみの会計はできません。")
    else:
        lines = remaining
    if not lines:
        raise ApiError("validation_error", "会計対象の明細がありません。")
    is_full = len(lines) == len(remaining) and not visit.checkouts.filter(status=CheckoutStatus.CONFIRMED).exists()
    totals = compute_totals(line_inputs_from_orderlines(lines), st)
    if totals.total < 0:
        raise ApiError("validation_error", "値引きが明細合計を超えています。値引きを見直してください。")

    payments_in = data.get("payments") or []
    if not payments_in:
        raise ApiError("validation_error", "支払方法を入力してください。")
    paid_sum = ZERO
    cash_count = 0
    for p in payments_in:
        method = p.get("method")
        if method not in PaymentMethod.values:
            raise ApiError("validation_error", "支払方法が不正です。")
        amt = D(p.get("amount") or 0)
        if amt <= 0:
            raise ApiError("validation_error", "支払金額は1円以上で入力してください。")
        if method == PaymentMethod.RECEIVABLE and not st.receivable_allowed:
            raise ApiError("receivable_not_allowed", "この店舗ではツケ・未収は利用できません（店舗設定）。", status=409)
        if method == PaymentMethod.CASH:
            cash_count += 1
            tendered = p.get("tendered")
            if tendered is not None and D(tendered) < amt:
                raise ApiError("validation_error", "現金の預り額が充当額より少なくなっています。")
        paid_sum += amt
    if paid_sum != totals.total:
        raise ApiError(
            "payment_mismatch",
            f"支払合計 {paid_sum:,.0f}円 が請求金額 {totals.total:,.0f}円 と一致しません。",
            details={"paid": str(paid_sum), "total": str(totals.total)},
        )
    if cash_count > 1:
        raise ApiError("validation_error", "現金の支払いは1件にまとめてください。")

    day = require_open_business_day(store)
    now = timezone.now()
    checkout = Checkout.objects.create(
        store=store, visit=visit, number=allocate_number(store, "next_checkout_number"), kind="full" if is_full else "partial",
        business_date=day.date, confirmed_at=now, confirmed_by=user,
        subtotal=totals.subtotal, discount_total=totals.discount_total, service_charge=totals.service_charge + totals.service_charge_tax,
        tax_total=totals.tax_total, tax_breakdown=totals.tax_breakdown, total_before_rounding=totals.total_before_rounding,
        rounding_adjustment=totals.rounding_adjustment, total=totals.total, total_excl_tax=totals.total_excl_tax,
        breakdown={k: str(v) for k, v in totals.breakdown.items()}, settings_snapshot=totals.settings_snapshot,
        note=(data.get("note") or "")[:200],
    )
    OrderLine.objects.filter(id__in=[l.id for l in lines]).update(checkout=checkout)
    has_receivable = False
    for p in payments_in:
        method = p["method"]
        amt = D(p["amount"])
        tendered = D(p["tendered"]) if p.get("tendered") is not None else None
        change = (tendered - amt) if (method == PaymentMethod.CASH and tendered is not None) else ZERO
        payment = Payment.objects.create(
            checkout=checkout, store=store, business_date=day.date, method=method, amount=amt, tendered=tendered,
            change_amount=change, paid_at=now, recorded_by=user, card_reference=(p.get("card_reference") or "")[:60],
            note=(p.get("note") or "")[:200],
        )
        if method == PaymentMethod.RECEIVABLE:
            has_receivable = True
            Receivable.objects.create(
                store=store, checkout=checkout, payment=payment, customer=visit.customer, debtor_name=(p.get("debtor_name") or visit.customer_name or "")[:80],
                amount=amt, business_date=day.date, note=(p.get("note") or "")[:200],
            )

    # バック確定（値引き按分率・サービス料率を渡す）
    gross = sum((D(l.amount) for l in lines if l.line_type != LineType.DISCOUNT), ZERO)
    net = gross - totals.discount_total
    discount_ratio = (net / gross).quantize(Decimal("0.000001")) if gross > 0 else Decimal("1")
    sc_rate = D(st.service_charge_rate) if st.service_charge_enabled else ZERO
    backs_svc.settle_entries_for_checkout(checkout, lines, user=user, discount_ratio=discount_ratio, service_charge_rate=sc_rate, has_receivable=has_receivable, now=now)

    # コース終了（セット明細が会計されたら時間制アラームの対象から外す）
    entry_ids = {l.course_entry_id for l in lines if l.line_type == LineType.COURSE_SET and l.course_entry_id}
    if entry_ids:
        CourseEntry.objects.filter(id__in=entry_ids, is_active=True).update(is_active=False, ended_at=now)
        for e in CourseEntry.objects.filter(id__in=entry_ids):
            CourseEvent.objects.create(course_entry=e, kind="end", performed_by=user, note=f"会計#{checkout.number}")

    still_remaining = visit.lines.filter(status=LineStatus.ACTIVE, checkout__isnull=True).exists()
    close_visit = data.get("close_visit", True)
    if not still_remaining and close_visit:
        visit.status = VisitStatus.CLOSED
        visit.closed_at = now
        visit.save(update_fields=["status", "closed_at", "updated_at"])
        visit.course_entries.filter(is_active=True).update(is_active=False, ended_at=now)
    return checkout


# --- 取消・返金 -----------------------------------------------------------------
@transaction.atomic
def void_checkout(store: Store, user, checkout_id, reason: str, terminal_refund_done: bool = False) -> Checkout:
    """会計取消。支払いごとに返金記録を作り、明細を伝票に戻す（伝票は営業中へ戻す）。"""
    checkout = Checkout.objects.select_for_update().filter(id=checkout_id, store=store).first()
    if checkout is None:
        raise ApiError("not_found", "会計が見つかりません。", status=404)
    if checkout.status == CheckoutStatus.VOIDED:
        raise ApiError("already_voided", "この会計は既に取消済みです。", status=409)
    reason = (reason or "").strip()
    if not reason:
        raise ApiError("validation_error", "取消理由を入力してください。")
    now = timezone.now()
    day = require_open_business_day(store)
    for payment in checkout.payments.filter(status="valid"):
        if payment.method == PaymentMethod.RECEIVABLE:
            Receivable.objects.filter(payment=payment).update(status="cancelled")
        else:
            Refund.objects.create(
                checkout=checkout, store=store, payment=payment, business_date=day.date, method=payment.method, amount=payment.amount,
                reason=f"会計取消: {reason}"[:200], refunded_at=now, recorded_by=user,
                terminal_refund_done=bool(terminal_refund_done) if payment.method == PaymentMethod.CARD else True,
            )
        payment.status = "refunded"
        payment.save(update_fields=["status"])
    backs_svc.reverse_entries_for_checkout(checkout, user=user, reason=f"会計取消: {reason}")
    checkout.status = CheckoutStatus.VOIDED
    checkout.void_reason = reason[:200]
    checkout.voided_at = now
    checkout.voided_by = user
    checkout.save()
    # 明細を伝票に戻す（金額は変えない）。伝票が会計済みなら営業中へ戻す。
    OrderLine.objects.filter(checkout=checkout).update(checkout=None)
    visit = Visit.objects.select_for_update().get(pk=checkout.visit_id)
    if visit.status == VisitStatus.CLOSED:
        visit.status = VisitStatus.OPEN
        visit.closed_at = None
        visit.save(update_fields=["status", "closed_at", "updated_at"])
    return checkout


@transaction.atomic
def refund_checkout(store: Store, user, checkout_id, data: dict) -> Refund:
    """一部返金（会計は有効のまま）。金額は返金済み合計を超えられない。"""
    checkout = Checkout.objects.select_for_update().filter(id=checkout_id, store=store).first()
    if checkout is None:
        raise ApiError("not_found", "会計が見つかりません。", status=404)
    if checkout.status != CheckoutStatus.CONFIRMED:
        raise ApiError("invalid_state", "取消済みの会計には返金できません。", status=409)
    amount = D(data.get("amount") or 0)
    method = data.get("method")
    reason = (data.get("reason") or "").strip()
    if amount <= 0:
        raise ApiError("validation_error", "返金額は1円以上で入力してください。")
    if method not in (PaymentMethod.CASH, PaymentMethod.CARD):
        raise ApiError("validation_error", "返金方法は現金またはカードを指定してください。")
    if not reason:
        raise ApiError("validation_error", "返金理由を入力してください。")
    paid = sum((p.amount for p in checkout.payments.filter(status="valid", method__in=["cash", "card"])), ZERO)
    refunded = sum((r.amount for r in checkout.refunds.all()), ZERO)
    if refunded + amount > paid:
        raise ApiError("validation_error", f"返金額が支払額を超えます（支払 {paid:,.0f}円、返金済 {refunded:,.0f}円）。")
    payment = None
    if data.get("payment_id"):
        payment = checkout.payments.filter(id=data["payment_id"]).first()
    day = require_open_business_day(store)
    return Refund.objects.create(
        checkout=checkout, store=store, payment=payment, business_date=day.date, method=method, amount=amount, reason=reason[:200],
        refunded_at=timezone.now(), recorded_by=user, terminal_refund_done=bool(data.get("terminal_refund_done")) if method == "card" else True,
        note=(data.get("note") or "")[:200],
    )


@transaction.atomic
def collect_receivable(store: Store, user, receivable_id, data: dict) -> ReceivableCollection:
    rec = Receivable.objects.select_for_update().filter(id=receivable_id, store=store).first()
    if rec is None:
        raise ApiError("not_found", "未収が見つかりません。", status=404)
    if rec.status != "open":
        raise ApiError("invalid_state", "この未収は回収済みか取消済みです。", status=409)
    amount = D(data.get("amount") or 0)
    method = data.get("method") or "cash"
    if amount <= 0:
        raise ApiError("validation_error", "回収額は1円以上で入力してください。")
    if method not in ("cash", "card"):
        raise ApiError("validation_error", "回収方法は現金またはカードを指定してください。")
    remaining = rec.amount - rec.collected_amount
    if amount > remaining:
        raise ApiError("validation_error", f"回収額が残額 {remaining:,.0f}円 を超えています。")
    day = require_open_business_day(store)
    coll = ReceivableCollection.objects.create(
        receivable=rec, store=store, business_date=day.date, method=method, amount=amount, collected_at=timezone.now(), recorded_by=user,
        note=(data.get("note") or "")[:200],
    )
    rec.collected_amount += amount
    if rec.collected_amount >= rec.amount:
        rec.status = "collected"
        # この会計に他の未回収がなければ、入金完了時確定のバックを確定する
        if not Receivable.objects.filter(checkout=rec.checkout, status="open").exclude(id=rec.id).exists():
            backs_svc.confirm_payment_timing_entries(rec.checkout)
    rec.save()
    return coll
