"""
店舗タブレット用アカウントの権限（店舗設定「権限」の「タブレット」の列）。

- 「タブレット用」の印（StoreMembership.is_tablet）が付いたアカウントは、役割（店舗管理者など）ではなく、
  ここで決めた画面と操作だけになる。会社オーナーは PC で全部できる（列は固定の ○）。
- 画面（StoreSettings.tablet_menu）: タブレットのメニューに出す画面。画面側（AppShell）が同じキー（画面の URL）で出し分ける。空なら既定（営業の画面だけ）。
- 操作（StoreSettings.tablet_ops）: タブレットに許可する操作。API の権限（ケイパビリティ）として効く（permissions.resolve_store_access）。
  None なら既定（これまでの店舗管理者と同じ操作）。
- 変えられるのは会社オーナー（会社管理者・運営者）だけ。タブレットは同じ表を見るだけ。
"""
from __future__ import annotations

from . import permissions as P

# (キー = 画面の URL, 名前, グループ)。画面側のメニュー（AppShell の NAV）と同じ URL
TABLET_MENU_ITEMS: list[tuple[str, str, str]] = [
    ("/floor", "営業・卓一覧（卓詳細・注文・会計を含む）", "営業"),
    ("/visits/new", "入店・コース登録", "営業"),
    ("/closing", "開店・日締め・現金", "営業"),
    ("/sales", "売上・会計履歴", "営業"),
    ("/receipts", "領収書・経費登録", "営業"),
    ("/attendance", "出勤・退勤・日払い", "営業"),
    ("/reservations", "予約・LINE", "営業"),
    ("/customers", "顧客・ボトルキープ", "営業"),
    ("/employees", "従業員管理", "管理"),
    ("/inventory", "商品・在庫・棚卸し", "管理"),
    ("/purchasing", "仕入先・発注", "管理"),
    ("/dashboard", "経営ダッシュボード", "管理"),
    ("/reports", "集計（伝票・収支・キャスト）", "管理"),
    ("/accounting/dashboard", "会計ダッシュボード", "経理"),
    ("/accounting/expenses", "経費・領収書管理", "経理"),
    ("/accounting/bank", "銀行 CSV 取込・入出金照合", "経理"),
    ("/accounting/ledgers", "帳簿", "経理"),
    ("/accounting", "スタッフ支払・源泉徴収", "経理"),
    ("/accounting/assets", "固定資産・棚卸", "経理"),
    ("/accounting/yearend", "年間決算", "経理"),
    ("/accounting/tax-return", "確定申告", "経理"),
    ("/accounting/exports", "ファイル出力", "経理"),
    ("/accounting/books", "月次損益・経費・証憑", "経理"),
    ("/accounting/freee", "freee 連携", "経理"),
    ("/accounting/contracts", "キャスト契約・税務区分", "経理"),
    ("/accounting/settings", "会計設定", "経理"),
    ("/settings/pos", "POS 設定（卓・セット構成・商品・タグ・料金）", "設定"),
    ("/settings/pricing", "時給・バック・料金", "設定"),
    ("/settings/store", "店舗設定・権限（営業ルール・プリンター・カード端末・権限の表）", "設定"),
    ("/settings/reports", "入出金項目・名称変更", "設定"),
]
# 経理の画面はパソコンだけ（タブレットには出せない。表では「—」で固定）
PC_ONLY_GROUPS = frozenset({"経理"})
TABLET_MENU_KEYS = [k for k, _l, g in TABLET_MENU_ITEMS if g not in PC_ONLY_GROUPS]
# 既定: 営業の画面だけ（卓一覧・入店・開店と日締めと現金・売上と会計履歴・領収書と経費・出勤と日払い）
DEFAULT_TABLET_MENU = ["/floor", "/visits/new", "/closing", "/sales", "/receipts", "/attendance"]

