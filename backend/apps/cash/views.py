from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.business_date import business_date_now
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.money import D
from apps.core.permissions import CAP_CLOSING, CAP_CLOSING_EDIT, CAP_PAYROLL_PAY, StoreCaps
from apps.staff.models import Employee, EmployeePayment

from . import services
from .models import BankDeposit, BusinessDay, CashMovement, CashMovementKind, ClosingRevision


class CashMovementSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)
    employee_name = serializers.CharField(source="employee.display_name", read_only=True, default=None)
    performed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CashMovement
        fields = ["id", "business_day", "kind", "kind_label", "amount", "reason", "employee", "employee_name", "occurred_at", "performed_by_name",
                  "is_voided", "void_reason"]

    def get_performed_by_name(self, obj):
        return str(obj.performed_by) if obj.performed_by else ""


class BankDepositSerializer(serializers.ModelSerializer):
    class Meta:
        model = BankDeposit
        fields = ["id", "business_day", "amount", "deposited_at", "note"]


class ClosingRevisionSerializer(serializers.ModelSerializer):
    changed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ClosingRevision
        fields = ["id", "changed_by_name", "changed_at", "reason", "before", "after"]

    def get_changed_by_name(self, obj):
        return str(obj.changed_by) if obj.changed_by else ""


class BusinessDaySerializer(serializers.ModelSerializer):
    opened_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()
    revisions = ClosingRevisionSerializer(many=True, read_only=True)

    class Meta:
        model = BusinessDay
        exclude = ["store", "updated_at"]

    def get_opened_by_name(self, obj):
        return str(obj.opened_by) if obj.opened_by else ""

    def get_closed_by_name(self, obj):
        return str(obj.closed_by) if obj.closed_by else ""


def _day_payload(day: BusinessDay) -> dict:
    data = BusinessDaySerializer(day).data
    data["summary"] = services.day_summary(day)
    data["precheck"] = services.precheck(day) if day.status == "open" else []
    data["open_visits"] = services.open_visits_for(day.store) if day.status == "open" else []
    data["movements"] = CashMovementSerializer(day.movements.select_related("employee", "performed_by").order_by("-occurred_at"), many=True).data
    data["bank_deposits"] = BankDepositSerializer(day.bank_deposits.all(), many=True).data
    return data


