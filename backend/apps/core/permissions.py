"""
権限（ケイパビリティ）定義と、店舗アクセスの解決。

- 画面で隠すだけでなく、API 側で必ず検証する。
- リクエストの X-Store-Id はそのまま信用せず、ユーザーの所属（会社・店舗）と照合する。
- 運営者(is_platform_operator)が顧客企業のデータへアクセスした場合は記録する。
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone
from rest_framework.permissions import BasePermission

from .exceptions import ApiError
from .models import Company, CompanyMembership, CompanyRole, OperatorAccessLog, Store, StoreMembership, StoreRole

# --- ケイパビリティ一覧 -------------------------------------------------------
CAP_FLOOR = "floor.operate"            # 入店・注文・延長・会計などの営業操作
CAP_SALES_VIEW = "sales.view"          # 売上・会計履歴の閲覧
CAP_SALES_VOID = "sales.void"          # 会計取消・返金
CAP_CLOSING = "closing.operate"        # 開店・日締め・現金出納
CAP_CLOSING_EDIT = "closing.edit_closed"  # 締め後の修正
CAP_SETTINGS_VIEW = "settings.view"
CAP_SETTINGS_EDIT = "settings.edit"    # 料金・店舗設定・権限設定
CAP_CATALOG_EDIT = "catalog.edit"      # 商品・コース・卓
CAP_EMPLOYEES_VIEW = "employees.view"
CAP_EMPLOYEES_EDIT = "employees.edit"
CAP_WAGES_VIEW = "wages.view"
CAP_WAGES_EDIT = "wages.edit"
CAP_BACK_RULES_VIEW = "backs.rules.view"
CAP_BACK_RULES_EDIT = "backs.rules.edit"
CAP_BACKS_VIEW_ALL = "backs.view_all"
CAP_ATTENDANCE_SELF = "attendance.self"
CAP_ATTENDANCE_MANAGE = "attendance.manage"
CAP_PAYROLL_VIEW_ALL = "payroll.view_all"
CAP_PAYROLL_VIEW_SELF = "payroll.view_self"
CAP_PAYROLL_PAY = "payroll.pay"        # 日払い・前払い・締め
CAP_CUSTOMERS_VIEW = "customers.view"
CAP_CUSTOMERS_EDIT = "customers.edit"
CAP_CUSTOMERS_EXPORT = "customers.export"
CAP_INVENTORY_VIEW = "inventory.view"
CAP_INVENTORY_OPERATE = "inventory.operate"  # 入荷・廃棄・棚卸し
CAP_INVENTORY_MANAGE = "inventory.manage"    # 仕入先・発注点
CAP_PURCHASING_DRAFT = "purchasing.draft"
CAP_PURCHASING_CONFIRM = "purchasing.confirm"
CAP_RESERVATIONS = "reservations.operate"
CAP_LINE_VIEW = "line.view"
CAP_LINE_MANAGE = "line.manage"
CAP_LINE_SEND = "line.send"
CAP_DASHBOARD = "dashboard.view"
CAP_EXPORT_SALES = "export.sales"
CAP_EXPORT_CASH = "export.cash"
CAP_EXPORT_PAYROLL = "export.payroll"
CAP_EXPORT_BACKS = "export.backs"
CAP_EXPORT_ATTENDANCE = "export.attendance"
CAP_EXPORT_INVENTORY = "export.inventory"
CAP_AUDIT_VIEW = "audit.view"
CAP_COMPANY_MANAGE = "company.manage"  # 会社内の店舗追加・ユーザー招待・配属
# 経理（apps.accounting）
CAP_ACCOUNTING_VIEW = "accounting.view"          # 経理データ（売上・経費・月次損益・仕訳・照合）の閲覧
CAP_ACCOUNTING_BOOKKEEP = "accounting.bookkeep"  # 領収書・経費の登録、仕訳の作成、照合、会計データ出力
CAP_ACCOUNTING_FREEE = "accounting.freee"        # freee 連携の管理（接続・勘定科目の対応付け・送信・再送）
CAP_ACCOUNTING_SETTINGS = "accounting.settings"  # 事業者情報・店舗の事業者・記帳代行／税理士への権限付与
CAP_TAX_VIEW = "tax.view"                        # 源泉所得税の集計・明細の閲覧
CAP_TAX_DECIDE = "tax.decide"                    # 税務区分の確定・月次の経営者確認・訂正・納付記録（経営者本人の判断）
CAP_TAX_ADVISE = "tax.advise"                    # 税理士等の確認コメント（判断そのものは経営者）
CAP_CONTRACTS_EDIT = "contracts.edit"            # キャスト契約の登録（税務区分の確定は tax.decide が必要）
# 領収書・経費（apps.expenses）
CAP_EXPENSES_SUBMIT = "expenses.submit"          # 領収書・経費の登録（撮影・手入力・再提出）
CAP_EXPENSES_VIEW_STORE = "expenses.view_store"  # 自店舗の登録状況の閲覧（科目・税務情報は含まない）
CAP_EXPENSES_APPROVE = "expenses.approve"        # 承認・差戻し・却下・訂正
# 銀行（apps.banking）
CAP_BANKING_VIEW = "banking.view"
CAP_BANKING_IMPORT = "banking.import"            # CSV 取込・取消
CAP_BANKING_RECONCILE = "banking.reconcile"      # 照合の確定・消込解除・対象外
# 帳簿・決算（apps.accounting / apps.closing）
CAP_BOOKS_ADJUST = "books.adjust"                # 手入力仕訳・決算整理仕訳・勘定科目の編集
CAP_YEAREND_PREPARE = "yearend.prepare"          # 棚卸・固定資産・減価償却・決算整理の作成
CAP_YEAREND_CONFIRM = "yearend.confirm"          # 年度の締め・再開（経営者本人）
# 確定申告（apps.tax）
CAP_TAX_RETURN_VIEW = "tax.return.view"          # 申告書データ・入力ガイドの閲覧
CAP_TAX_RETURN_EDIT = "tax.return.edit"          # 所得控除などの入力・計算・版の作成・完成（経営者本人）
CAP_TAX_RETURN_EXPORT = "tax.return.export"      # PDF/CSV/XLSX/XTX の出力（経営者本人）
CAP_TAX_CONFIG_MANAGE = "tax.config.manage"      # 年度別税制設定の編集（運営者）

ALL_CAPS: frozenset[str] = frozenset(
    v for k, v in globals().items() if k.startswith("CAP_") and isinstance(v, str)
)

# 経営者本人だけが持つ税務判断のケイパビリティ
OWNER_ONLY_CAPS = frozenset({CAP_TAX_DECIDE, CAP_YEAREND_CONFIRM, CAP_TAX_RETURN_EDIT, CAP_TAX_RETURN_EXPORT})
OWNER_CAPS = ALL_CAPS - {CAP_TAX_CONFIG_MANAGE}
# 税務上の判断（税務区分の確定・月次の確認・納付・年度締め・申告書の入力/出力）は経営者本人だけ。会社管理者・運営者には持たせない
ADMIN_CAPS = ALL_CAPS - {CAP_COMPANY_MANAGE, CAP_ACCOUNTING_SETTINGS, CAP_TAX_CONFIG_MANAGE} - OWNER_ONLY_CAPS
OPERATOR_CAPS = ALL_CAPS - {CAP_TAX_ADVISE} - OWNER_ONLY_CAPS
MANAGER_CAPS = frozenset({
    CAP_FLOOR, CAP_SALES_VIEW, CAP_SALES_VOID, CAP_CLOSING, CAP_CLOSING_EDIT,
    CAP_SETTINGS_VIEW, CAP_SETTINGS_EDIT, CAP_CATALOG_EDIT,
    CAP_EMPLOYEES_VIEW, CAP_EMPLOYEES_EDIT,
    CAP_ATTENDANCE_SELF, CAP_ATTENDANCE_MANAGE,
    CAP_CUSTOMERS_VIEW, CAP_CUSTOMERS_EDIT,
    CAP_INVENTORY_VIEW, CAP_INVENTORY_OPERATE, CAP_INVENTORY_MANAGE,
    CAP_PURCHASING_DRAFT,
    CAP_RESERVATIONS, CAP_LINE_VIEW, CAP_LINE_MANAGE, CAP_LINE_SEND,
    CAP_DASHBOARD, CAP_EXPORT_SALES, CAP_EXPORT_CASH, CAP_EXPORT_INVENTORY, CAP_EXPORT_ATTENDANCE,
    CAP_AUDIT_VIEW, CAP_BACK_RULES_VIEW, CAP_BACKS_VIEW_ALL,
    CAP_PAYROLL_PAY,  # レジ現金からの日払い登録（時給設定・支払集計の閲覧はオーナーのみ）
    CAP_CONTRACTS_EDIT,  # 契約の登録まで（税務区分の確定はオーナー）
    CAP_EXPENSES_SUBMIT, CAP_EXPENSES_VIEW_STORE,  # 領収書の登録と自店舗の状況（承認は店舗設定で許可したときだけ）
})
STAFF_CAPS = frozenset({
    CAP_FLOOR, CAP_SALES_VIEW, CAP_CLOSING,
    CAP_SETTINGS_VIEW,
    CAP_EMPLOYEES_VIEW,
    CAP_ATTENDANCE_SELF,
    CAP_PAYROLL_VIEW_SELF,
    CAP_CUSTOMERS_VIEW, CAP_CUSTOMERS_EDIT,
    CAP_INVENTORY_VIEW, CAP_INVENTORY_OPERATE,
    CAP_PURCHASING_DRAFT,
    CAP_RESERVATIONS, CAP_LINE_VIEW,
    CAP_EXPENSES_SUBMIT, CAP_EXPENSES_VIEW_STORE,
})
CAST_CAPS = frozenset({CAP_ATTENDANCE_SELF, CAP_PAYROLL_VIEW_SELF})

# 記帳代行担当者（NOXIA の記帳代行サービス）: 一般的な記帳業務まで。個別の税務判断・申告書作成・税務代理はできない
BOOKKEEPER_CAPS = frozenset({
    CAP_ACCOUNTING_VIEW, CAP_ACCOUNTING_BOOKKEEP, CAP_ACCOUNTING_FREEE, CAP_TAX_VIEW,
    CAP_SALES_VIEW, CAP_DASHBOARD, CAP_EXPORT_SALES, CAP_EXPORT_CASH,
    CAP_EXPENSES_SUBMIT, CAP_EXPENSES_VIEW_STORE, CAP_EXPENSES_APPROVE,
    CAP_BANKING_VIEW, CAP_BANKING_IMPORT, CAP_BANKING_RECONCILE,
    CAP_BOOKS_ADJUST, CAP_YEAREND_PREPARE, CAP_TAX_RETURN_VIEW,
})
# 税理士等の外部専門家: 閲覧と確認コメント（税務区分の確定・納付の記録は経営者が行う）
TAX_ADVISOR_CAPS = frozenset({
    CAP_ACCOUNTING_VIEW, CAP_TAX_VIEW, CAP_TAX_ADVISE,
    CAP_SALES_VIEW, CAP_DASHBOARD, CAP_EXPORT_SALES, CAP_EXPORT_CASH,
    CAP_BANKING_VIEW, CAP_TAX_RETURN_VIEW,
})
GRANT_ROLE_CAPS = {"bookkeeper": BOOKKEEPER_CAPS, "tax_advisor": TAX_ADVISOR_CAPS}

# 停止中の会社・店舗で許可する閲覧系ケイパビリティ
READ_ONLY_CAPS = frozenset(
    c for c in ALL_CAPS if c.endswith(".view") or c.endswith(".view_all") or c.endswith(".view_store") or c.startswith("export.")
)

ROLE_CAPS = {
    StoreRole.MANAGER: MANAGER_CAPS,
    StoreRole.STAFF: STAFF_CAPS,
    StoreRole.CAST: CAST_CAPS,
}

# 共有iPad対策: 以下のケイパビリティを使う更新系操作は再認証(reauth)を要求する
# 店舗のタブレットは店舗ごとに 1 アカウントでログインしたままにする運用（2026-11-12）なので、
# 店舗の操作（会計・取消・開店・日締め・締め後の修正・レジからの日払い・領収書）には再確認を求めない。
# 再確認が要るのは、パソコンから行う設定・給与・バックのルール・集計の閲覧・会社管理・税務・freee・会計設定。
REAUTH_CAPS = frozenset({CAP_WAGES_VIEW, CAP_WAGES_EDIT, CAP_BACK_RULES_EDIT, CAP_PAYROLL_VIEW_ALL, CAP_PAYROLL_PAY,
                         CAP_EXPORT_PAYROLL, CAP_SETTINGS_EDIT, CAP_COMPANY_MANAGE,
                         CAP_TAX_DECIDE, CAP_ACCOUNTING_FREEE, CAP_ACCOUNTING_SETTINGS,
                         CAP_YEAREND_CONFIRM, CAP_TAX_RETURN_EDIT, CAP_TAX_RETURN_EXPORT, CAP_TAX_CONFIG_MANAGE})
# 申告・年度締めは、ログイン直後の自動の再認証では足りず、パスワードを明示的に再確認した記録が必要
EXPLICIT_REAUTH_CAPS = frozenset({CAP_YEAREND_CONFIRM, CAP_TAX_RETURN_EDIT, CAP_TAX_RETURN_EXPORT, CAP_TAX_CONFIG_MANAGE})


@dataclass(frozen=True)
class StoreAccess:
    store: Store
    company: Company
    caps: frozenset[str]
    role: str          # operator / owner / admin / manager / staff / cast
    read_only: bool    # 停止中などで閲覧のみ

    def has(self, *caps: str) -> bool:
        return all(c in self.caps for c in caps)


def _company_role(user, company: Company) -> str | None:
    cm = CompanyMembership.objects.filter(user=user, company=company, is_active=True).first()
    return cm.role if cm else None


def _active_grants(user):
    """会社オーナーが許可した記帳代行・税理士等の権限（有効期限内のもの）。"""
    from django.db.models import Q

    from apps.accounting.models import AccountingAccessGrant

    today = timezone.localdate()
    return AccountingAccessGrant.objects.filter(user=user, is_active=True, company__is_active=True).filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=today)
    )


def _grant_role(user, company: Company) -> str | None:
    g = _active_grants(user).filter(company=company).first()
    return g.role if g else None


def resolve_store_access(user, store_id, *, request=None) -> StoreAccess:
    """ユーザーが store_id を操作できるか検証し、ケイパビリティを返す。"""
    if not user or not user.is_authenticated:
        raise ApiError("auth_required", "ログインが必要です。", status=401)
    try:
        sid = int(store_id)
    except (TypeError, ValueError):
        raise ApiError("store_required", "店舗が指定されていません。店舗を選択してください。", status=400)
    store = Store.objects.select_related("company").filter(id=sid).first()
    if store is None:
        # 存在有無を漏らさないため、権限なしと同じ応答にする
        raise ApiError("store_forbidden", "この店舗を操作する権限がありません。", status=403)
    company = store.company
    active = store.is_active and company.is_active

    if user.is_platform_operator:
        if request is not None and not getattr(request, "_operator_access_logged", False):
            OperatorAccessLog.objects.create(user=user, company=company, store=store, method=request.method, path=request.path[:300])
            request._operator_access_logged = True
        return StoreAccess(store, company, OPERATOR_CAPS, "operator", read_only=False)

    crole = _company_role(user, company)
    if crole in (CompanyRole.OWNER, CompanyRole.ADMIN):
        caps = OWNER_CAPS if crole == CompanyRole.OWNER else ADMIN_CAPS
        if not active:
            return StoreAccess(store, company, caps & READ_ONLY_CAPS, crole, read_only=True)
        return StoreAccess(store, company, caps, crole, read_only=False)

    sm = StoreMembership.objects.filter(user=user, store=store, is_active=True).first()
    grole = _grant_role(user, company)
    if sm is None and grole is None:
        raise ApiError("store_forbidden", "この店舗を操作する権限がありません。", status=403)
    if not active:
        raise ApiError("store_inactive", "この店舗は停止中です。", status=403)
    caps = (ROLE_CAPS[sm.role] if sm else frozenset()) | (GRANT_ROLE_CAPS.get(grole, frozenset()) if grole else frozenset())
    if sm is not None and sm.role == StoreRole.MANAGER:
        # 店長の領収書承認は店舗設定で許可した場合だけ（既定は不可）
        st = getattr(store, "settings", None)
        if st is not None and getattr(st, "manager_can_approve_expenses", False):
            caps = caps | {CAP_EXPENSES_APPROVE}
    return StoreAccess(store, company, caps, sm.role if sm else grole, read_only=False)


def accessible_stores(user):
    """ログイン後に選択できる店舗の一覧（所属する会社・店舗だけ）。"""
    if user.is_platform_operator:
        return Store.objects.select_related("company").all()
    company_ids = list(
        CompanyMembership.objects.filter(user=user, is_active=True, role__in=[CompanyRole.OWNER, CompanyRole.ADMIN])
        .values_list("company_id", flat=True)
    )
    member_store_ids = list(StoreMembership.objects.filter(user=user, is_active=True).values_list("store_id", flat=True))
    company_ids += list(_active_grants(user).values_list("company_id", flat=True))
    from django.db.models import Q
    return Store.objects.select_related("company").filter(Q(company_id__in=company_ids) | Q(id__in=member_store_ids)).distinct()


def company_role_for(user, company: Company) -> str | None:
    if user.is_platform_operator:
        return "operator"
    return _company_role(user, company)


# --- DRF permission classes ---------------------------------------------------
def ensure_store_access(request) -> StoreAccess:
    access = getattr(request, "store_access", None)
    if access is None:
        access = resolve_store_access(request.user, request.headers.get("X-Store-Id"), request=request)
        request.store_access = access
        request.store = access.store
    return access


def is_reauthenticated(request, *, explicit: bool = False) -> bool:
    """
    再認証済みか。explicit=True のときは、ログイン時の自動付与ではなく「パスワードを再確認した」記録（reauth_source=explicit）を要求する
    （申告・年度締めなどの税務上の操作）。
    """
    until = request.session.get("reauth_until")
    if not until:
        return False
    if timezone.now().timestamp() >= float(until):
        return False
    if explicit and request.session.get("reauth_source") != "explicit":
        return False
    return True


class StoreCaps(BasePermission):
    """
    使い方: permission_classes = [StoreCaps("floor.operate")]
    X-Store-Id の所属検証を行い、必要なケイパビリティを確認する。
    更新系(POST/PUT/PATCH/DELETE)で REAUTH_CAPS を要求する場合は再認証も確認する。
    """

    def __init__(self, *caps: str, write_caps: tuple[str, ...] = (), reauth: bool | None = None):
        self.caps = caps
        self.write_caps = write_caps
        self.reauth = reauth

    def __call__(self):  # DRF は permission_classes の要素をインスタンス化しようとする
        return self

    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            raise ApiError("auth_required", "ログインが必要です。", status=401)
        access = ensure_store_access(request)
        needed = list(self.caps)
        is_write = request.method not in ("GET", "HEAD", "OPTIONS")
        if is_write:
            needed += list(self.write_caps)
        for cap in needed:
            if cap not in access.caps:
                raise ApiError("forbidden", "この操作を行う権限がありません。", status=403)
        if is_write and access.read_only:
            raise ApiError("store_inactive", "停止中の店舗では更新できません。", status=403)
        need_reauth = self.reauth if self.reauth is not None else (is_write and any(c in REAUTH_CAPS for c in needed))
        need_explicit = need_reauth and any(c in EXPLICIT_REAUTH_CAPS for c in needed)
        if need_reauth and not is_reauthenticated(request, explicit=need_explicit):
            raise ApiError("reauth_required", "この操作にはパスワードの再確認が必要です。", status=403)
        return True


class IsAuthenticatedUser(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            raise ApiError("auth_required", "ログインが必要です。", status=401)
        return True


class IsPlatformOperator(BasePermission):
    def has_permission(self, request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            raise ApiError("auth_required", "ログインが必要です。", status=401)
        if not request.user.is_platform_operator:
            raise ApiError("forbidden", "運営者権限が必要です。", status=403)
        return True


def reauth_valid_seconds() -> int:
    return int(settings.REAUTH_VALID_MINUTES) * 60
