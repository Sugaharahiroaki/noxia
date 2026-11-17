from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from . import void_pin
from .audit import audit
from .exceptions import ApiError
from .models import (
    AuditLog, Company, CompanyMembership, CompanyRole, Invitation, OperatorAccessLog, Store, StoreMembership, StoreRole,
    StoreSettings, User,
)
from .permissions import (
    CAP_AUDIT_VIEW, CAP_COMPANY_MANAGE, CAP_SETTINGS_EDIT, CAP_SETTINGS_VIEW, CAP_TAX_DECIDE, IsAuthenticatedUser, IsPlatformOperator,
    StoreCaps, accessible_stores, company_role_for, is_reauthenticated, reauth_valid_seconds, resolve_store_access,
)
from .serializers import (
    AuditLogSerializer, CompanyMembershipSerializer, CompanySerializer, InvitationSerializer, StoreMembershipSerializer,
    StoreSerializer, StoreSettingsSerializer, UserBriefSerializer,
)


# --- 認証 ---------------------------------------------------------------------
class LoginThrottle(SimpleRateThrottle):
    """ログイン・再認証の試行回数制限（IP単位）。"""

    scope = "login"

    def get_rate(self):
        return "30/min"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


def _taxpayer_brief(store) -> dict | None:
    """店舗の事業者（あれば）。画面は申告方法で「経理」メニューの出し分けをする。"""
    from apps.accounting.models import StoreTaxProfile

    prof = StoreTaxProfile.objects.filter(store=store).select_related("taxpayer").first()
    if prof is None:
        return None
    tp = prof.taxpayer
    return {"id": tp.id, "name": tp.name, "entity_type": tp.entity_type, "filing_method": tp.filing_method}


def me_payload(request) -> dict:
    from . import tablet

    user = request.user
    stores = []
    tablet_store_ids = set(StoreMembership.objects.filter(user=user, is_active=True, is_tablet=True).values_list("store_id", flat=True))
    for store in accessible_stores(user):
        try:
            access = resolve_store_access(user, store.id)
        except ApiError:
            continue
        stores.append({
            # 店舗タブレット用のアカウントか、そのときに出す画面（画面側のメニューと移動の制限に使う）
            "is_tablet": store.id in tablet_store_ids,
            "tablet_menu": tablet.resolve_menu(store),
            "id": store.id,
            "name": store.name,
            "code": store.code,
            "is_active": store.is_active,
            "is_demo": store.is_demo,
            "company": {"id": store.company_id, "name": store.company.name},
            "role": access.role,
            "caps": sorted(access.caps),
            "read_only": access.read_only,
            "taxpayer": _taxpayer_brief(store),
        })
    companies = []
    if user.is_platform_operator:
        for c in Company.objects.all():
            companies.append({"id": c.id, "name": c.name, "role": "operator", "is_active": c.is_active})
    else:
        for cm in CompanyMembership.objects.filter(user=user, is_active=True).select_related("company"):
            companies.append({"id": cm.company_id, "name": cm.company.name, "role": cm.role, "is_active": cm.company.is_active})
    employee = None
    emp = getattr(user, "employee", None)
    if emp is not None:
        employee = {"id": emp.id, "display_name": emp.display_name, "company_id": emp.company_id}
    reauth_until = request.session.get("reauth_until")
    return {
        "user": UserBriefSerializer(user).data,
        "stores": stores,
        "companies": companies,
        "employee": employee,
        "reauth_until": reauth_until,
        "reauth_valid": is_reauthenticated(request),
        "server_now": timezone.now().isoformat(),
    }


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"ok": True})


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username or not password:
            raise ApiError("validation_error", "ユーザー名とパスワードを入力してください。")
        user = authenticate(request, username=username, password=password)
        if user is None:
            raise ApiError("login_failed", "ユーザー名またはパスワードが正しくありません。", status=401)
        if not user.is_active:
            raise ApiError("login_failed", "このアカウントは無効化されています。", status=401)
        login(request, user)
        request.session["reauth_until"] = timezone.now().timestamp() + reauth_valid_seconds()
        request.session["reauth_source"] = "login"  # ログイン直後の自動付与（税務上の操作には使えない）
        audit(request, "auth.login", target=user)
        return Response(me_payload(request))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user and request.user.is_authenticated:
            audit(request, "auth.logout", target=request.user)
        logout(request)
        return Response({"ok": True})


