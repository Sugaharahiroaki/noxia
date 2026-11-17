"""
操作履歴（AuditLog）の表示用の日本語。

- action（例: visit.cancel）→ 操作の名前、target_type（例: Visit）→ 対象の名前、changes（JSON）→ 読める 1 行。
- 表示のためだけのもの。記録そのものは変えない。知らない action はそのまま出す。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

ACTION_LABELS: dict[str, str] = {
    # 認証
    "auth.login": "ログイン", "auth.logout": "ログアウト", "auth.reauth": "パスワードの再確認", "auth.reauth_failed": "パスワードの再確認に失敗",
    "auth.select_store": "店舗の選択", "auth.change_password": "パスワードの変更", "auth.accept_invite": "招待の受諾",
    # 営業（伝票）
    "visit.open": "入店", "visit.add_course": "コースの追加", "visit.extend": "延長", "visit.change_unlimited": "無制限へ変更",
    "visit.guest_leave": "お客様の退店", "visit.tags": "タグの変更", "visit.move_table": "席移動", "visit.change_time": "開始時刻の訂正",
    "visit.cancel": "伝票の取消",
    "line.add.batch": "注文の追加", "line.add.product": "商品の追加", "line.add.nomination": "本指名の追加", "line.add.jounai": "場内指名の追加", "line.add.yobi": "呼びの追加",
    "line.add.douhan": "同伴の追加", "line.add.discount": "値引きの追加", "line.cancel": "明細の取消", "line.cancel.batch": "明細の取消（まとめて）",
    "line.back_targets": "バック対象の変更",
    "checkout.confirm": "会計の確定", "checkout.void": "会計の取消", "checkout.refund": "返金",
    "checkout.delete": "会計の取消（記録ごと削除）",  # いまは書かない。以前に書かれた記録の表示用
    "receivable.collect": "売掛の回収",
    # レジ・営業日
    "day.open": "開店", "day.close": "レジ締め", "day.reopen": "営業日の再開", "day.revise": "締めの修正", "day.bank_deposit": "銀行入金",
    "cash.movement.expense": "入出金の登録（現金経費）", "cash.movement.daily_pay": "入出金の登録（日払い）", "cash.movement.other_in": "入出金の登録（その他入金）",
    "cash.movement.other_out": "入出金の登録（その他出金）", "cash.movement.mid_collection": "入出金の登録（途中回収）", "cash.movement.float_adjust_in": "入出金の登録（釣銭補充）",
    "cash.movement.void": "入出金の取消", "cash.item.create": "入出金項目の追加", "cash.item.update": "入出金項目の変更", "cash.item.deactivate": "入出金項目の停止",
    # 勤怠・報酬
    "shift.clock_in": "出勤", "shift.clock_out": "退勤", "shift.break_start": "休憩の開始", "shift.break_end": "休憩の終了", "shift.correct": "勤怠の修正", "shift.create": "勤怠の追加",
    "payout.daily": "日払いのお渡し", "employee_payment.create": "支払いの記録", "pay_period.create": "支払期間の作成", "pay_period.close": "支払期間の締め",
    "back.adjust": "バックの調整", "back_rule.create": "バックルールの追加", "back_rule.update": "バックルールの変更", "back_rule.deactivate": "バックルールの停止", "back_rule.designation_update": "指名・同伴のバックの変更",
    "wage.set": "時給の設定",
    "employee.create": "従業員の追加", "employee.update": "従業員の変更", "employee.assignment": "配属の変更", "employee.login_account": "ログインアカウントの設定",
    # マスタ・設定
    "catalog.category.create": "カテゴリーの追加", "catalog.category.update": "カテゴリーの変更", "catalog.category.deactivate": "カテゴリーの停止",
    "catalog.product.create": "商品の追加", "catalog.product.update": "商品の変更", "catalog.product.deactivate": "商品の停止",
    "catalog.course.create": "コースの追加", "catalog.course.update": "コースの変更", "catalog.course.deactivate": "コースの停止", "catalog.course.copy": "コースの複製",
    "catalog.table.create": "卓の追加", "catalog.table.update": "卓の変更", "catalog.table.deactivate": "卓の停止",
    "catalog.menu.load": "メニューの一括登録",
    "sales.tag.create": "タグの追加", "sales.tag.update": "タグの変更", "sales.tag.deactivate": "タグの停止",
    "settings.update": "店舗設定の変更", "settings.void_pin": "取消用の暗証番号の設定", "terms.update": "名称の変更", "terms.reset": "名称を初期値に戻す",
    "settings.tablet": "タブレットの設定", "settings.tablet.account_create": "タブレット用アカウントの作成", "settings.tablet.account_password": "タブレット用アカウントのパスワード変更",
    "store.create": "店舗の追加", "store.update": "店舗の変更", "store.membership.update": "店舗の権限の変更",
    "company.update": "会社の変更", "company.invite": "招待", "company.membership.update": "会社の権限の変更",
    # 顧客・予約・在庫
    "customer.create": "顧客の追加", "customer.update": "顧客の変更", "customer.deactivate": "顧客の停止",
    "bottle_keep.create": "ボトルキープの登録", "bottle_keep.update": "ボトルキープの変更",
    "reservation.create": "予約の追加", "reservation.update": "予約の変更", "reservation.seat": "予約からの入店",
    "stock.take": "棚卸し", "stock.receive": "入荷", "stock.waste": "廃棄", "stock.adjust": "在庫の調整",
    "po.create": "発注の作成", "po.update": "発注の変更", "po.receive": "入荷の登録",
    # 出力
    "export": "ファイル出力", "download": "ダウンロード", "export.po_csv": "発注 CSV の出力",
    # カード端末
    "terminal.config.update": "カード端末の設定", "terminal.device.add": "端末の追加", "terminal.device.update": "端末の変更", "terminal.device.pair_start": "端末のペアリング",
    "terminal.payment.request": "端末へ送信", "terminal.payment.cancel": "端末の送信取消", "terminal.payment.resolve": "端末の結果確認", "terminal.payment.status": "端末の状態確認",
    "terminal.payment.simulate": "端末の模擬操作",
    # 領収書・LINE
    "expenses.receipt.create": "領収書の登録", "expenses.receipt.edit": "領収書の修正", "expenses.receipt.file": "領収書の画像",
    "line.integration.update": "LINE 連携の設定", "line.user.create": "LINE 友だちの登録", "line.user.update": "LINE 友だちの変更",
    "line.draft.create": "LINE 配信の下書き", "line.draft.review": "LINE 配信の確認", "line.draft.send": "LINE 配信", "line.draft.blocked": "LINE 配信の停止",
}

# 前方一致で決める家族（上の表に無いとき）。後ろの語（create/update/void など）を日本語にする
FAMILY_LABELS: dict[str, str] = {
    "export.report": "集計の出力", "stock": "在庫", "po": "発注", "accounting": "経理", "banking": "銀行", "closing": "決算", "tax": "税務",
    "expenses": "経費", "platform": "運営者", "terminal": "カード端末", "line": "LINE", "catalog": "マスタ", "shift": "勤怠",
}
VERB_LABELS: dict[str, str] = {
    "create": "追加", "update": "変更", "delete": "削除", "deactivate": "停止", "void": "取消", "cancel": "取消", "confirm": "確定",
    "send": "送信", "upload": "アップロード", "download": "ダウンロード", "export": "出力", "compute": "計算", "close": "締め", "reopen": "再開",
    "approve": "承認", "reject": "却下", "return": "差戻し", "review": "確認", "resolve": "確定", "build": "作成", "manual": "手入力",
}

TARGET_LABELS: dict[str, str] = {
    "Visit": "伝票", "Checkout": "会計", "OrderLine": "明細", "CourseEntry": "セット", "Refund": "返金", "Receivable": "売掛",
    "BusinessDay": "営業日", "CashMovement": "入出金", "CashItem": "入出金項目", "BankDeposit": "銀行入金",
    "Shift": "勤怠", "EmployeePayment": "支払い", "BackEntry": "バック", "BackRule": "バックルール", "WageSetting": "時給", "Employee": "従業員",
    "Category": "カテゴリー", "Product": "商品", "Course": "コース", "Table": "卓", "Tag": "タグ", "StoreSettings": "店舗設定", "Store": "店舗",
    "Customer": "顧客", "BottleKeep": "ボトルキープ", "Reservation": "予約", "User": "ユーザー", "Company": "会社", "Invitation": "招待",
    "TerminalDevice": "端末", "TerminalPayment": "端末決済", "StockMovement": "在庫の移動", "PurchaseOrder": "発注", "ExpenseReceipt": "領収書",
}

KEY_LABELS: dict[str, str] = {
    "amount": "金額", "total": "合計", "subtotal": "小計", "reason": "理由", "method": "支払方法", "payments": "支払い", "tendered": "お預かり",
    "change": "お釣り", "table": "卓", "table_id": "卓", "guest_count": "人数", "present_count": "在席", "name": "名前", "display_name": "表示名",
    "line_id": "明細", "line_ids": "明細", "kind": "種別", "mode": "方法", "item": "項目", "filename": "ファイル", "date": "営業日", "business_date": "営業日",
    "opening_float": "釣銭準備金", "counted_cash": "実際の現金", "difference": "過不足", "next_day_float": "翌日釣銭", "employee_id": "従業員", "employee": "従業員",
    "visit_id": "伝票", "checkout_id": "会計", "number": "会計番号", "started_at": "開始", "planned_end_at": "終了予定", "entry_id": "セット", "minutes": "分",
    "tags": "タグ", "price": "価格", "is_active": "使用中", "sort_order": "並び順", "color": "色", "note": "メモ", "hourly_wage": "時給", "effective_from": "適用日",
    "status": "状態", "before": "変更前", "after": "変更後", "kept_backs": "残したバック", "card": "カード払い", "label": "表示名", "labels": "名称",
    "customer_name": "お客様", "customer_id": "顧客", "course_id": "コース", "course": "コース", "option_id": "延長の候補", "direction": "入金／出金",
    "affects_profit": "粗利に含める", "quantity": "数量", "short_name": "略称", "refunds": "返金", "store": "店舗", "role": "役割", "user": "ユーザー",
    "username": "ユーザー名", "email": "メール", "discount": "値引き", "service_charge": "サービス料", "tax_total": "消費税", "items": "項目", "count": "件数",
    "created": "追加", "updated": "変更",
    "nomination": "本指名", "jounai": "場内指名", "yobi": "呼び", "douhan": "同伴",
    "nomination_subtotal": "本指名のある会計", "jounai_subtotal": "場内指名のある会計", "yobi_subtotal": "呼びのある会計", "douhan_subtotal": "同伴のある会計",
}

VALUE_LABELS: dict[str, dict[str, str]] = {
    "method": {"cash": "現金", "card": "カード", "receivable": "売掛"},
    "mode": {"refund": "お客様に全額返す", "redo": "打ち間違い", "net": "手取り"},
    "kind": {
        "expense": "現金経費", "daily_pay": "日払い", "other_in": "その他入金", "other_out": "その他出金", "mid_collection": "途中回収", "float_adjust_in": "釣銭補充",
        "product": "商品", "nomination": "本指名", "jounai": "場内指名", "yobi": "呼び", "douhan": "同伴", "discount": "値引き", "full": "全額", "partial": "一部", "daily": "日払い",
    },
    "status": {"open": "営業中", "closed": "退店", "cancelled": "取消", "confirmed": "確定", "voided": "取消", "pending": "未確定", "active": "使用中"},
    "direction": {"in": "入金", "out": "出金"},
    "role": {"owner": "オーナー", "manager": "店長", "staff": "スタッフ", "cast": "キャスト", "accountant": "経理"},
}
MAX_SUMMARY = 240


def action_label(action: str) -> str:
    if action in ACTION_LABELS:
        return ACTION_LABELS[action]
    parts = action.split(".")
    for n in range(len(parts) - 1, 0, -1):
        family = ".".join(parts[:n])
        if family in FAMILY_LABELS:
            rest = [VERB_LABELS.get(p, p) for p in parts[n:]]
            return f"{FAMILY_LABELS[family]}: {'・'.join(rest)}"
    return action


def target_label(target_type: str) -> str:
    return TARGET_LABELS.get(target_type, target_type)


def _money(v) -> str:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return str(v)
    if d == d.to_integral_value():
        return f"{int(d):,}"
    return f"{d.normalize():f}"


def _value(key: str, v) -> str:
    if isinstance(v, bool):
        return "はい" if v else "いいえ"
    if v is None or v == "":
        return "--"
    if isinstance(v, dict):
        if set(v) == {"before", "after"}:
            return f"{_value(key, v['before'])} → {_value(key, v['after'])}"
        return "、".join(f"{KEY_LABELS.get(k, k)} {_value(k, x)}" for k, x in v.items())
    if isinstance(v, list):
        if key == "payments":
            return " / ".join(f"{VALUE_LABELS['method'].get(str(p.get('method')), p.get('method'))} {_money(p.get('amount'))}" if isinstance(p, dict) else str(p) for p in v)
        if all(isinstance(x, (int, str)) for x in v):
            return f"{len(v)} 件" if key.endswith("_ids") else "、".join(str(x) for x in v)
        return f"{len(v)} 件"
    s = str(v)
    if key in VALUE_LABELS and s in VALUE_LABELS[key]:
        return VALUE_LABELS[key][s]
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit():
        return s.replace("-", "/")  # 日付は 2026/11/17 の形に
    if key in ("amount", "total", "subtotal", "tendered", "change", "opening_float", "counted_cash", "difference", "next_day_float", "price", "kept_backs",
               "hourly_wage", "discount", "service_charge", "tax_total", "refunds"):
        return _money(s)
    return s


def changes_summary(changes) -> str:
    """changes（JSON）を「金額 7,000、理由 入力ミス」のような 1 行にする。長いときは切る。"""
    if not isinstance(changes, dict) or not changes:
        return ""
    parts = []
    for k, v in changes.items():
        if k in ("request_id", "id"):
            continue
        parts.append(f"{KEY_LABELS.get(k, k)} {_value(k, v)}")
    s = "、".join(parts)
    return s if len(s) <= MAX_SUMMARY else s[: MAX_SUMMARY - 1] + "…"
