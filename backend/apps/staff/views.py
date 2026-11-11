from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.api import StoreScopedViewSet
from apps.core.audit import audit
from apps.core.business_date import business_date_for, business_date_now, current_business_date
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.models import CompanyMembership, CompanyRole, StoreMembership, User
from apps.core.money import D, ZERO
from apps.core.permissions import (
    CAP_ATTENDANCE_MANAGE, CAP_ATTENDANCE_SELF, CAP_BACK_RULES_EDIT, CAP_BACK_RULES_VIEW, CAP_BACKS_VIEW_ALL, CAP_EMPLOYEES_EDIT,
    CAP_EMPLOYEES_VIEW, CAP_PAYROLL_PAY, CAP_PAYROLL_VIEW_ALL, CAP_PAYROLL_VIEW_SELF, CAP_WAGES_EDIT, CAP_WAGES_VIEW, StoreCaps,
)

from . import payroll
from .models import (
    BackEntry, BackRule, Employee, EmployeePayment, EmployeeStoreAssignment, PayPeriod, Shift, ShiftBreak, ShiftCorrection, WageSetting,
)
from .serializers import (
    BackEntrySerializer, BackRuleSerializer, EmployeePaymentSerializer, EmployeeSerializer, PayPeriodSerializer, PayStatementSerializer,
    ShiftSerializer, WageSettingSerializer,
)


def _employee_qs(store, include_all_company=False):
    qs = Employee.objects.filter(company=store.company).select_related("user").prefetch_related("assignments__store")
    if not include_all_company:
        qs = qs.filter(assignments__store=store).distinct()
    return qs


def _get_employee(request, employee_id) -> Employee:
    emp = _employee_qs(request.store, include_all_company=True).filter(id=employee_id).first()
    if emp is None:
        raise ApiError("not_found", "従業員が見つかりません。", status=404)
    # この店舗に配属のない従業員は、会社オーナー/管理者/運営者だけが扱える
    if not emp.assignments.filter(store=request.store).exists() and request.store_access.role not in ("owner", "admin", "operator"):
        raise ApiError("forbidden", "この店舗に配属されていない従業員です。", status=403)
    return emp


# --- 従業員 -------------------------------------------------------------------
def _shift_info(store) -> dict:
    """{employee_id: (状態, 時刻)}。出勤中は出勤時刻、退勤済みは退勤時刻。担当を選ぶ画面で「未出勤」を知らせるために使う。"""
    info: dict = {}
    bd = current_business_date(store)
    for s in Shift.objects.filter(store=store, business_date=bd, clock_out__isnull=False).order_by("clock_out"):
        info[s.employee_id] = ("left", s.clock_out)
    for s in Shift.objects.filter(store=store, clock_out__isnull=True).prefetch_related("breaks"):
        on_break = any(b.ended_at is None for b in s.breaks.all())
        info[s.employee_id] = ("on_break" if on_break else "working", s.clock_in)
    return info


class EmployeesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_EMPLOYEES_VIEW, write_caps=(CAP_EMPLOYEES_EDIT,))]

    def get(self, request):
        include_all = request.query_params.get("company") == "1" and request.store_access.role in ("owner", "admin", "operator")
        qs = _employee_qs(request.store, include_all_company=include_all)
        if request.query_params.get("active") == "1":
            qs = qs.filter(is_active=True, assignments__store=request.store, assignments__is_active=True).distinct()
        return Response(EmployeeSerializer(qs, many=True, context={"store": request.store, "shift_info": _shift_info(request.store)}).data)

    @transaction.atomic
    def post(self, request):
        ser = EmployeeSerializer(data=request.data, context={"store": request.store})
        ser.is_valid(raise_exception=True)
        emp = ser.save(company=request.store.company)
        role = request.data.get("role") or "cast"
        if role not in ("manager", "staff", "cast"):
            raise ApiError("validation_error", "役割の値が不正です。")
        EmployeeStoreAssignment.objects.create(employee=emp, store=request.store, role=role, started_on=emp.hired_on)
        audit(request, "employee.create", target=emp, changes={"display_name": emp.display_name, "role": role})
        return Response(EmployeeSerializer(_employee_qs(request.store).get(pk=emp.pk), context={"store": request.store}).data, status=201)


class EmployeeDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_EMPLOYEES_VIEW, write_caps=(CAP_EMPLOYEES_EDIT,))]

    def get(self, request, employee_id):
        emp = _get_employee(request, employee_id)
        return Response(EmployeeSerializer(emp, context={"store": request.store}).data)

    @transaction.atomic
    def patch(self, request, employee_id):
        emp = _get_employee(request, employee_id)
        ser = EmployeeSerializer(emp, data=request.data, partial=True, context={"store": request.store})
        ser.is_valid(raise_exception=True)
        ser.save()
        if "is_active" in request.data and not emp.is_active:
            # 退職: ログインを無効化し、配属も停止する（過去データは残す）
            emp.assignments.update(is_active=False)
            if emp.user_id:
                emp.user.is_active = False
                emp.user.save(update_fields=["is_active"])
                StoreMembership.objects.filter(user=emp.user).update(is_active=False)
        audit(request, "employee.update", target=emp, changes={k: v for k, v in request.data.items() if k != "note"})
        return Response(EmployeeSerializer(_employee_qs(request.store, True).get(pk=emp.pk), context={"store": request.store}).data)


class EmployeeAssignmentView(APIView):
    """この店舗への配属（役割・有効）を設定する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_EMPLOYEES_EDIT)]

    @transaction.atomic
    def put(self, request, employee_id):
        emp = Employee.objects.filter(id=employee_id, company=request.store.company).first()
        if emp is None:
            raise ApiError("not_found", "従業員が見つかりません。", status=404)
        role = request.data.get("role") or "cast"
        if role not in ("manager", "staff", "cast"):
            raise ApiError("validation_error", "役割の値が不正です。")
        is_active = bool(request.data.get("is_active", True))
        a, _ = EmployeeStoreAssignment.objects.update_or_create(
            employee=emp, store=request.store, defaults={"role": role, "is_active": is_active, "ended_on": None if is_active else timezone.localdate()}
        )
        if emp.user_id:
            StoreMembership.objects.update_or_create(user=emp.user, store=request.store, defaults={"role": role, "is_active": is_active and emp.user.is_active})
        audit(request, "employee.assignment", target=emp, changes={"role": role, "is_active": is_active})
        return Response(EmployeeSerializer(_employee_qs(request.store, True).get(pk=emp.pk), context={"store": request.store}).data)


class EmployeeLoginAccountView(APIView):
    """従業員のログインアカウント作成・有効化・無効化（在籍情報とは分離）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_EMPLOYEES_EDIT, reauth=True)]

    @transaction.atomic
    def post(self, request, employee_id):
        emp = _get_employee(request, employee_id)
        enabled = bool(request.data.get("enabled", True))
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if emp.user_id is None:
            if not enabled:
                return Response(EmployeeSerializer(emp, context={"store": request.store}).data)
            if not username or not password:
                raise ApiError("validation_error", "ユーザー名とパスワードを入力してください。")
            if User.objects.filter(username=username).exists():
                raise ApiError("validation_error", "このユーザー名は既に使われています。")
            try:
                validate_password(password)
            except DjangoValidationError as e:
                raise ApiError("validation_error", "、".join(e.messages))
            user = User.objects.create_user(username=username, password=password, display_name=emp.display_name)
            emp.user = user
            emp.save(update_fields=["user"])
            CompanyMembership.objects.get_or_create(user=user, company=emp.company, defaults={"role": CompanyRole.MEMBER})
        else:
            user = emp.user
            if password:
                try:
                    validate_password(password)
                except DjangoValidationError as e:
                    raise ApiError("validation_error", "、".join(e.messages))
                user.set_password(password)
            user.is_active = enabled
            user.save()
        for a in emp.assignments.all():
            StoreMembership.objects.update_or_create(user=user, store=a.store, defaults={"role": a.role, "is_active": a.is_active and enabled})
        audit(request, "employee.login_account", target=emp, changes={"enabled": enabled, "username": user.username})
        return Response(EmployeeSerializer(_employee_qs(request.store, True).get(pk=emp.pk), context={"store": request.store}).data)


class EmployeeWagesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_WAGES_VIEW, write_caps=(CAP_WAGES_EDIT,), reauth=True)]

    def get(self, request, employee_id):
        emp = _get_employee(request, employee_id)
        qs = WageSetting.objects.filter(employee=emp, store=request.store)
        return Response({"current": str(payroll.wage_for(emp, request.store, current_business_date(request.store)) or ""),
                         "history": WageSettingSerializer(qs, many=True).data})

    @transaction.atomic
    def post(self, request, employee_id):
        emp = _get_employee(request, employee_id)
        ser = WageSettingSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if ser.validated_data["hourly_wage"] < 0:
            raise ApiError("validation_error", "時給は0以上で入力してください。")
        ws = ser.save(employee=emp, store=request.store, created_by=request.user)
        audit(request, "wage.set", target=emp, changes={"hourly_wage": ws.hourly_wage, "effective_from": ws.effective_from})
        return Response(WageSettingSerializer(ws).data, status=201)