# タブレットに付け外しできる操作: (キー, 名前, ケイパビリティ, 既定で許可するか)
# 既定の組み合わせ = これまでのタブレット用アカウント（店舗管理者）と同じ操作。既定で外れているものは、これまでオーナーだけだった操作
TABLET_OPS: list[tuple[str, str, frozenset[str], bool]] = [
    ("sales_void", "会計の取消・返金・領収書の記録の取消（取消は暗証番号も必要）", frozenset({P.CAP_SALES_VOID}), True),
    ("closing_edit", "締めた営業日の修正", frozenset({P.CAP_CLOSING_EDIT}), True),
    ("payroll_pay", "日払いを渡す（レジの現金から）", frozenset({P.CAP_PAYROLL_PAY}), True),
    ("attendance_manage", "ほかの人の出勤・退勤の操作・修正", frozenset({P.CAP_ATTENDANCE_MANAGE}), True),
    ("settings_edit", "店舗設定・料金・プリンター・カード端末の変更", frozenset({P.CAP_SETTINGS_EDIT}), True),
    ("catalog_edit", "卓・商品・セット構成の登録と変更", frozenset({P.CAP_CATALOG_EDIT}), True),
    ("employees_edit", "従業員の登録・変更", frozenset({P.CAP_EMPLOYEES_EDIT}), True),
    ("customers_edit", "顧客の登録・変更", frozenset({P.CAP_CUSTOMERS_EDIT}), True),
    ("inventory_operate", "入荷・廃棄・棚卸し・仕入先・発注の下書き",
     frozenset({P.CAP_INVENTORY_OPERATE, P.CAP_INVENTORY_MANAGE, P.CAP_PURCHASING_DRAFT}), True),
    ("line", "LINE の送信・設定", frozenset({P.CAP_LINE_MANAGE, P.CAP_LINE_SEND}), True),
    ("export", "売上・現金・在庫・勤怠のファイル出力",
     frozenset({P.CAP_EXPORT_SALES, P.CAP_EXPORT_CASH, P.CAP_EXPORT_INVENTORY, P.CAP_EXPORT_ATTENDANCE}), True),
    ("wages", "時給の閲覧・設定", frozenset({P.CAP_WAGES_VIEW, P.CAP_WAGES_EDIT}), False),
    ("back_rules_edit", "バック設定の変更", frozenset({P.CAP_BACK_RULES_EDIT}), False),
    ("payroll_all", "全員の支払集計・締め払い", frozenset({P.CAP_PAYROLL_VIEW_ALL, P.CAP_EXPORT_PAYROLL}), False),
    ("customers_export", "顧客情報の出力", frozenset({P.CAP_CUSTOMERS_EXPORT}), False),
    ("purchasing_confirm", "発注の確定", frozenset({P.CAP_PURCHASING_CONFIRM}), False),
]
TABLET_OP_KEYS = [k for k, _l, _c, _d in TABLET_OPS]
DEFAULT_TABLET_OPS = [k for k, _l, _c, d in TABLET_OPS if d]
TABLET_TOGGLE_CAPS: frozenset[str] = frozenset().union(*(c for _k, _l, c, _d in TABLET_OPS))
# タブレットがいつも持つ操作（営業の画面を開いて使うのに要るもの）。これまでの店舗管理者から、付け外しできる操作を除いたもの
TABLET_BASE_CAPS: frozenset[str] = P.MANAGER_CAPS - TABLET_TOGGLE_CAPS
# タブレットには付けられない（会社オーナーだけ）。表に「—」で出す
OWNER_ONLY_LABELS = [
    "権限・タブレット用アカウントの変更",
    "会計の取消の暗証番号の設定",
    "店舗の追加・アカウントの招待",
    "操作履歴",
    "経理・確定申告・源泉徴収",
]


def clean_menu(raw) -> list[str]:
    """設定に保存する一覧。知らないキー・パソコンだけの画面はエラー、重複は除く。順番は TABLET_MENU_ITEMS の順に揃える。"""
    from .exceptions import ApiError

    if not isinstance(raw, list):
        raise ApiError("validation_error", "タブレットに出す画面の一覧を指定してください。")
    keys = set()
    for k in raw:
        if not isinstance(k, str) or k not in TABLET_MENU_KEYS:
            raise ApiError("validation_error", f"タブレットに出せない画面です: {k}")
        keys.add(k)
    return [k for k in TABLET_MENU_KEYS if k in keys]


def resolve_menu(store) -> list[str]:
    """店舗設定の一覧（形の合わないもの・パソコンだけの画面は捨てる）。空なら既定。"""
    raw = getattr(getattr(store, "settings", None), "tablet_menu", None)
    keys = [k for k in raw if isinstance(k, str) and k in TABLET_MENU_KEYS] if isinstance(raw, list) else []
    return [k for k in TABLET_MENU_KEYS if k in set(keys)] if keys else list(DEFAULT_TABLET_MENU)


def clean_ops(raw) -> list[str]:
    """設定に保存する操作の一覧（空 = 全部外す）。知らないキーはエラー、重複は除く。"""
    from .exceptions import ApiError

    if not isinstance(raw, list):
        raise ApiError("validation_error", "タブレットに許可する操作の一覧を指定してください。")
    keys = set()
    for k in raw:
        if not isinstance(k, str) or k not in TABLET_OP_KEYS:
            raise ApiError("validation_error", f"タブレットに付けられない操作です: {k}")
        keys.add(k)
    return [k for k in TABLET_OP_KEYS if k in keys]


def resolve_ops(store) -> list[str]:
    """店舗設定の操作の一覧。未設定（None）や形の合わないものは既定。"""
    raw = getattr(getattr(store, "settings", None), "tablet_ops", None)
    if not isinstance(raw, list):
        return list(DEFAULT_TABLET_OPS)
    keys = {k for k in raw if isinstance(k, str)}
    return [k for k in TABLET_OP_KEYS if k in keys]


def tablet_caps(store) -> frozenset[str]:
    """タブレット用アカウントのケイパビリティ: いつも持つ操作 ＋ 店舗設定で許可した操作。"""
    ops = set(resolve_ops(store))
    return TABLET_BASE_CAPS.union(*(c for k, _l, c, _d in TABLET_OPS if k in ops))


def items_json() -> list[dict]:
    return [{"key": k, "label": label, "group": group, "tablet": group not in PC_ONLY_GROUPS} for k, label, group in TABLET_MENU_ITEMS]


def ops_json() -> list[dict]:
    return [{"key": k, "label": label, "default": d} for k, label, _c, d in TABLET_OPS]
