"""
伝票の中身（概算伝票・会計伝票・領収書・精算伝票・テスト印刷）。

金額はここで計算しない。会計の確定値（Checkout のスナップショット）、見積（sales.services.estimate_tiers）、
締めの集計（cash.services.day_summary）をそのまま並べるだけ。
"""
from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone

from apps.core.money import D, round_yen

from .models import PrinterConfig
from .slip import Slip

WEEKDAYS = "月火水木金土日"


def yen(v, *, minus: bool = False) -> str:
    n = int(round_yen(D(v), "half_up"))
    if minus and n:
        n = -abs(n)
    return f"-¥{abs(n):,}" if n < 0 else f"¥{n:,}"


def _rate(v) -> str:
    """10.00 → "10"、8.50 → "8.5"。"""
    return f"{D(v).normalize():f}"


def _dt(v: datetime) -> str:
    t = timezone.localtime(v)
    return f"{t.year}年{t.month}月{t.day}日({WEEKDAYS[t.weekday()]}) {t:%H:%M}"


def _d(v: date) -> str:
    return f"{v.year}年{v.month}月{v.day}日({WEEKDAYS[v.weekday()]})"


def _hm(v) -> str:
    if not v:
        return ""
    if isinstance(v, str):
        v = datetime.fromisoformat(v)
    return f"{timezone.localtime(v):%H:%M}"


def _shop_header(s: Slip, cfg: PrinterConfig, *, big: bool = True) -> None:
    s.text(cfg.display_shop_name(), align="center", size=2 if big else 1, bold=True)
    for a in (cfg.address1, cfg.address2, cfg.address3):
        if a:
            s.text(a, align="center")
    if cfg.phone:
        s.text(f"TEL {cfg.phone}", align="center")
    if cfg.invoice_number:
        s.text(f"登録番号 {cfg.invoice_number}", align="center")


def _tax_lines(s: Slip, tax_breakdown: list, prices_include_tax: bool, prefix: str = "") -> None:
    for tb in tax_breakdown or []:
        if D(tb.get("rate", 0)) == 0:
            continue
        label = "内消費税" if prices_include_tax else "消費税"
        s.text(f"{prefix}{_rate(tb['rate'])}%対象 {yen(tb['total_incl'])}（{label} {yen(tb['tax'])}）")


# --- 概算伝票 ----------------------------------------------------------------------
def estimate_slip(cfg: PrinterConfig, visit, tiers: list[dict], present_count: int) -> Slip:
    s = Slip(kind="estimate", title="概算伝票", columns=cfg.columns())
    _shop_header(s, cfg)
    s.text("ご利用料金のご案内（概算）", align="center", bold=True)
    s.rule()
    s.text(f"{visit.table.name}  伝票#{visit.slip_number}  {present_count}名様")
    entries = [e for e in visit.course_entries.all() if e.is_active]
    if entries:
        start = min(e.started_at for e in entries)
        end = tiers[0].get("planned_end_at") if tiers else None
        s.text(f"ご入店 {_hm(start)}" + (f"   終了予定 {_hm(end)}" if end else "   時間無制限"))
    for t in tiers:
        s.rule()
        if t["minutes"] == 0:
            s.text("いまお会計の場合", bold=True)
        else:
            s.text(f"{t['minutes']}分 延長した場合（{_hm(t['planned_end_at'])}まで）", bold=True)
            if t.get("extended_entries"):  # セットが複数あるときだけ入っている
                s.text("延長するセット: " + "・".join(t["extended_entries"]))
        s.pair("", yen(t["total"]), size=2, bold=True)
        bm = t.get("by_method") or {}
        if bm.get("cash") and bm.get("card"):
            s.pair("現金でのお支払い", yen(bm["cash"]))
            s.pair("カードでのお支払い", yen(bm["card"]))
    s.rule()
    s.text("※税・サービス料を含む金額です。")
    s.text("※ご注文を追加すると金額が変わります。")
    s.text(f"{_dt(timezone.now())} 現在", align="right")
    return s


