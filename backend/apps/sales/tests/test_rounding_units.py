"""
端数処理の単位: 消費税・サービス料・合計を、それぞれ「何円単位で」切り捨て・切り上げ・四捨五入するか。
単位はどれも 1 / 10 / 100 / 1000 円。1 円単位は 1 円未満の端数の処理（前からの動き）。
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.core.money import D
from apps.core.models import StoreSettings
from apps.core.testing import client_for, standard_fixture
from apps.sales.pricing import LineInput, compute_totals


def st(**kw) -> StoreSettings:
    base = dict(
        price_basis="per_person", prices_include_tax=True, default_tax_rate=Decimal("10.00"),
        service_charge_enabled=True, service_charge_rate=Decimal("20"), service_charge_target="all",
        service_charge_order="before_tax", service_charge_after_discount=True,
        tax_rounding="floor", service_charge_rounding="floor", total_rounding_unit=1, total_rounding_mode="floor",
    )
    base.update(kw)
    return StoreSettings(**base)


def line(line_type: str, amount, incl=True) -> LineInput:
    return LineInput(id=None, line_type=line_type, amount=D(amount), tax_rate=D("10.00"), tax_included=incl)


class RoundingUnitPricingTests(SimpleTestCase):
    # 内税・サービス料 20%: 4,100 円 × 20% = 820 円
    LINES = [line("course_set", 3500), line("product", 600)]

    def test_default_unit_is_one_yen(self):
        s = StoreSettings()
        self.assertEqual((s.tax_rounding_unit, s.service_charge_rounding_unit), (1, 1))
        t = compute_totals(self.LINES, st())
        self.assertEqual((t.service_charge, t.total), (Decimal("820"), Decimal("4920")))

    def test_service_charge_unit(self):
        t = compute_totals(self.LINES, st(service_charge_rounding="ceil", service_charge_rounding_unit=100))
        self.assertEqual((t.service_charge, t.total), (Decimal("900"), Decimal("5000")))
        t = compute_totals(self.LINES, st(service_charge_rounding="floor", service_charge_rounding_unit=100))
        self.assertEqual((t.service_charge, t.total), (Decimal("800"), Decimal("4900")))
        t = compute_totals(self.LINES, st(service_charge_rounding="half_up", service_charge_rounding_unit=100))
        self.assertEqual(t.service_charge, Decimal("800"))
        t = compute_totals(self.LINES, st(service_charge_rounding="ceil", service_charge_rounding_unit=10))
        self.assertEqual(t.service_charge, Decimal("820"))

    def test_tax_unit_internal(self):
        # 内税 5,000 円に含まれる消費税 454.54… 円。内税なので合計は変わらず、税額と税抜が変わる
        lines = [line("product", 5000)]
        t = compute_totals(lines, st(service_charge_enabled=False))
        self.assertEqual((t.tax_total, t.total), (Decimal("454"), Decimal("5000")))
        t = compute_totals(lines, st(service_charge_enabled=False, tax_rounding="floor", tax_rounding_unit=10))
        self.assertEqual((t.tax_total, t.total, t.total_excl_tax), (Decimal("450"), Decimal("5000"), Decimal("4550")))
        t = compute_totals(lines, st(service_charge_enabled=False, tax_rounding="ceil", tax_rounding_unit=10))
        self.assertEqual((t.tax_total, t.total), (Decimal("460"), Decimal("5000")))

    def test_tax_and_service_charge_units_external(self):
        # 外税: 5,050 円 → サービス料 10% 505 円 → 10 円単位で切り上げ 510 円。
        # 消費税は (5,050 + 510) × 10% = 556 円 → 10 円単位で切り捨て 550 円
        lines = [line("product", 5050, incl=False)]
        s = st(prices_include_tax=False, service_charge_rate=Decimal("10"),
               service_charge_rounding="ceil", service_charge_rounding_unit=10, tax_rounding="floor", tax_rounding_unit=10)
        t = compute_totals(lines, s)
        self.assertEqual(t.service_charge, Decimal("510"))
        self.assertEqual(t.service_charge_tax, Decimal("50"))
        self.assertEqual(t.tax_total, Decimal("550"))
        self.assertEqual(t.total, Decimal("6110"))

    def test_all_units_together(self):
        # サービス料 100 円単位で切り上げ、消費税 1 円未満切り捨て、合計 100 円単位で切り上げ
        lines = [line("course_set", 3500), line("product", 650)]  # 4,150 × 20% = 830 → 900、合計 5,050 → 5,100
        t = compute_totals(lines, st(service_charge_rounding="ceil", service_charge_rounding_unit=100,
                                     total_rounding_unit=100, total_rounding_mode="ceil"))
        self.assertEqual((t.service_charge, t.total_before_rounding, t.total), (Decimal("900"), Decimal("5050"), Decimal("5100")))

    def test_snapshot_keeps_units(self):
        t = compute_totals(self.LINES, st(tax_rounding_unit=10, service_charge_rounding_unit=100))
        self.assertEqual((t.settings_snapshot["tax_rounding_unit"], t.settings_snapshot["service_charge_rounding_unit"]), (10, 100))


class RoundingUnitApiTests(APITestCase):
    """セット 2,500円 × 2名 ＋ キャストドリンク 1,500円 = 6,500円（税込）、サービス料 15% = 975 円"""

    def setUp(self):
        self.fx = standard_fixture(service_charge_enabled=True, service_charge_rate=Decimal("15"))
        self.c = client_for(self.fx.users["manager"], self.fx.store)

    def key(self):
        return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}

    def test_settings_accept_only_known_units(self):
        r = self.c.put("/api/store/settings", {"tax_rounding_unit": 10, "service_charge_rounding_unit": 100}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["tax_rounding_unit"], r.json()["service_charge_rounding_unit"]), (10, 100))
        for k in ("tax_rounding_unit", "service_charge_rounding_unit", "total_rounding_unit"):
            for bad in (0, 5, 50, 500):
                r = self.c.put("/api/store/settings", {k: bad}, format="json")
                self.assertEqual(r.status_code, 400, (k, bad, r.content))
        self.fx.store.settings.refresh_from_db()
        self.assertEqual((self.fx.store.settings.tax_rounding_unit, self.fx.store.settings.service_charge_rounding_unit), (10, 100))

    def test_units_apply_to_quote_and_checkout(self):
        r = self.c.put("/api/store/settings", {"service_charge_rounding": "ceil", "service_charge_rounding_unit": 100}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]},
                        format="json", **self.key())
        self.assertEqual(r.status_code, 201, r.content)
        vid = r.json()["id"]
        r = self.c.post(f"/api/visits/{vid}/lines", {"kind": "product", "product_id": self.fx.products["cast_drink"].id, "quantity": 1},
                        format="json", **self.key())
        self.assertEqual(r.status_code, 201, r.content)
        q = self.c.get(f"/api/visits/{vid}").json()["quote"]
        self.assertEqual((Decimal(q["service_charge"]), Decimal(q["total"])), (Decimal("1000"), Decimal("7500")))
        r = self.c.post(f"/api/visits/{vid}/checkouts", {"payments": [{"method": "cash", "amount": "7500", "tendered": "10000"}]},
                        format="json", **self.key())
        self.assertEqual(r.status_code, 201, r.content)
        c = r.json()
        self.assertEqual((Decimal(c["service_charge"]), Decimal(c["total"])), (Decimal("1000"), Decimal("7500")))
        self.assertEqual((c["settings_snapshot"]["service_charge_rounding"], c["settings_snapshot"]["service_charge_rounding_unit"]), ("ceil", 100))