class CurrentBusinessDayView(APIView):
    """営業中の営業日（なければ null と本日の営業日・引継ぎ候補の釣銭）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    def get(self, request):
        store = request.store
        day = services.current_open_day(store)
        today = business_date_now(store)
        suggested = None
        carried = False
        # 直近の締め済み営業日の「翌日釣銭準備金」を引継ぎ候補にする（当日分が締め済みでも翌営業日に引き継ぐ）
        prev = BusinessDay.objects.filter(store=store, status="closed").order_by("-date").first()
        if prev is not None and prev.next_day_float is not None:
            suggested = str(prev.next_day_float)
            carried = True
        else:
            st = getattr(store, "settings", None)
            suggested = str(st.default_opening_float) if st else "0"
        return Response({
            "business_date_today": today.isoformat(),
            "day": _day_payload(day) if day else None,
            # 締め直後に結果を確認できるよう、営業中の営業日がないときは直近の締め済み営業日も返す
            "last_closed_day": _day_payload(prev) if (day is None and prev is not None) else None,
            "suggested_opening_float": suggested,
            "suggested_from_previous_day": carried,
            "previous_day": {"date": prev.date.isoformat(), "next_day_float": str(prev.next_day_float)} if prev else None,
            "server_now": timezone.now().isoformat(),
        })


class OpenBusinessDayView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    @idempotent
    def post(self, request):
        opening_float = request.data.get("opening_float")
        if opening_float is None:
            raise ApiError("validation_error", "釣銭準備金を入力してください。")
        if D(opening_float) < 0:
            raise ApiError("validation_error", "釣銭準備金は0以上で入力してください。")
        day = services.get_or_open_business_day(request.store, request.user, opening_float=D(opening_float))
        audit(request, "day.open", target=day, changes={"date": day.date, "opening_float": day.opening_float})
        return Response(_day_payload(day), status=201)


class CashMovementsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    @idempotent
    @transaction.atomic
    def post(self, request):
        store = request.store
        kind = request.data.get("kind")
        if kind not in CashMovementKind.values:
            raise ApiError("validation_error", "入出金の種別が不正です。")
        amount = D(request.data.get("amount") or 0)
        if amount <= 0:
            raise ApiError("validation_error", "金額は1円以上で入力してください。")
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "理由・内容を入力してください。")
        day = services.require_open_business_day(store)
        if day.status != "open":
            raise ApiError("day_closed", "営業日が締め済みです。", status=409)
        employee = None
        if request.data.get("employee_id"):
            employee = Employee.objects.filter(id=request.data["employee_id"], company=store.company).first()
            if employee is None:
                raise ApiError("validation_error", "この店舗で利用できない従業員が指定されました。")
        if kind == CashMovementKind.DAILY_PAY:
            # 日払いは権限と従業員指定が必要。現金出金と支払記録を関連付け、二重控除を防ぐ。
            if CAP_PAYROLL_PAY not in request.store_access.caps:
                raise ApiError("forbidden", "日払いの登録には支払い権限が必要です。", status=403)
            if employee is None:
                raise ApiError("validation_error", "日払いの対象従業員を選択してください。")
        account = None
        if kind == CashMovementKind.EXPENSE and request.data.get("account_key"):
            # 現金経費の勘定科目（承認済みの領収書から、または経理が選ぶ）。未指定なら仕訳では雑費＋警告
            from apps.accounting import books

            account = books.account_by_key(store, str(request.data["account_key"]))
        mv = CashMovement.objects.create(
            store=store, business_day=day, kind=kind, amount=amount, reason=reason[:200], employee=employee,
            occurred_at=timezone.now(), performed_by=request.user, account=account,
        )
        if kind == CashMovementKind.DAILY_PAY:
            from apps.accounting import services as accounting

            ep = EmployeePayment.objects.create(
                employee=employee, store=store, kind=request.data.get("payment_kind") or "daily", amount=amount, method="cash",
                paid_at=mv.occurred_at, business_date=day.date, work_date=day.date, cash_movement=mv, recorded_by=request.user, note=reason[:200],
            )
            # 手入力の出金は渡した額だけなので源泉徴収を計算しない。給与以外は月次の「要確認」に出す
            st = accounting.settle_simple(store, employee, amount=amount, on_date=day.date, paid_on=timezone.localdate(mv.occurred_at), mode="net")
            accounting.record_withholding(ep, st, work_date=day.date, user=request.user)
        audit(request, f"cash.movement.{kind}", target=mv, changes={"amount": amount, "reason": reason})
        return Response(CashMovementSerializer(mv).data, status=201)


class CashMovementVoidView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    @transaction.atomic
    def post(self, request, movement_id):
        mv = CashMovement.objects.select_for_update().filter(id=movement_id, store=request.store).first()
        if mv is None:
            raise ApiError("not_found", "入出金が見つかりません。", status=404)
        if mv.is_voided:
            raise ApiError("already_voided", "既に取消済みです。", status=409)
        if mv.business_day.status != "open":
            # 締めた後に現金の記録を変えると締めの数字と合わなくなるため、営業日を再開してから（履歴が残る）
            raise ApiError("day_closed", "レジ締め済みの営業日の入出金は取り消せません。必要なら「この営業日を再開する」をしてから取り消してください。", status=409)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "取消理由を入力してください。")
        if getattr(mv, "tax_remittance", None) is not None or getattr(mv, "owner_transfer", None) is not None:
            raise ApiError("linked_record", "経理の納付記録・資金移動に紐づく出金です。「経理」画面の該当記録から取り消してください。", status=409)
        mv.is_voided = True
        mv.void_reason = reason[:200]
        mv.voided_by = request.user
        mv.save()
        ep = getattr(mv, "employee_payment", None)
        if ep is not None:
            from apps.accounting import services as accounting

            ep.is_voided = True
            ep.void_reason = reason[:200]
            ep.save(update_fields=["is_voided", "void_reason"])
            # 源泉徴収明細も取消にし、使った控除額を戻す（明細は履歴として残る）
            accounting.on_payment_voided(ep, reason, request.user)
        audit(request, "cash.movement.void", target=mv, changes={"reason": reason})
        return Response(CashMovementSerializer(mv).data)


class CloseBusinessDayView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    @idempotent
    def post(self, request):
        day = services.current_open_day(request.store)
        if day is None:
            raise ApiError("no_open_day", "営業中の営業日がありません。", status=409)
        if request.data.get("business_day_id") and int(request.data["business_day_id"]) != day.id:
            raise ApiError("conflict", "対象の営業日が変わっています。画面を更新してください。", status=409)
        if request.data.get("next_day_float") is None:
            raise ApiError("validation_error", "翌日の釣銭準備金を入力してください。")
        if request.data.get("opening_float") is not None:
            day.opening_float = D(request.data["opening_float"])
            day.opening_float_carried = False
            day.save(update_fields=["opening_float", "opening_float_carried"])
        day = services.close_business_day(
            day, request.user, counted_cash=request.data.get("counted_cash"), denominations=request.data.get("denominations"),
            next_day_float=request.data["next_day_float"], difference_reason=request.data.get("difference_reason", ""),
            note=request.data.get("note", ""),
        )
        audit(request, "day.close", target=day, changes={"date": day.date, "counted_cash": day.counted_cash, "difference": day.difference,
                                                         "set_aside": day.set_aside_amount, "shortage": day.shortage_amount})
        return Response(_day_payload(day))


class BusinessDayListView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    def get(self, request):
        qs = BusinessDay.objects.filter(store=request.store).order_by("-date")
        f = parse_date(request.query_params.get("from") or "")
        t = parse_date(request.query_params.get("to") or "")
        if f:
            qs = qs.filter(date__gte=f)
        if t:
            qs = qs.filter(date__lte=t)
        return Response(BusinessDaySerializer(qs[:120], many=True).data)


class BusinessDayDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    def get(self, request, day_id):
        day = BusinessDay.objects.filter(id=day_id, store=request.store).first()
        if day is None:
            raise ApiError("not_found", "営業日が見つかりません。", status=404)
        return Response(_day_payload(day))


class BusinessDayReviseView(APIView):
    """締め後の修正（権限 + 再認証必須、履歴を残す）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING_EDIT)]

    def post(self, request, day_id):
        day = BusinessDay.objects.filter(id=day_id, store=request.store).first()
        if day is None:
            raise ApiError("not_found", "営業日が見つかりません。", status=404)
        day = services.revise_closing(day, request.user, request.data.get("changes") or {}, request.data.get("reason", ""))
        audit(request, "day.revise", target=day, changes={"reason": request.data.get("reason"), "changes": request.data.get("changes")})
        return Response(_day_payload(day))


