"""
集計画面の表を、出力用の「シート」（名前・見出し・行）にする。XLSX 出力（M6）で使う。

- 数字は reports.py の結果をそのまま並べるだけ（ここで計算しない）。
- 見出しの業務用語は、店舗の名称変更（core.terms）に合わせて言い換える。
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from apps.core import terms
from apps.core.permissions import CAP_CUSTOMERS_VIEW, CAP_EXPORT_CASH, CAP_EXPORT_PAYROLL, CAP_EXPORT_SALES

from . import reports

# 出力の種類 → （必要なケイパビリティ, 再認証が要るか, 期間の指定の仕方）
KINDS: dict[str, tuple[tuple[str, ...], bool, str]] = {
    "slips": ((CAP_EXPORT_SALES,), False, "range"),
    "tags": ((CAP_EXPORT_SALES,), False, "range"),
    "items": ((CAP_EXPORT_SALES,), False, "range"),
    "customers": ((CAP_EXPORT_SALES, CAP_CUSTOMERS_VIEW), False, "none"),
    "deletions": ((CAP_EXPORT_SALES,), False, "range"),
    "monthly": ((CAP_EXPORT_CASH,), False, "month"),
    "cash": ((CAP_EXPORT_CASH,), False, "month"),
    "cash-history": ((CAP_EXPORT_CASH,), False, "month"),
    "cast": ((CAP_EXPORT_PAYROLL,), True, "month"),
    "staff": ((CAP_EXPORT_PAYROLL,), True, "month"),
}

_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")

PEOPLE_COLUMNS = [
    ("名前", "employee_name"), ("総支給額", "gross"), ("残り支給額", "balance"), ("オーダー小計", "order_subtotal"),
    ("呼び本計", "yobi_total"), ("呼び小計", "yobi_subtotal"), ("同伴本計", "douhan_total"), ("同伴小計", "douhan_subtotal"), ("給率(%)", "pay_ratio"),
    ("勤務日数", "days"), ("勤務分", "minutes"), ("平均時給", "avg_hourly"), ("時間報酬", "wage"),
    ("本指名 組数", "nomination_groups"), ("本指名 回数", "nomination_count"), ("本指名バック", "back_nomination"),
    ("場内指名 組数", "jounai_groups"), ("場内指名 回数", "jounai_count"), ("場内指名バック", "back_jounai"),
    ("呼び 組数", "yobi_groups"), ("呼び 回数", "yobi_count"), ("呼びバック", "back_yobi"),
    ("同伴 組数", "douhan_groups"), ("同伴 回数", "douhan_count"), ("同伴バック", "back_douhan"),
    ("呼び小計バック", "back_yobi_subtotal"), ("同伴小計バック", "back_douhan_subtotal"),
    ("ドリンク売上", "sales_drink"), ("ドリンク本数", "qty_drink"), ("ドリンクバック", "back_drink"),
    ("ボトル売上", "sales_bottle"), ("ボトル本数", "qty_bottle"), ("ボトルバック", "back_bottle"),
    ("フード売上", "sales_food"), ("フード回数", "qty_food"), ("フードバック", "back_food"),
    ("その他バック", "back_other"), ("バック計", "back_total"), ("ボーナス", "bonus"), ("マイナス", "minus"),
    ("控除", "deducted"), ("日払い", "paid"),
]


def num(v):
    """数字の文字列（"45100.00" など）を数値セルにする。それ以外はそのまま。None は空。"""
    if v is None:
        return ""
    if isinstance(v, str) and _NUMERIC.match(v):
        return Decimal(v)
    return v


def _rows(rows: list[list]) -> list[list]:
    return [[num(c) for c in r] for r in rows]


def _hm(iso: str | None) -> str:
    """ISO 日時 → 「MM/DD HH:MM」（日本時間）。"""
    if not iso:
        return ""
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    dt = parse_datetime(iso)
    return timezone.localtime(dt).strftime("%m/%d %H:%M") if dt else ""


def build(kind: str, store, *, f: date | None = None, t: date | None = None, year: int = 0, month: int = 0, group_by: str = "tag",
          today: date | None = None) -> tuple[str, list[tuple[str, list[str], list]]]:
    """戻り値: (ファイル名の本体, [(シート名, 見出し, 行)])。"""
    labels = terms.term_labels(store)
    tx = lambda s: terms.relabel(s, labels)  # noqa: E731
    head = lambda cols: [tx(c) for c in cols]  # noqa: E731
    span = f"{store.code}_{f.isoformat()}_{t.isoformat()}" if f and t else ""
    ym = f"{store.code}_{year}-{month:02d}" if year else ""

    if kind == "slips":
        d = reports.slip_list(store, f, t)
        header = ["営業日", "伝票ID", "伝票番号", "卓", "状態", "入店", "退店", "滞在(分)", "人数", "お客様", "タグ", "本指名", "場内指名", "呼び", "同伴",
                  "小計", "サービス料", "値引き", "現金", "カード", "売掛", "合計", "返金"]
        rows = [[r["business_date"], r["public_id"], r["slip_number"], r["table_name"], r["status_label"], _hm(r["opened_at"]), _hm(r["closed_at"]),
                 r["minutes"], r["guest_count"], r["customer"], "・".join(x["name"] for x in r["tags"]), "・".join(r["nomination"]),
                 "・".join(r["jounai"]), "・".join(r["yobi"]), "・".join(r["douhan"]), r["subtotal"], r["service_charge"], r["discount"], r["cash"], r["card"],
                 r["receivable"], r["total"], r["refunds"]] for r in d["rows"]]
        return f"slips_{span}", [("伝票一覧", head(header), _rows(rows))]

    if kind == "tags":
        d = reports.tag_report(store, f, t, group_by)
        rows = [[r["period"], r["tag_name"], r["count"], r["guests"], r["subtotal"], r["cash"], r["card"], r["receivable"], r["total"]] for r in d["rows"]]
        return f"tags_{span}", [(tx("タグ集計"), head(["期間", "タグ", "件数", "客数", "伝票小計", "現金", "カード", "売掛", "販売額"]), _rows(rows))]

    if kind == "items":
        d = reports.item_report(store, f, t)
        by_date = [[p["name"], p["category_name"], p["quantity"], p["amount"]] + [p["by_date"].get(x, "") for x in d["dates"]] for p in d["products"]]
        by_cast = [[c["employee_name"], it["name"], it["quantity"], it["amount"]] for c in d["casts"] for it in c["items"]]
        return f"items_{span}", [
            ("日付×商品", ["商品", "カテゴリー", "個数", "金額"] + d["dates"], _rows(by_date)),
            ("キャスト×商品", ["キャスト", "商品", "個数", "金額"], _rows(by_cast)),
        ]

    if kind == "customers":
        d = reports.customer_report(store, today)
        header = ["お客様", "登録店舗", "ランク", "キープ", "来店回数", "直近3か月の来店", "初回来店日", "最終来店日", "平均滞在(分)", "単価",
                  "会計", "現金", "カード", "売掛", "本指名", "場内指名", "呼び", "同伴", "担当"]
        rows = [[r["name"], r["store_name"], r["rank"], r["keep_count"], r["visits"], r["visits_recent"], r["first_visit"], r["last_visit"],
                 r["avg_minutes"], r["unit_price"], r["total"], r["cash"], r["card"], r["receivable"], r["nomination"], r["jounai"], r["yobi"], r["douhan"],
                 r["assigned_employee_name"]] for r in d["rows"]]
        return f"customers_{store.code}_{d['as_of']}", [("お客様分析", head(header), _rows(rows))]

    if kind == "deletions":
        d = reports.deletion_history(store, f, t)
        logs = [[_hm(r["at"]), r["action_label"], r["business_date"], r["public_id"], r["table_name"], r["detail"], r["amount"], r["reason"], r["user_name"]]
                for r in d["logs"]]
        cancelled = [[r["business_date"], r["public_id"], r["slip_number"], r["table_name"], r["guest_count"], r["customer"], _hm(r["opened_at"]),
                      _hm(r["closed_at"]), r["cancel_reason"]] for r in d["cancelled"]]
        return f"deletions_{span}", [
            ("取消の操作", ["操作日時", "操作", "営業日", "伝票ID", "卓", "内容", "金額", "理由", "操作した人"], _rows(logs)),
            ("取消済みの伝票", ["営業日", "伝票ID", "伝票番号", "卓", "人数", "お客様", "入店", "取消", "理由"], _rows(cancelled)),
        ]

    if kind == "monthly":
        d = reports.monthly_report(store, year, month)
        cols = [("営業日", "date"), ("売上", "sales"), ("現金", "cash"), ("カード", "card"), ("売掛", "receivable"), ("女子売上", "cast_sales"),
                ("来店組数", "groups"), ("来店人数", "guests"), ("客単価", "per_guest"), ("組単価", "per_group"),
                ("キャスト人件費", "labor_cast"), ("スタッフ人件費", "labor_staff"), ("入金（粗利対象）", "income"), ("出金（粗利対象）", "expenses"),
                ("経費計", "cost_total"), ("粗利", "gross_profit"), ("粗利累計", "gross_profit_cumulative"), ("給率(%)", "pay_ratio"),
                ("日払い（キャスト）", "daily_pay_cast"), ("日払い（スタッフ）", "daily_pay_staff"), ("返金", "refunds")]
        rows = [[{**d["totals"], "date": "合計"}.get(k) for _h, k in cols]] + [[r.get(k) for _h, k in cols] for r in d["rows"]]
        return f"monthly_{ym}", [("収支（月次）", [h for h, _k in cols], _rows(rows))]

    if kind == "cash":
        f2, t2 = reports.month_range(year, month)
        cols = [("営業日", "date"), ("状態", "status_label"), ("開始時釣銭", "opening_float"), ("現金売上", "cash_sales"), ("入金", "cash_in"),
                ("出金", "cash_out"), ("うち日払い", "daily_pay"), ("理論現金", "theoretical_cash"), ("実際の現金", "counted_cash"), ("過不足", "difference"),
                ("入金用取り分け", "set_aside_amount"), ("翌日釣銭", "next_day_float"), ("過不足の理由", "difference_reason")]
        rows = [[r.get(k) for _h, k in cols] for r in reports.cash_days(store, f2, t2)]
        return f"cash_{ym}", [("レジ精算（月間）", [h for h, _k in cols], _rows(rows))]

    if kind == "cash-history":
        d = reports.cash_history(store, year, month)
        summary = [["入金" if g["direction"] == "in" else "出金", g["label"], g["count"], g["amount"], "含める" if g["in_profit"] else "含めない"] for g in d["summary"]]
        rows = [[r["date"], _hm(r["occurred_at"]), "入金" if r["direction"] == "in" else "出金", r["label"], r["kind_label"], r["amount"], r["reason"],
                 r["employee_name"], "含める" if r["in_profit"] else "含めない", "取消" if r["is_voided"] else "", r["void_reason"], r["performed_by_name"]]
                for r in d["rows"]]
        return f"cash_history_{ym}", [
            ("項目ごとの合計", ["入金／出金", "項目", "件数", "金額", "粗利"], _rows(summary)),
            ("明細", ["営業日", "日時", "入金／出金", "項目", "種別", "金額", "内容", "従業員", "粗利", "取消", "取消の理由", "登録した人"], _rows(rows)),
        ]

    if kind in ("cast", "staff"):
        d = reports.people_report(store, year, month, cast=(kind == "cast"))
        cols = list(PEOPLE_COLUMNS)
        if d["settings"]["withholding_enabled"]:
            cols.append(("源泉所得税", "withheld"))
        rows = [[r.get(k) for _h, k in cols] for r in [{"employee_name": "合計", **d["totals"]}] + d["rows"]]
        return f"{kind}_report_{ym}", [("キャスト集計" if kind == "cast" else "スタッフ集計", head([h for h, _k in cols]), _rows(rows))]

    raise KeyError(kind)