# --- バック設定・集計 -----------------------------------------------------------
class BackRuleViewSet(StoreScopedViewSet):
    model = BackRule
    serializer_class = BackRuleSerializer
    caps_read = (CAP_BACK_RULES_VIEW,)
    caps_write = (CAP_BACK_RULES_EDIT,)

    def get_queryset(self):
        qs = super().get_queryset().select_related("employee", "category", "product")
        emp = self.request.query_params.get("employee")
        if emp == "common":
            qs = qs.filter(employee__isnull=True)
        elif emp:
            qs = qs.filter(employee_id=emp)
        return qs

    def perform_create(self, serializer):
        super().perform_create(serializer)
        audit(self.request, "back_rule.create", target=serializer.instance, changes=serializer.data)

    def perform_update(self, serializer):
        serializer.save()
        audit(self.request, "back_rule.update", target=serializer.instance, changes=serializer.data)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        audit(request, "back_rule.deactivate", target=obj)
        return Response(status=204)


class BackEntriesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_BACKS_VIEW_ALL)]

    def get(self, request):
        f = parse_date(request.query_params.get("from") or "") or current_business_date(request.store)
        t = parse_date(request.query_params.get("to") or "") or f
        qs = BackEntry.objects.filter(store=request.store, business_date__gte=f, business_date__lte=t).select_related("employee", "visit", "order_line")
        if request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        entries = list(qs[:1000])
        by_emp: dict[int, dict] = {}
        for e in entries:
            row = by_emp.setdefault(e.employee_id, {"employee_id": e.employee_id, "employee_name": e.employee.display_name, "confirmed": ZERO, "pending": ZERO, "count": 0})
            if e.status == "confirmed":
                row["confirmed"] += e.amount
            elif e.status == "pending":
                row["pending"] += e.amount
            row["count"] += 1
        summary = [{**r, "confirmed": str(r["confirmed"]), "pending": str(r["pending"])} for r in by_emp.values()]
        return Response({"from": f, "to": t, "summary": summary, "entries": BackEntrySerializer(entries, many=True).data})


