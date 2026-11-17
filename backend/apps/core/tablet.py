"""
店舗タブレット用アカウントに出す画面（メニュー）。

- 「タブレット用」の印（StoreMembership.is_tablet）が付いたアカウントでログインすると、メニューはここで決めた画面だけになる。
  管理・経理・設定は PC から行う（オーナーの方針）。画面側（AppShell）が同じキー（画面の URL）で出し分ける。
- 出す画面は店舗設定（StoreSettings.tablet_menu）で変えられる。空なら既定（営業の画面だけ）。
- これは表示の出し分け。API の権限（ケイパビリティ）はこれまでどおり役割で決まる。
"""
from __future__ import annotations

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
    ("/settings/store", "店舗設定・権限", "設定"),
    ("/settings/reports", "入出金項目・名称変更", "設定"),
]
TABLET_MENU_KEYS = [k for k, _l, _g in TABLET_MENU_ITEMS]
# 既定: 営業の画面だけ（卓一覧・入店・開店と日締めと現金・売上と会計履歴・領収書と経費・出勤と日払い）
DEFAULT_TABLET_MENU = ["/floor", "/visits/new", "/closing", "/sales", "/receipts", "/attendance"]


def clean_menu(raw) -> list[str]:
    """設定に保存する一覧。知らないキーはエラー、重複は除く。順番は TABLET_MENU_ITEMS の順に揃える。"""
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
    """店舗設定の一覧（形の合わないものは捨てる）。空なら既定。"""
    raw = getattr(getattr(store, "settings", None), "tablet_menu", None)
    keys = [k for k in raw if isinstance(k, str) and k in TABLET_MENU_KEYS] if isinstance(raw, list) else []
    return [k for k in TABLET_MENU_KEYS if k in set(keys)] if keys else list(DEFAULT_TABLET_MENU)


def items_json() -> list[dict]:
    return [{"key": k, "label": label, "group": group} for k, label, group in TABLET_MENU_ITEMS]