# --- 会計伝票（レシート） ---------------------------------------------------------------
def _valid_payments(checkout) -> list:
    return [p for p in checkout.payments.all() if p.status == "valid"]


def receipt_slip(cfg: PrinterConfig, checkout) -> Slip:
    s = Slip(kind="receipt", title="会計伝票", columns=cfg.columns())
    _shop_header(s, cfg)
    s.rule()
    if checkout.status == "voided":
        s.text("＊＊ 取消済みの会計 ＊＊", align="center", bold=True)
    s.text(_dt(checkout.confirmed_at))
    s.text(f"会計#{checkout.number}  伝票#{checkout.visit.slip_number}  {checkout.visit.table.name}")
    s.rule()
    for ln in checkout.lines.all():
        if ln.status != "active":
            continue
        qty = D(ln.quantity)
        name = ln.name + (f" ×{qty.normalize():f}" if qty != 1 else "")
        s.pair(name, yen(ln.amount))
    s.rule()
    snap = checkout.settings_snapshot or {}
    incl = bool(snap.get("prices_include_tax", True))
    s.pair("小計", yen(checkout.subtotal))
    if D(checkout.discount_total) > 0:
        s.pair("値引き", yen(checkout.discount_total, minus=True))
    if D(checkout.service_charge) > 0:
        rate = snap.get("service_charge_rate")
        s.pair(f"サービス料（{_rate(rate)}%）" if rate else "サービス料", yen(checkout.service_charge))
    if not incl and D(checkout.tax_total) > 0:
        s.pair("消費税", yen(checkout.tax_total))
    if D(checkout.rounding_adjustment) != 0:
        s.pair("端数調整", yen(checkout.rounding_adjustment))
    s.pair("合計", yen(checkout.total), size=2, bold=True)
    _tax_lines(s, checkout.tax_breakdown, incl)
    s.rule()
    for p in _valid_payments(checkout):
        if p.method == "cash":
            s.pair("現金", yen(p.amount))
            if p.tendered is not None:
                s.pair("お預かり", yen(p.tendered))
                s.pair("お釣り", yen(p.change_amount))
        elif p.method == "card":
            s.pair("カード", yen(p.amount))
        elif p.method == "receivable":
            s.pair("後日のお支払い（未収）", yen(p.amount))
    if cfg.receipt_message:
        s.rule()
        s.text(cfg.receipt_message, align="center")
    return s


# --- 領収書 ------------------------------------------------------------------------
def ryoshusho_amounts(checkout) -> dict:
    pays = _valid_payments(checkout)
    cash = sum((D(p.amount) for p in pays if p.method == "cash"), D(0))
    card = sum((D(p.amount) for p in pays if p.method == "card"), D(0))
    receivable = sum((D(p.amount) for p in pays if p.method == "receivable"), D(0))
    return {"cash": cash, "card": card, "receivable": receivable, "amount": cash + card}


def ryoshusho_slip(cfg: PrinterConfig, checkout, *, addressee: str, proviso: str, reissue: bool) -> Slip:
    a = ryoshusho_amounts(checkout)
    s = Slip(kind="ryoshusho", title="領収書", columns=cfg.columns())
    s.text("領 収 書", align="center", size=2, bold=True)
    s.text(f"No.{checkout.number}" + ("（再発行）" if reissue else ""), align="right")
    s.feed()
    s.text(f"{addressee.strip() or '　' * 8} 様", size=1, bold=True)
    s.rule()
    s.text(f"{yen(a['amount'])}-", align="center", size=2, bold=True)
    s.rule()
    s.text(f"但し {proviso.strip() or cfg.ryoshusho_proviso}")
    s.text("上記正に領収いたしました。")
    s.text(_d(checkout.business_date) if checkout.business_date else _dt(checkout.confirmed_at))
    s.feed()
    snap = checkout.settings_snapshot or {}
    _tax_lines(s, checkout.tax_breakdown, bool(snap.get("prices_include_tax", True)), prefix="内訳 ")
    parts = []
    if a["cash"] > 0:
        parts.append(f"現金 {yen(a['cash'])}")
    if a["card"] > 0:
        parts.append(f"カード {yen(a['card'])}")
    if parts:
        s.text("お支払い: " + "・".join(parts))
    if a["receivable"] > 0:
        s.text(f"※後日のお支払い（未収）{yen(a['receivable'])} は含みません。")
    if cfg.stamp_box_from is not None and a["cash"] >= D(cfg.stamp_box_from):
        s.stamp()
    s.rule()
    _shop_header(s, cfg, big=False)
    return s