class BackAdjustmentView(APIView):
    """バックの手動調整（理由必須・履歴として残す）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_BACK_RULES_EDIT)]

    @idempotent
    def post(self, request):
        emp = Employee.objects.filter(id=request.data.get("employee_id"), company=request.store.company).first()
        if emp is None:
            raise ApiError("validation_error", "従業員を指定してください。")
        amount = D(request.data.get("amount") or 0)
        reason = (request.data.get("reason") or "").strip()
        if amount == 0 or not reason:
            raise ApiError("validation_error", "金額（0以外）と理由を入力してください。")
        bd = parse_date(request.data.get("business_date") or "") or current_business_date(request.store)
        entry = BackEntry.objects.create(
            store=request.store, employee=emp, kind="adjustment", business_date=bd, amount=amount, status="confirmed",
            confirm_timing="order", confirmed_at=timezone.now(), reason=reason[:200], created_by=request.user,
        )
        audit(request, "back.adjust", target=entry, changes={"employee": emp.display_name, "amount": amount, "reason": reason})
        return Response(BackEntrySerializer(entry).data, status=201)


# --- 勤怠 ---------------------------------------------------------------------
def _own_employee(request) -> Employee | None:
    emp = getattr(request.user, "employee", None)
    if emp is not None and emp.company_id == request.store.company_id:
        return emp
    return None


def _shift_target(request, employee_id) -> Employee:
    own = _own_employee(request)
    if employee_id in (None, "", "me"):
        if own is None:
            raise ApiError("validation_error", "ログインユーザーに従業員情報が紐付いていません。")
        return own
    emp = Employee.objects.filter(id=employee_id, company=request.store.company).first()
    if emp is None:
        raise ApiError("validation_error", "従業員が見つかりません。")
    if own is not None and emp.id == own.id:
        return emp
    if CAP_ATTENDANCE_MANAGE not in request.store_access.caps:
        raise ApiError("forbidden", "他の従業員の勤怠を操作する権限がありません。", status=403)
    return emp


def _shift_snapshot(shift: Shift) -> dict:
    return {
        "clock_in": shift.clock_in.isoformat(), "clock_out": shift.clock_out.isoformat() if shift.clock_out else None,
        "business_date": shift.business_date.isoformat(), "note": shift.note,
        "breaks": [{"started_at": b.started_at.isoformat(), "ended_at": b.ended_at.isoformat() if b.ended_at else None} for b in shift.breaks.all()],
    }


class ShiftsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_SELF)]

    def get(self, request):
        f = parse_date(request.query_params.get("from") or "") or current_business_date(request.store)
        t = parse_date(request.query_params.get("to") or "") or f
        qs = Shift.objects.filter(store=request.store, business_date__gte=f, business_date__lte=t).select_related("employee").prefetch_related("breaks", "corrections")
        if CAP_ATTENDANCE_MANAGE not in request.store_access.caps:
            own = _own_employee(request)
            qs = qs.filter(employee=own) if own else qs.none()
        elif request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        open_shifts = Shift.objects.filter(store=request.store, clock_out__isnull=True).select_related("employee").prefetch_related("breaks")
        if CAP_ATTENDANCE_MANAGE not in request.store_access.caps:
            own = _own_employee(request)
            open_shifts = open_shifts.filter(employee=own) if own else open_shifts.none()
        return Response({"from": f, "to": t, "shifts": ShiftSerializer(qs, many=True).data, "open_shifts": ShiftSerializer(open_shifts, many=True).data})

    @idempotent
    @transaction.atomic
    def post(self, request):
        """手動登録（管理者）: {employee_id, clock_in, clock_out?, note}"""
        if CAP_ATTENDANCE_MANAGE not in request.store_access.caps:
            raise ApiError("forbidden", "勤怠の手動登録には権限が必要です。", status=403)
        emp = _shift_target(request, request.data.get("employee_id"))
        clock_in = parse_datetime(request.data.get("clock_in") or "")
        if clock_in is None:
            raise ApiError("validation_error", "出勤時刻を入力してください。")
        clock_out = parse_datetime(request.data.get("clock_out") or "") if request.data.get("clock_out") else None
        if clock_out and clock_out <= clock_in:
            raise ApiError("validation_error", "退勤時刻は出勤時刻より後にしてください。")
        from apps.core.business_date import business_date_for, store_tz

        st = request.store.settings
        shift = Shift.objects.create(
            employee=emp, store=request.store, business_date=business_date_for(clock_in, st.business_day_switch_time, store_tz(request.store)),
            clock_in=clock_in, clock_out=clock_out, note=(request.data.get("note") or "")[:200], created_by=request.user,
        )
        audit(request, "shift.create", target=shift, changes={"employee": emp.display_name})
        return Response(ShiftSerializer(shift).data, status=201)


class ClockInView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_SELF)]

    @idempotent
    @transaction.atomic
    def post(self, request):
        emp = _shift_target(request, request.data.get("employee_id"))
        if Shift.objects.filter(employee=emp, clock_out__isnull=True).exists():
            raise ApiError("already_clocked_in", "既に出勤中です。", status=409)
        now = timezone.now()
        shift = Shift.objects.create(employee=emp, store=request.store, business_date=current_business_date(request.store), clock_in=now, created_by=request.user)
        audit(request, "shift.clock_in", target=shift, changes={"employee": emp.display_name})
        return Response(ShiftSerializer(shift).data, status=201)


class ClockOutView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_SELF)]

    @idempotent
    @transaction.atomic
    def post(self, request, shift_id):
        shift = Shift.objects.select_for_update().filter(id=shift_id, store=request.store).first()
        if shift is None:
            raise ApiError("not_found", "勤怠が見つかりません。", status=404)
        _shift_target(request, shift.employee_id)
        if shift.clock_out is not None:
            raise ApiError("already_clocked_out", "既に退勤済みです。", status=409)
        now = timezone.now()
        shift.breaks.filter(ended_at__isnull=True).update(ended_at=now)
        shift.clock_out = now
        shift.save(update_fields=["clock_out", "updated_at"])
        audit(request, "shift.clock_out", target=shift)
        return Response(ShiftSerializer(shift).data)


class BreakView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_SELF)]

    @idempotent
    @transaction.atomic
    def post(self, request, shift_id):
        shift = Shift.objects.select_for_update().filter(id=shift_id, store=request.store).first()
        if shift is None:
            raise ApiError("not_found", "勤怠が見つかりません。", status=404)
        _shift_target(request, shift.employee_id)
        if shift.clock_out is not None:
            raise ApiError("already_clocked_out", "退勤済みの勤怠です。", status=409)
        now = timezone.now()
        open_break = shift.breaks.filter(ended_at__isnull=True).first()
        action = request.data.get("action") or ("end" if open_break else "start")
        if action == "start":
            if open_break:
                raise ApiError("break_open", "既に休憩中です。", status=409)
            ShiftBreak.objects.create(shift=shift, started_at=now)
        else:
            if not open_break:
                raise ApiError("no_break", "休憩中ではありません。", status=409)
            open_break.ended_at = now
            open_break.save(update_fields=["ended_at"])
        audit(request, f"shift.break_{action}", target=shift)
        return Response(ShiftSerializer(shift).data)


class ShiftCorrectView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_MANAGE)]

    @transaction.atomic
    def post(self, request, shift_id):
        shift = Shift.objects.select_for_update().filter(id=shift_id, store=request.store).prefetch_related("breaks").first()
        if shift is None:
            raise ApiError("not_found", "勤怠が見つかりません。", status=404)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "修正理由を入力してください。")
        before = _shift_snapshot(shift)
        if request.data.get("clock_in"):
            ci = parse_datetime(request.data["clock_in"])
            if ci is None:
                raise ApiError("validation_error", "出勤時刻の形式が正しくありません。")
            shift.clock_in = ci
        if "clock_out" in request.data:
            co = parse_datetime(request.data["clock_out"]) if request.data["clock_out"] else None
            shift.clock_out = co
        if shift.clock_out and shift.clock_out <= shift.clock_in:
            raise ApiError("validation_error", "退勤時刻は出勤時刻より後にしてください。")
        if "breaks" in request.data:
            shift.breaks.all().delete()
            for b in request.data.get("breaks") or []:
                s = parse_datetime(b.get("started_at") or "")
                e = parse_datetime(b.get("ended_at") or "") if b.get("ended_at") else None
                if s is None:
                    raise ApiError("validation_error", "休憩開始時刻の形式が正しくありません。")
                ShiftBreak.objects.create(shift=shift, started_at=s, ended_at=e)
        if "note" in request.data:
            shift.note = (request.data.get("note") or "")[:200]
        from apps.core.business_date import business_date_for, store_tz

        shift.business_date = business_date_for(shift.clock_in, request.store.settings.business_day_switch_time, store_tz(request.store))
        shift.save()
        ShiftCorrection.objects.create(shift=shift, before=before, after=_shift_snapshot(shift), reason=reason[:200], corrected_by=request.user)
        audit(request, "shift.correct", target=shift, changes={"reason": reason})
        return Response(ShiftSerializer(Shift.objects.prefetch_related("breaks", "corrections").get(pk=shift.pk)).data)


# --- 支払い・締め -----------------------------------------------------------------
class EmployeePaymentsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_VIEW_ALL, write_caps=(CAP_PAYROLL_PAY,), reauth=True)]

    def get(self, request):
        f = parse_date(request.query_params.get("from") or "") or (current_business_date(request.store) - timedelta(days=30))
        t = parse_date(request.query_params.get("to") or "") or current_business_date(request.store)
        qs = EmployeePayment.objects.filter(store=request.store, business_date__gte=f, business_date__lte=t).select_related("employee")
        if request.query_params.get("employee"):
            qs = qs.filter(employee_id=request.query_params["employee"])
        return Response(EmployeePaymentSerializer(qs, many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        """
        レジ現金以外の支払い（振込など）。レジ現金からの日払いは /staff/daily-payouts/pay を使う。
        amount_basis="gross": amount を報酬額として、ホステス報酬（確認済み）は源泉所得税を差し引いた額を支払う。
        既定（"net"）: amount は渡した額。給与は withholding_amount（給与計算ソフトで計算した額）を一緒に記録できる。
        """
        from apps.accounting import services as accounting

        emp = Employee.objects.filter(id=request.data.get("employee_id"), company=request.store.company).first()
        if emp is None:
            raise ApiError("validation_error", "従業員を指定してください。")
        amount = D(request.data.get("amount") or 0)
        if amount <= 0:
            raise ApiError("validation_error", "金額は1円以上で入力してください。")
        method = request.data.get("method") or "bank"
        if method == "cash":
            raise ApiError("validation_error", "レジ現金からの支払いは「現金管理」の日払い出金から登録してください（二重控除防止）。")
        kind = request.data.get("kind") or "settlement"
        pay_period = None
        if request.data.get("pay_period_id"):
            pay_period = PayPeriod.objects.filter(id=request.data["pay_period_id"], store=request.store).first()
        business_date = parse_date(request.data.get("business_date") or "") or current_business_date(request.store)
        basis = request.data.get("amount_basis") or "net"
        if basis not in ("net", "gross"):
            raise ApiError("validation_error", "amount_basis は net か gross を指定してください。")
        period = None
        ps, pe = parse_date(request.data.get("period_start") or ""), parse_date(request.data.get("period_end") or "")
        if ps and pe:
            if pe < ps:
                raise ApiError("validation_error", "報酬計算期間の終了日は開始日以降にしてください。")
            period = (ps, pe)
        elif pay_period is not None:
            period = (pay_period.start_date, pay_period.end_date)
        manual_w = D(request.data["withholding_amount"]) if request.data.get("withholding_amount") not in (None, "") else None
        if manual_w is not None and manual_w < 0:
            raise ApiError("validation_error", "源泉徴収額は0以上で入力してください。")
        now = timezone.now()
        st = accounting.settle_simple(request.store, emp, amount=amount, on_date=period[1] if period else business_date, paid_on=timezone.localdate(now),
                                      mode=basis, period=period, manual_withholding=manual_w)
        ep = EmployeePayment.objects.create(
            employee=emp, store=request.store, kind=kind, amount=st.net, withholding_amount=st.withholding, method=method, paid_at=now,
            business_date=business_date, pay_period=pay_period, recorded_by=request.user, note=(request.data.get("note") or "")[:200],
        )
        rec = accounting.record_withholding(ep, st, work_date=None, user=request.user)
        accounting.record_salary_entry_for_payment(ep, st, request.user)
        audit(request, "employee_payment.create", target=ep, changes={
            "employee": emp.display_name, "amount": st.net, "method": method, "gross": st.gross, "withholding": st.withholding,
            "withholding_status": rec.status if rec else st.status,
        })
        return Response(EmployeePaymentSerializer(ep).data, status=201)


class StatementsView(APIView):
    """期間別・個人別の支払集計（締め前のプレビュー）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_VIEW_ALL, reauth=True)]

    def get(self, request):
        f = parse_date(request.query_params.get("from") or "")
        t = parse_date(request.query_params.get("to") or "")
        if not f or not t:
            raise ApiError("validation_error", "期間（from, to）を指定してください。")
        qs = Employee.objects.filter(company=request.store.company, assignments__store=request.store).distinct()
        if request.query_params.get("employee"):
            qs = qs.filter(id=request.query_params["employee"])
        return Response([payroll.compute_statement(request.store, emp, f, t) for emp in qs])


class PayPeriodsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_VIEW_ALL, write_caps=(CAP_PAYROLL_PAY,), reauth=True)]

    def get(self, request):
        return Response(PayPeriodSerializer(PayPeriod.objects.filter(store=request.store).prefetch_related("statements__employee"), many=True).data)

    def post(self, request):
        ser = PayPeriodSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if ser.validated_data["end_date"] < ser.validated_data["start_date"]:
            raise ApiError("validation_error", "終了日は開始日以降にしてください。")
        if PayPeriod.objects.filter(store=request.store, start_date=ser.validated_data["start_date"], end_date=ser.validated_data["end_date"]).exists():
            raise ApiError("validation_error", "同じ期間の締めが既にあります。")
        pp = ser.save(store=request.store)
        audit(request, "pay_period.create", target=pp)
        return Response(PayPeriodSerializer(pp).data, status=201)


class PayPeriodDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_VIEW_ALL, reauth=True)]

    def get(self, request, period_id):
        pp = PayPeriod.objects.filter(id=period_id, store=request.store).prefetch_related("statements__employee").first()
        if pp is None:
            raise ApiError("not_found", "締め期間が見つかりません。", status=404)
        data = PayPeriodSerializer(pp).data
        if pp.status == "open":
            emps = Employee.objects.filter(company=request.store.company, assignments__store=request.store).distinct()
            data["preview"] = [payroll.compute_statement(request.store, e, pp.start_date, pp.end_date, pp) for e in emps]
        return Response(data)


