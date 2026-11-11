"""経理テスト用の部品。勤務日は「先月」を使う（時給設定の適用開始日が100日前のため、いつ実行しても時給がある）。"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from apps.accounting.models import CastContract, StoreTaxProfile, TaxPayer
from apps.core.business_date import TOKYO
from apps.core.models import StoreSettings
from apps.staff.models import BackEntry, Shift


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


def tiny_png(width: int = 1200, height: int = 900) -> bytes:
    """アップロード検査（中身の判定）を通る本物の PNG。既定は領収書の目安（短辺 450・長辺 1000 以上）を満たす大きさ。"""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (250, 250, 240)).save(buf, format="PNG")
    return buf.getvalue()


def tiny_jpeg(width: int = 1200, height: int = 900) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (255, 255, 255)).save(buf, format="JPEG")
    return buf.getvalue()


def tiny_pdf() -> bytes:
    """pypdf で読める 1 ページの PDF。"""
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def prev_month_first() -> date:
    first = timezone.localdate().replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def enable_withholding(store, on: bool = True) -> None:
    """店舗設定の「日払いで源泉所得税を差し引く」（既定はオフ）。"""
    StoreSettings.objects.filter(store=store).update(withholding_enabled=on)
    store.refresh_from_db()


def make_taxpayer(fx, name="株式会社テスト", entity_type="corporation", withholding: bool = True, **kw) -> TaxPayer:
    """事業者を作って fx の店舗に設定する。源泉徴収のテスト用に、店舗の源泉所得税の差し引きもオンにする。"""
    tp = TaxPayer.objects.create(company=fx.company, name=name, entity_type=entity_type, tax_office_name="麹町", reference_number="0123456789",
                                 address="東京都千代田区1-1", **kw)
    StoreTaxProfile.objects.create(store=fx.store, taxpayer=tp)
    if withholding:
        enable_withholding(fx.store)
    return tp


def contract(fx, emp, *, category="hostess_fee", confirmed=True, period="daily", contract_type="outsourcing", deductions=None, start=None) -> CastContract:
    return CastContract.objects.create(
        company=fx.company, employee=emp, contract_type=contract_type, start_date=start or date(2020, 1, 1), calc_period=period,
        tax_category=category, tax_review_status="confirmed" if confirmed else "unreviewed", deductions=deductions or [],
    )


def work(store, emp, d: date, hours: int = 8, back: Decimal | int = 0, start_hour: int = 20):
    """d の営業日に hours 時間勤務（退勤済み）し、back 円の確定バックが付いた状態を作る。"""
    ci = datetime.combine(d, time(start_hour, 0), tzinfo=TOKYO)
    Shift.objects.create(employee=emp, store=store, business_date=d, clock_in=ci, clock_out=ci + timedelta(hours=hours))
    if back:
        BackEntry.objects.create(store=store, employee=emp, kind="adjustment", business_date=d, amount=Decimal(back), status="confirmed",
                                 confirm_timing="order", confirmed_at=timezone.now(), reason="テスト")


def pay(client, emp, d: date, **extra):
    return client.post("/api/staff/daily-payouts/pay", {"employee_id": emp.id, "work_date": d.isoformat(), **extra}, format="json", **key())


def payout(client, emp, d: date) -> dict:
    rows = client.get(f"/api/staff/daily-payouts?date={d.isoformat()}").json()["rows"]
    return next(r for r in rows if r["employee_id"] == emp.id)