# --- 精算伝票（締め） ---------------------------------------------------------------
def closing_slip(cfg: PrinterConfig, day, summary: dict) -> Slip:
    closed = day.status == "closed"
    s = Slip(kind="closing", title="精算伝票", columns=cfg.columns())
    s.text("精算伝票" if closed else "精算伝票（締め前）", align="center", size=2, bold=True)
    s.text(cfg.display_shop_name(), align="center")
    s.text(f"営業日 {_d(day.date)}")
    if closed and day.closed_at:
        who = str(day.closed_by) if day.closed_by else ""
        s.text(f"締め {_dt(day.closed_at)}" + (f" {who}" if who else ""))
    s.text(f"印字 {_dt(timezone.now())}")
    s.rule()
    s.pair("売上合計", yen(summary["sales_total"]), bold=True)
    s.pair("会計件数", f"{summary['checkout_count']}件")
    s.pair("来店人数", f"{summary['guest_count']}名")
    s.pair("　現金売上", yen(summary["cash_sales"]))
    s.pair("　カード売上", yen(summary["card_sales"]))
    if D(summary["receivable_sales"]) > 0:
        s.pair("　未収（ツケ）", yen(summary["receivable_sales"]))
    if D(summary["cash_refunds"]) > 0 or D(summary["card_refunds"]) > 0:
        s.pair("返金（現金）", yen(summary["cash_refunds"], minus=True))
        s.pair("返金（カード）", yen(summary["card_refunds"], minus=True))
    if D(summary["receivable_collected_cash"]) > 0 or D(summary["receivable_collected_card"]) > 0:
        s.pair("未収の回収（現金）", yen(summary["receivable_collected_cash"]))
        s.pair("未収の回収（カード）", yen(summary["receivable_collected_card"]))
    s.rule()
    s.text("レジの現金", bold=True)
    s.pair("釣銭準備金", yen(summary["opening_float"]))
    s.pair("入金（その他）", yen(summary["cash_in_other"]))
    s.pair("経費（現金）", yen(summary["cash_out_expense"], minus=True))
    s.pair("日払い", yen(summary["cash_out_daily_pay"], minus=True))
    s.pair("その他の出金", yen(summary["cash_out_other"], minus=True))
    s.pair("途中回収", yen(summary["cash_out_mid_collection"], minus=True))
    s.pair("レジにあるはずの現金", yen(summary["theoretical_cash"]), bold=True)
    if closed:
        if day.counted_cash is not None:
            s.pair("数えた現金", yen(day.counted_cash))
        if day.difference is not None:
            diff = int(round_yen(D(day.difference), "half_up"))
            s.pair("過不足", ("+" if diff > 0 else "") + yen(diff))
        if day.next_day_float is not None:
            s.pair("翌日の釣銭準備金", yen(day.next_day_float))
    if D(summary.get("bank_deposits_total") or 0) > 0:
        s.pair("銀行への預け入れ", yen(summary["bank_deposits_total"]))
    return s


# --- テスト印刷 --------------------------------------------------------------------
def test_slip(cfg: PrinterConfig) -> Slip:
    s = Slip(kind="test", title="テスト印刷", columns=cfg.columns())
    _shop_header(s, cfg)
    s.rule()
    s.text("テスト印刷", align="center", size=2, bold=True)
    s.text(_dt(timezone.now()), align="center")
    s.text(f"{cfg.printer_name}" + (f"（{cfg.model_name}）" if cfg.model_name else ""), align="center")
    s.rule()
    s.pair("金額の例", yen(12345))
    s.text("この紙が出れば、iPad とプリンターはつながっています。")
    return s