class PayPeriodCloseView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_PAY, reauth=True)]

    @idempotent
    @transaction.atomic
    def post(self, request, period_id):
        pp = PayPeriod.objects.select_for_update().filter(id=period_id, store=request.store).first()
        if pp is None:
            raise ApiError("not_found", "締め期間が見つかりません。", status=404)
        if pp.status == "closed":
            raise ApiError("already_closed", "既に締め済みです。", status=409)
        emps = Employee.objects.filter(company=request.store.company, assignments__store=request.store).distinct()
        payroll.close_pay_period(pp, request.user, emps)
        audit(request, "pay_period.close", target=pp)
        return Response(PayPeriodSerializer(PayPeriod.objects.prefetch_related("statements__employee").get(pk=pp.pk)).data)


def _payout_date(request):
    """日払いの対象営業日: 指定がなければ営業中の営業日（なければ今日の営業日）。"""
    from apps.cash.services import current_open_day

    d = parse_date(request.query_params.get("date") or request.data.get("work_date") or "") if hasattr(request, "data") else None
    if d:
        return d
    day = current_open_day(request.store)
    return day.date if day else current_business_date(request.store)


class DailyPayoutsView(APIView):
    """その日の出勤者ごとのお渡し額（時給×勤務時間＋確定バック）と、渡し済み／未渡し。"""

    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_PAY, reauth=False)]

    def get(self, request):
        work_date = _payout_date(request)
        include_rate = CAP_WAGES_VIEW in request.store_access.caps
        rows = [payroll.daily_payout(request.store, e, work_date, include_rate=include_rate) for e in payroll.daily_payout_employees(request.store, work_date)]
        order = {"working": 0, "ready": 1, "partial": 1, "missing_wage": 2, "paid": 3, "nothing": 4, "absent": 5}
        rows.sort(key=lambda r: (order.get(r["status"], 9), r["employee_name"]))
        due = [r for r in rows if r["status"] in ("ready", "partial")]
        from apps.accounting.compensation import withholding_enabled

        withholding = withholding_enabled(request.store)
        return Response({
            "work_date": work_date.isoformat(),
            "rows": rows,
            "totals": {
                "gross": str(sum((D(r["gross_amount"]) for r in rows), ZERO)),
                "paid": str(sum((D(r["paid_amount"]) for r in rows), ZERO)),
                "withheld": str(sum((D(r["withheld_amount"]) for r in rows), ZERO)),
                "deducted": str(sum((D(r["deducted_amount"]) for r in rows), ZERO)),
                "remaining": str(sum((D(r["remaining_amount"]) for r in due), ZERO)),
                "unpaid_count": len(due),
                "working_count": sum(1 for r in rows if r["working"]),
                "tax_review_count": sum(1 for r in rows if r.get("tax") and r["tax"]["status"] == "needs_review"),
            },
            "withholding_enabled": withholding,
            "notice": "報酬は時給×勤務時間（休憩除く）＋その日の確定バックです。" + (
                "ホステス報酬と確認済みの人は源泉所得税を差し引いて渡します（預り金として記録）。" if withholding else "源泉所得税は差し引いていません（店舗設定）。"
            ),
        })