class MeView(APIView):
    """ログイン状態の確認。未ログインでも 401 ではなく user: null を返す（画面側の初期表示用）。"""

    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return Response({"user": None, "stores": [], "companies": [], "employee": None, "reauth_until": None, "reauth_valid": False,
                             "server_now": timezone.now().isoformat()})
        return Response(me_payload(request))


class ReauthView(APIView):
    """共有iPadで機微な操作を行う前のパスワード再確認。"""

    permission_classes = [IsAuthenticatedUser]
    throttle_classes = [LoginThrottle]

    def post(self, request):
        password = request.data.get("password") or ""
        if not request.user.check_password(password):
            audit(request, "auth.reauth_failed", target=request.user)
            raise ApiError("reauth_failed", "パスワードが正しくありません。", status=401)
        until = timezone.now().timestamp() + reauth_valid_seconds()
        request.session["reauth_until"] = until
        request.session["reauth_source"] = "explicit"  # パスワードを明示的に再確認した（申告・年度締めはこれが必要）
        audit(request, "auth.reauth", target=request.user)
        return Response({"reauth_until": until, "valid_minutes": reauth_valid_seconds() // 60})


class SelectStoreView(APIView):
    """店舗切替（所属検証を行い、監査ログに記録する）。"""

    permission_classes = [IsAuthenticatedUser]

    def post(self, request):
        access = resolve_store_access(request.user, request.data.get("store_id"), request=request)
        request.session["active_store_id"] = access.store.id
        audit(request, "auth.select_store", target=access.store, store=access.store)
        return Response({"store_id": access.store.id, "role": access.role, "caps": sorted(access.caps)})


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def post(self, request):
        current = request.data.get("current_password") or ""
        new = request.data.get("new_password") or ""
        if not request.user.check_password(current):
            raise ApiError("reauth_failed", "現在のパスワードが正しくありません。", status=401)
        try:
            validate_password(new, request.user)
        except DjangoValidationError as e:
            raise ApiError("validation_error", "、".join(e.messages))
        request.user.set_password(new)
        request.user.save(update_fields=["password"])
        login(request, request.user)
        audit(request, "auth.change_password", target=request.user)
        return Response({"ok": True})


class AcceptInviteView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @transaction.atomic
    def post(self, request):
        token = (request.data.get("token") or "").strip()
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        display_name = (request.data.get("display_name") or "").strip()
        inv = Invitation.objects.select_for_update().filter(token=token).first()
        if inv is None or inv.accepted_at is not None or inv.expires_at < timezone.now():
            raise ApiError("invite_invalid", "招待が無効か期限切れです。", status=400)
        if not username or not password:
            raise ApiError("validation_error", "ユーザー名とパスワードを入力してください。")
        if User.objects.filter(username=username).exists():
            raise ApiError("validation_error", "このユーザー名は既に使われています。")
        try:
            validate_password(password)
        except DjangoValidationError as e:
            raise ApiError("validation_error", "、".join(e.messages))
        user = User.objects.create_user(username=username, password=password, email=inv.email, display_name=display_name or inv.display_name)
        CompanyMembership.objects.create(user=user, company=inv.company, role=inv.company_role)
        for sr in inv.store_roles or []:
            store = Store.objects.filter(id=sr.get("store_id"), company=inv.company).first()
            if store is None:
                continue
            role = sr.get("role") if sr.get("role") in StoreRole.values else StoreRole.STAFF
            StoreMembership.objects.create(user=user, store=store, role=role)
        if inv.employee_id and inv.employee.company_id == inv.company_id and inv.employee.user_id is None:
            inv.employee.user = user
            inv.employee.save(update_fields=["user"])
        inv.accepted_at = timezone.now()
        inv.accepted_user = user
        inv.save(update_fields=["accepted_at", "accepted_user"])
        login(request, user)
        audit(request, "auth.accept_invite", target=inv, company=inv.company)
        return Response(me_payload(request), status=201)


# --- 店舗設定 -----------------------------------------------------------------
class StoreSettingsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def get(self, request):
        store = request.store
        settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
        return Response(StoreSettingsSerializer(settings_obj, context={"store": store}).data)

    def put(self, request):
        store = request.store
        settings_obj, _ = StoreSettings.objects.get_or_create(store=store)
        before = StoreSettingsSerializer(settings_obj, context={"store": store}).data
        ser = StoreSettingsSerializer(settings_obj, data=request.data, partial=True, context={"store": store, "request": request})
        ser.is_valid(raise_exception=True)
        new_withholding = ser.validated_data.get("withholding_enabled", settings_obj.withholding_enabled)
        if new_withholding != settings_obj.withholding_enabled and CAP_TAX_DECIDE not in request.store_access.caps:
            # 源泉所得税を差し引くかどうかは税務上の判断なので経営者だけが変えられる
            raise ApiError("forbidden", "源泉所得税を差し引くかどうかの設定は経営者（オーナー）だけが変更できます。", status=403)
        ser.save()
        after = ser.data
        changes = {k: {"before": before.get(k), "after": after.get(k)} for k in after if before.get(k) != after.get(k)}
        audit(request, "settings.update", target=settings_obj, changes=changes)
        return Response(after)


class StoreVoidPinView(APIView):
    """会計取消の暗証番号を設定・変更・解除する。店舗タブレットの共有アカウント（店長）では変えられないよう、オーナー・会社管理者だけ。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,), reauth=True)]

    def put(self, request):
        if request.store_access.role not in ("owner", "admin", "operator"):
            raise ApiError("forbidden", "会計取消の暗証番号はオーナーだけが設定できます。", status=403)
        pin = request.data.get("pin")
        st = void_pin.set_pin(request.store, None if pin in (None, "") else str(pin))
        audit(request, "settings.void_pin", target=st, changes={"set": bool(st.void_pin_hash)})
        return Response({"void_pin_set": bool(st.void_pin_hash)})


# --- 会社管理（オーナー） -----------------------------------------------------
def _company_for_manage(request, company_id) -> Company:
    company = Company.objects.filter(id=company_id).first()
    if company is None:
        raise ApiError("forbidden", "この会社を管理する権限がありません。", status=403)
    role = company_role_for(request.user, company)
    if role == "operator":
        OperatorAccessLog.objects.create(user=request.user, company=company, method=request.method, path=request.path[:300])
        return company
    if role != CompanyRole.OWNER and not (role == CompanyRole.ADMIN and request.method in ("GET", "HEAD")):
        raise ApiError("forbidden", "この会社を管理する権限がありません。", status=403)
    if request.method not in ("GET", "HEAD") and not is_reauthenticated(request):
        raise ApiError("reauth_required", "この操作にはパスワードの再確認が必要です。", status=403)
    return company


class CompanyDetailView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, company_id):
        company = _company_for_manage(request, company_id)
        return Response(CompanySerializer(company).data)

    def patch(self, request, company_id):
        company = _company_for_manage(request, company_id)
        allowed = {"name", "customer_sharing_enabled", "note"}
        data = {k: v for k, v in request.data.items() if k in allowed}
        ser = CompanySerializer(company, data=data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "company.update", target=company, company=company, changes=data)
        return Response(ser.data)


class CompanyStoresView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, company_id):
        company = _company_for_manage(request, company_id)
        return Response(StoreSerializer(company.stores.all(), many=True).data)

    @transaction.atomic
    def post(self, request, company_id):
        company = _company_for_manage(request, company_id)
        ser = StoreSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Store.objects.filter(company=company, code=ser.validated_data["code"]).exists():
            raise ApiError("validation_error", "同じ店舗コードが既にあります。")
        store = ser.save(company=company, webhook_token=secrets.token_urlsafe(24))
        StoreSettings.objects.create(store=store, settings_note="新規作成。料金・サービス料・端数処理などを確定してください。")
        audit(request, "store.create", target=store, company=company, store=store, changes={"name": store.name})
        return Response(StoreSerializer(store).data, status=201)


class CompanyStoreDetailView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def patch(self, request, company_id, store_id):
        company = _company_for_manage(request, company_id)
        store = Store.objects.filter(company=company, id=store_id).first()
        if store is None:
            raise ApiError("not_found", "店舗が見つかりません。", status=404)
        allowed = {"name", "is_active", "timezone"}
        data = {k: v for k, v in request.data.items() if k in allowed}
        ser = StoreSerializer(store, data=data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        # 停止しても過去データは削除しない（is_active のみ変更）
        audit(request, "store.update", target=store, company=company, store=store, changes=data)
        return Response(ser.data)


class CompanyMembersView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, company_id):
        company = _company_for_manage(request, company_id)
        cms = CompanyMembership.objects.filter(company=company).select_related("user")
        sms = StoreMembership.objects.filter(store__company=company).select_related("user", "store")
        return Response({
            "company_memberships": CompanyMembershipSerializer(cms, many=True).data,
            "store_memberships": StoreMembershipSerializer(sms, many=True).data,
        })

    @transaction.atomic
    def post(self, request, company_id):
        """店舗配属の追加・更新 {user_id, store_id, role, is_active} / 会社権限の更新 {user_id, company_role, is_active}"""
        company = _company_for_manage(request, company_id)
        user = User.objects.filter(id=request.data.get("user_id")).first()
        if user is None or not CompanyMembership.objects.filter(user=user, company=company).exists():
            raise ApiError("validation_error", "この会社に所属するユーザーを指定してください。")
        if "company_role" in request.data:
            role = request.data.get("company_role")
            if role not in CompanyRole.values:
                raise ApiError("validation_error", "会社権限の値が不正です。")
            cm = CompanyMembership.objects.get(user=user, company=company)
            cm.role = role
            if "is_active" in request.data:
                cm.is_active = bool(request.data.get("is_active"))
            cm.save()
            audit(request, "company.membership.update", target=cm, company=company, changes={"role": role, "is_active": cm.is_active})
            return Response(CompanyMembershipSerializer(cm).data)
        store = Store.objects.filter(company=company, id=request.data.get("store_id")).first()
        if store is None:
            raise ApiError("validation_error", "この会社の店舗を指定してください。")
        role = request.data.get("role")
        if role not in StoreRole.values:
            raise ApiError("validation_error", "店舗権限の値が不正です。")
        sm, _ = StoreMembership.objects.update_or_create(
            user=user, store=store, defaults={"role": role, "is_active": bool(request.data.get("is_active", True))}
        )
        audit(request, "store.membership.update", target=sm, company=company, store=store, changes={"role": role, "is_active": sm.is_active})
        return Response(StoreMembershipSerializer(sm).data)


class CompanyInvitationsView(APIView):
    permission_classes = [IsAuthenticatedUser]

    def get(self, request, company_id):
        company = _company_for_manage(request, company_id)
        return Response(InvitationSerializer(company.invitations.order_by("-id"), many=True).data)

    @transaction.atomic
    def post(self, request, company_id):
        company = _company_for_manage(request, company_id)
        ser = InvitationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        store_roles = []
        for sr in ser.validated_data.get("store_roles") or []:
            if not Store.objects.filter(company=company, id=sr.get("store_id")).exists():
                raise ApiError("validation_error", "この会社の店舗だけを配属できます。")
            if sr.get("role") not in StoreRole.values:
                raise ApiError("validation_error", "店舗権限の値が不正です。")
            store_roles.append({"store_id": int(sr["store_id"]), "role": sr["role"]})
        employee = ser.validated_data.get("employee")
        if employee is not None and employee.company_id != company.id:
            raise ApiError("validation_error", "この会社の従業員を指定してください。")
        inv = ser.save(
            company=company, store_roles=store_roles, token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timedelta(days=7), created_by=request.user,
        )
        audit(request, "company.invite", target=inv, company=company, changes={"company_role": inv.company_role, "store_roles": store_roles})
        return Response(InvitationSerializer(inv).data, status=201)


# --- 運営者（プラットフォーム）管理 ------------------------------------------
class PlatformCompaniesView(APIView):
    permission_classes = [IsPlatformOperator]

    def get(self, request):
        companies = Company.objects.prefetch_related("stores").all()
        data = []
        for c in companies:
            item = CompanySerializer(c).data
            item["stores"] = StoreSerializer(c.stores.all(), many=True).data
            data.append(item)
        return Response(data)

    @transaction.atomic
    def post(self, request):
        ser = CompanySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        company = ser.save()
        audit(request, "platform.company.create", target=company, company=company)
        return Response(CompanySerializer(company).data, status=201)


class PlatformCompanyDetailView(APIView):
    permission_classes = [IsPlatformOperator]

    def patch(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if company is None:
            raise ApiError("not_found", "会社が見つかりません。", status=404)
        ser = CompanySerializer(company, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        OperatorAccessLog.objects.create(user=request.user, company=company, method=request.method, path=request.path[:300])
        audit(request, "platform.company.update", target=company, company=company, changes=dict(request.data))
        return Response(ser.data)


class PlatformCompanyStoresView(APIView):
    permission_classes = [IsPlatformOperator]

    @transaction.atomic
    def post(self, request, company_id):
        company = Company.objects.filter(id=company_id).first()
        if company is None:
            raise ApiError("not_found", "会社が見つかりません。", status=404)
        ser = StoreSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if Store.objects.filter(company=company, code=ser.validated_data["code"]).exists():
            raise ApiError("validation_error", "同じ店舗コードが既にあります。")
        store = ser.save(company=company, webhook_token=secrets.token_urlsafe(24))
        StoreSettings.objects.create(store=store, settings_note="新規作成。料金・サービス料・端数処理などを確定してください。")
        audit(request, "platform.store.create", target=store, company=company, store=store)
        return Response(StoreSerializer(store).data, status=201)


class PlatformUsersView(APIView):
    permission_classes = [IsPlatformOperator]

    def get(self, request):
        users = User.objects.all().order_by("id")
        data = []
        for u in users:
            item = UserBriefSerializer(u).data
            item["company_memberships"] = [
                {"company_id": cm.company_id, "company_name": cm.company.name, "role": cm.role, "is_active": cm.is_active}
                for cm in u.company_memberships.select_related("company")
            ]
            data.append(item)
        return Response(data)

    @transaction.atomic
    def post(self, request):
        """会社オーナーなどの初期ユーザー作成 {username, password, display_name, company_id, company_role}"""
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username or not password:
            raise ApiError("validation_error", "ユーザー名とパスワードを入力してください。")
        if User.objects.filter(username=username).exists():
            raise ApiError("validation_error", "このユーザー名は既に使われています。")
        try:
            validate_password(password)
        except DjangoValidationError as e:
            raise ApiError("validation_error", "、".join(e.messages))
        user = User.objects.create_user(username=username, password=password, display_name=request.data.get("display_name") or "")
        company = Company.objects.filter(id=request.data.get("company_id")).first()
        if company is not None:
            role = request.data.get("company_role") or CompanyRole.OWNER
            if role not in CompanyRole.values:
                raise ApiError("validation_error", "会社権限の値が不正です。")
            CompanyMembership.objects.create(user=user, company=company, role=role)
        audit(request, "platform.user.create", target=user, company=company)
        return Response(UserBriefSerializer(user).data, status=201)


class PlatformAccessLogsView(APIView):
    permission_classes = [IsPlatformOperator]

    def get(self, request):
        logs = OperatorAccessLog.objects.select_related("user", "company", "store").order_by("-id")[:500]
        return Response([
            {"id": l.id, "user": str(l.user), "company": l.company.name, "store": l.store.name if l.store else None,
             "method": l.method, "path": l.path, "created_at": l.created_at}
            for l in logs
        ])


# --- 監査ログ -----------------------------------------------------------------
class AuditLogsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_AUDIT_VIEW)]

    def get(self, request):
        qs = AuditLog.objects.filter(store=request.store).select_related("user").order_by("-id")
        action = request.query_params.get("action")
        if action:
            qs = qs.filter(action__startswith=action)
        return Response(AuditLogSerializer(qs[:300], many=True).data)


class BusinessDateView(APIView):
    """いまの営業日（レジ締めするまでは24時を過ぎても同じ営業日）。画面の初期表示の日付に使う。"""

    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        from apps.cash.services import current_open_day

        from .business_date import current_business_date

        day = current_open_day(request.store)
        return Response({
            "business_date": current_business_date(request.store).isoformat(),
            "day_open": day is not None,
            "opened_at": day.opened_at.isoformat() if day else None,
        })


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"ok": True, "server_now": timezone.now().isoformat()})


class StoreTermsView(APIView):
    """名称変更（POS・集計画面の業務用語）。読むのは店舗の誰でも、変えるのは店舗設定の編集権限（再認証）。"""

    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_SETTINGS_EDIT,))]

    def get(self, request):
        from . import terms

        return Response({"terms": terms.term_list(request.store)})

    @transaction.atomic
    def put(self, request):
        from . import terms

        changes = terms.save_labels(request.store, terms.clean_labels(request.data.get("labels")))
        if changes:
            audit(request, "terms.update", changes=changes)
        return Response({"terms": terms.term_list(request.store)})

    @transaction.atomic
    def delete(self, request):
        """すべて初期値に戻す。"""
        from . import terms

        changes = terms.save_labels(request.store, {k: "" for k in terms.TERMS})
        if changes:
            audit(request, "terms.reset", changes=changes)
        return Response({"terms": terms.term_list(request.store)})


class StoreTabletView(APIView):
    """
    店舗タブレットの設定（PC から）: どのアカウントがタブレット用か、タブレットに出す画面。
    読む: settings.view。変える: settings.edit（再認証）。表示の出し分けだけで、API の権限は役割のまま。
    """

    def get_permissions(self):
        return [StoreCaps(CAP_SETTINGS_VIEW, write_caps=(CAP_SETTINGS_EDIT,))]

    def _payload(self, store) -> dict:
        from . import tablet

        st = getattr(store, "settings", None)
        saved = st.tablet_menu if st is not None and isinstance(st.tablet_menu, list) else []
        members = [{
            "id": sm.id, "user_id": sm.user_id, "username": sm.user.username, "display_name": sm.user.display_name,
            "role": sm.role, "is_active": sm.is_active, "is_tablet": sm.is_tablet,
        } for sm in StoreMembership.objects.filter(store=store).select_related("user").order_by("-is_tablet", "role", "id")]
        return {"members": members, "items": tablet.items_json(), "menu": tablet.resolve_menu(store),
                "default_menu": list(tablet.DEFAULT_TABLET_MENU), "is_default": not saved}

    def get(self, request):
        return Response(self._payload(request.store))

    @transaction.atomic
    def put(self, request):
        """{tablet_membership_ids: [...]} でタブレット用の印を付け替える。{menu: [...]} で出す画面を変える（null か空 = 既定に戻す）。"""
        from . import tablet

        store = request.store
        changes = {}
        if "tablet_membership_ids" in request.data:
            raw = request.data.get("tablet_membership_ids")
            if not isinstance(raw, list) or not all(isinstance(x, int) for x in raw):
                raise ApiError("validation_error", "タブレット用アカウントの一覧を指定してください。")
            ids = set(raw)
            own = set(StoreMembership.objects.filter(store=store).values_list("id", flat=True))
            if ids - own:
                raise ApiError("validation_error", "この店舗に配属されていないアカウントが含まれています。")
            before = set(StoreMembership.objects.filter(store=store, is_tablet=True).values_list("id", flat=True))
            StoreMembership.objects.filter(store=store, id__in=own - ids, is_tablet=True).update(is_tablet=False)
            StoreMembership.objects.filter(store=store, id__in=ids, is_tablet=False).update(is_tablet=True)
            if before != ids:
                names = dict(StoreMembership.objects.filter(store=store, id__in=before | ids).values_list("id", "user__display_name"))
                changes["tablet_accounts"] = {"before": sorted(names.get(i, str(i)) for i in before), "after": sorted(names.get(i, str(i)) for i in ids)}
        if "menu" in request.data:
            raw = request.data.get("menu")
            menu = [] if raw in (None, []) else tablet.clean_menu(raw)
            st, _ = StoreSettings.objects.get_or_create(store=store)
            if st.tablet_menu != menu:
                changes["tablet_menu"] = {"before": st.tablet_menu, "after": menu}
                st.tablet_menu = menu
                st.save(update_fields=["tablet_menu", "updated_at"])
        if changes:
            audit(request, "settings.tablet", target=store, changes=changes)
        return Response(self._payload(store))