class ReopenBusinessDayView(APIView):
    """締め済み営業日の再開（締め後の修正の一種。権限 + 再認証必須、履歴を残す）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING_EDIT)]

    @transaction.atomic
    def post(self, request, day_id):
        day = BusinessDay.objects.select_for_update().filter(id=day_id, store=request.store).first()
        if day is None:
            raise ApiError("not_found", "営業日が見つかりません。", status=404)
        if day.status != "closed":
            raise ApiError("not_closed", "締め済みの営業日だけ再開できます。", status=409)
        if services.current_open_day(request.store) is not None:
            raise ApiError("conflict", "営業中の営業日があるため再開できません。先にその営業日を締めてください。", status=409)
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            raise ApiError("validation_error", "再開の理由を入力してください。")
        before = {"status": day.status, "closed_at": day.closed_at.isoformat() if day.closed_at else None, "counted_cash": str(day.counted_cash)}
        day.status = "open"
        day.closed_at = None
        day.closed_by = None
        day.save(update_fields=["status", "closed_at", "closed_by", "updated_at"])
        ClosingRevision.objects.create(business_day=day, changed_by=request.user, reason=f"再開: {reason}"[:300], before=before, after={"status": "open"})
        audit(request, "day.reopen", target=day, changes={"reason": reason})
        return Response(_day_payload(day))


class BankDepositsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    @idempotent
    def post(self, request, day_id):
        day = BusinessDay.objects.filter(id=day_id, store=request.store).first()
        if day is None:
            raise ApiError("not_found", "営業日が見つかりません。", status=404)
        amount = D(request.data.get("amount") or 0)
        if amount <= 0:
            raise ApiError("validation_error", "入金額は1円以上で入力してください。")
        dep = BankDeposit.objects.create(store=request.store, business_day=day, amount=amount, deposited_at=timezone.now(),
                                         recorded_by=request.user, note=(request.data.get("note") or "")[:200])
        audit(request, "day.bank_deposit", target=day, changes={"amount": amount})
        return Response(BankDepositSerializer(dep).data, status=201)