class DailyPayoutPayView(APIView):
    """
    「渡した」: レジ現金からの出金（日払い）と従業員への支払記録を同時に1件作る。
    expected_amount（画面に出ていた未渡し額）と現在の額が違えば拒否し、古い画面での二重払いを防ぐ。
    """

    def get_permissions(self):
        return [StoreCaps(CAP_PAYROLL_PAY, reauth=False)]

    @idempotent
    @transaction.atomic
    def post(self, request):
        from apps.cash.models import CashMovement, CashMovementKind
        from apps.cash.services import require_open_business_day

        store = request.store
        emp = Employee.objects.select_for_update().filter(id=request.data.get("employee_id"), company=store.company).first()
        if emp is None:
            raise ApiError("validation_error", "従業員が見つかりません。")
        work_date = parse_date(request.data.get("work_date") or "") or _payout_date(request)
        # チェックした店舗の控除項目（備品代など）。送られなければ「最初からチェック」の項目
        selected = request.data.get("deductions")
        if selected is not None and not (isinstance(selected, list) and all(isinstance(c, str) for c in selected)):
            raise ApiError("validation_error", "deductions は控除項目のコードの配列で指定してください。")
        info = payroll.daily_payout(store, emp, work_date, selected_deductions=selected)
        if info["status"] == "absent":
            raise ApiError("validation_error", f"{emp.display_name}さんはこの日の勤務がありません。")
        if info["working"]:
            raise ApiError("still_working", f"{emp.display_name}さんはまだ退勤していません。退勤してから渡してください。", status=409)
        if info["missing_wage_setting"]:
            raise ApiError("missing_wage", f"{emp.display_name}さんの時給が未設定です。「従業員管理」で時給を設定してください。", status=409)
        remaining = D(info["remaining_amount"])
        if D(info["remaining_gross"]) <= 0 or remaining <= 0:
            raise ApiError("nothing_to_pay", f"{emp.display_name}さんに渡す金額はありません（渡し済み）。", status=409)
        expected = request.data.get("expected_amount")
        if expected is not None and D(expected) != remaining:
            raise ApiError(
                "amount_changed",
                f"お渡し額が変わりました（いまは ¥{remaining:,.0f}）。画面を更新して確認してください。",
                status=409, details={"remaining_amount": str(remaining)},
            )
        custom = D(request.data["amount"]) if request.data.get("amount") not in (None, "") else None
        if custom is not None and custom <= 0:
            raise ApiError("validation_error", "金額は1円以上で入力してください。")
        if custom is not None and custom > remaining:
            raise ApiError("validation_error", f"未渡しの額（¥{remaining:,.0f}）より多くは渡せません。")
        day = require_open_business_day(store)
        now = timezone.now()
        from apps.accounting import services as accounting

        # 源泉所得税・その他控除を事業者単位のロックの中で計算し直し、手渡し額（差引支給額）を決める
        st = accounting.settle_daily_payment(store, emp, work_date, info, cash_amount=custom, paid_on=timezone.localdate(now), selected=selected)
        amount = st.net
        mv = CashMovement.objects.create(
            store=store, business_day=day, kind=CashMovementKind.DAILY_PAY, amount=amount, employee=emp,
            reason=f"日払い {emp.display_name}（{work_date.isoformat()} 勤務分）"[:200], occurred_at=now, performed_by=request.user,
        )
        ep = EmployeePayment.objects.create(
            employee=emp, store=store, kind="daily", amount=amount, withholding_amount=st.withholding, deduction_amount=st.other_deduction,
            deduction_items=st.deduction_rows, method="cash", paid_at=now, business_date=day.date, work_date=work_date, cash_movement=mv, recorded_by=request.user,
            note=(request.data.get("note") or "")[:200],
        )
        rec = accounting.record_withholding(ep, st, work_date=work_date, user=request.user)
        audit(request, "payout.daily", target=ep, changes={
            "employee": emp.display_name, "work_date": work_date, "amount": amount, "cash_movement_id": mv.id, "gross": st.gross,
            "withholding": st.withholding, "other_deduction": st.other_deduction, "deductions": [f"{d['label']} {d['amount']}" for d in st.deduction_rows],
            "withholding_status": rec.status if rec else st.status, "withholding_record_id": rec.id if rec else None,
        })
        return Response({
            "payout": payroll.daily_payout(store, emp, work_date, include_rate=CAP_WAGES_VIEW in request.store_access.caps),
            "cash_movement_id": mv.id,
            "payment_id": ep.id,
            "withholding_record_id": rec.id if rec else None,
            "amount": str(amount),
            "gross_amount": str(st.gross),
            "withholding_amount": str(st.withholding),
            "other_deduction_amount": str(st.other_deduction),
            "other_deductions": st.deduction_rows,
            "withholding_status": rec.status if rec else st.status,
        }, status=201)


class MyStaffView(APIView):
    """キャスト等が自分の勤怠・成績・バックを確認する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_ATTENDANCE_SELF)]

    def get(self, request):
        emp = _own_employee(request)
        if emp is None:
            return Response({"employee": None, "message": "ログインユーザーに従業員情報が紐付いていません。"})
        today = current_business_date(request.store)
        f = parse_date(request.query_params.get("from") or "") or today.replace(day=1)
        t = parse_date(request.query_params.get("to") or "") or today
        shifts = Shift.objects.filter(store=request.store, employee=emp, business_date__gte=f, business_date__lte=t).prefetch_related("breaks", "corrections")
        backs = BackEntry.objects.filter(store=request.store, employee=emp, business_date__gte=f, business_date__lte=t).select_related("visit", "order_line")
        open_shift = Shift.objects.filter(store=request.store, employee=emp, clock_out__isnull=True).prefetch_related("breaks").first()
        data = {
            "employee": EmployeeSerializer(emp, context={"store": request.store}).data,
            "from": f, "to": t,
            "open_shift": ShiftSerializer(open_shift).data if open_shift else None,
            "shifts": ShiftSerializer(shifts, many=True).data,
            "backs": BackEntrySerializer(backs, many=True).data,
            "back_total_confirmed": str(backs.filter(status="confirmed").aggregate(s=Sum("amount"))["s"] or ZERO),
        }
        if CAP_PAYROLL_VIEW_SELF in request.store_access.caps:
            data["statement"] = payroll.compute_statement(request.store, emp, f, t)
            from apps.cash.services import current_open_day

            open_day = current_open_day(request.store)
            data["today_payout"] = payroll.daily_payout(request.store, emp, open_day.date if open_day else today)
        return Response(data)
