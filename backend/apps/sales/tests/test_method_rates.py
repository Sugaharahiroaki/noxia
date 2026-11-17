"""
支払方法別のサービス料率（UNIFIED_PLAN S1）。
現金 15%・カード 20%（標準 20%）の店で、会計の支払方法ごとに金額が変わること、現金＋カードはカードの料率、
未設定のツケは標準の料率、決済端末にはカードの金額を送ること、バックは支払方法で変わらないことを確かめる。
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.core.testing import client_for, standard_fixture
from apps.sales.models import Checkout
from apps.sales.pricing import LineInput, compute_totals, has_method_rates, rate_method_for_payments
from apps.staff.models import BackEntry, BackRule
from apps.terminals.models import CardTerminal, TerminalConfig


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


def fake_st(**kw):
    base = dict(
        price_basis="per_person", prices_include_tax=True, default_tax_rate=Decimal("10"), service_charge_enabled=True,
        service_charge_rate=Decimal("20"), service_charge_target="all", service_charge_order="before_tax", service_charge_after_discount=True,
        tax_rounding="floor", service_charge_rounding="floor", total_rounding_unit=1, total_rounding_mode="floor",
        service_charge_rate_cash=None, service_charge_rate_card=None, service_charge_rate_receivable=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class RateRuleTests(SimpleTestCase):
    def test_rate_method_for_payments(self):
        self.assertEqual(rate_method_for_payments(["cash"]), "cash")
        self.assertEqual(rate_method_for_payments(["card"]), "card")
        self.assertEqual(rate_method_for_payments(["card", "cash"]), "card")  # 現金＋カードはカード
        self.assertEqual(rate_method_for_payments(["receivable"]), "receivable")
        self.assertEqual(rate_method_for_payments(["cash", "receivable"]), "receivable")

    def test_rates_by_method(self):
        st = fake_st(service_charge_rate_cash=Decimal("15"), service_charge_rate_card=Decimal("20"))
        lines = [LineInput(None, "course_set", Decimal("10000"), Decimal("10"), True)]
        self.assertEqual(compute_totals(lines, st, "cash").total, Decimal("11500"))
        self.assertEqual(compute_totals(lines, st, "card").total, Decimal("12000"))
        self.assertEqual(compute_totals(lines, st, "receivable").total, Decimal("12000"))  # 未設定は標準
        self.assertEqual(compute_totals(lines, st).total, Decimal("12000"))
        cash = compute_totals(lines, st, "cash")
        self.assertEqual((cash.method, cash.service_charge_rate), ("cash", Decimal("15")))
        self.assertEqual(cash.settings_snapshot["service_charge_rate"], "15")
        self.assertEqual(cash.settings_snapshot["service_charge_method"], "cash")
        self.assertEqual(cash.settings_snapshot["service_charge_rate_standard"], "20")

    def test_zero_rate_for_cash(self):
        st = fake_st(service_charge_rate_cash=Decimal("0"))
        lines = [LineInput(None, "product", Decimal("5000"), Decimal("10"), True)]
        t = compute_totals(lines, st, "cash")
        self.assertEqual((t.service_charge, t.total), (Decimal("0"), Decimal("5000")))

    def test_has_method_rates(self):
        self.assertFalse(has_method_rates(fake_st()))
        self.assertFalse(has_method_rates(fake_st(service_charge_rate_card=Decimal("20"))))  # 標準と同じ
        self.assertTrue(has_method_rates(fake_st(service_charge_rate_cash=Decimal("15"))))
        self.assertFalse(has_method_rates(fake_st(service_charge_enabled=False, service_charge_rate_cash=Decimal("15"))))

    def test_unknown_method_is_rejected(self):
        with self.assertRaises(ValueError):
            compute_totals([], fake_st(), "bitcoin")


class MethodRateApiTests(APITestCase):
    """セット 2,500円 × 2名 ＋ キャストドリンク 1,500円 = 6,500円（税込）。現金 15% → 7,475円、カード 20% → 7,800円。"""

    def setUp(self):
        self.fx = standard_fixture(service_charge_enabled=True, service_charge_rate=Decimal("20"),
                                   service_charge_rate_cash=Decimal("15"), service_charge_rate_card=Decimal("20"))
        self.store = self.fx.store
        self.c = client_for(self.fx.users["manager"], self.store)
        self.cast = self.fx.employees["cast1"]

    def open_visit(self, table=0):
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        vid = r.json()["id"]
        r = self.c.post(f"/api/visits/{vid}/lines", {"kind": "product", "product_id": self.fx.products["cast_drink"].id, "quantity": 1,
                                                     "back_targets": [{"employee_id": self.cast.id, "share_value": 100}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return vid

    def checkout(self, vid, payments, expect=201):
        r = self.c.post(f"/api/visits/{vid}/checkouts", {"payments": payments}, format="json", **key())
        self.assertEqual(r.status_code, expect, r.content)
        return r.json()

    def test_quote_shows_each_method(self):
        vid = self.open_visit()
        q = self.c.get(f"/api/visits/{vid}").json()["quote"]
        self.assertEqual(Decimal(q["total"]), Decimal("7800"))  # 支払方法が決まる前は標準の料率
        self.assertEqual(q["method"], "")
        self.assertEqual({m: (Decimal(v["rate"]), Decimal(v["total"])) for m, v in q["by_method"].items()},
                         {"cash": (Decimal("15"), Decimal("7475")), "card": (Decimal("20"), Decimal("7800")), "receivable": (Decimal("20"), Decimal("7800"))})
        self.assertEqual(Decimal(q["by_method"]["cash"]["service_charge"]), Decimal("975"))
        r = self.c.post(f"/api/visits/{vid}/quote", {"method": "cash"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((Decimal(r.json()["total"]), r.json()["method"], Decimal(r.json()["service_charge_rate"])), (Decimal("7475"), "cash", Decimal("15")))
        r = self.c.post(f"/api/visits/{vid}/quote", {"method": "bitcoin"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_cash_checkout_uses_cash_rate(self):
        vid = self.open_visit()
        c = self.checkout(vid, [{"method": "cash", "amount": "7475", "tendered": "10000"}])
        self.assertEqual(Decimal(c["total"]), Decimal("7475"))
        self.assertEqual(Decimal(c["service_charge"]), Decimal("975"))
        self.assertEqual(c["settings_snapshot"]["service_charge_rate"], "15.00")
        self.assertEqual(c["settings_snapshot"]["service_charge_method"], "cash")
        self.assertEqual(Decimal(c["payments"][0]["change_amount"]), Decimal("2525"))

    def test_cash_paying_the_card_amount_is_rejected(self):
        vid = self.open_visit()
        r = self.c.post(f"/api/visits/{vid}/checkouts", {"payments": [{"method": "cash", "amount": "7800", "tendered": "10000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["code"], "payment_mismatch")
        self.assertFalse(Checkout.objects.filter(visit_id=vid).exists())

    def test_card_and_mixed_use_card_rate(self):
        vid = self.open_visit()
        self.assertEqual(Decimal(self.checkout(vid, [{"method": "card", "amount": "7800"}])["total"]), Decimal("7800"))
        vid2 = self.open_visit(table=1)
        c = self.checkout(vid2, [{"method": "card", "amount": "3000"}, {"method": "cash", "amount": "4800", "tendered": "5000"}])
        self.assertEqual(Decimal(c["total"]), Decimal("7800"))
        self.assertEqual(c["settings_snapshot"]["service_charge_method"], "card")

    def test_receivable_without_own_rate_uses_standard(self):
        vid = self.open_visit()
        c = self.checkout(vid, [{"method": "receivable", "amount": "7800", "debtor_name": "田中様"}])
        self.assertEqual(Decimal(c["total"]), Decimal("7800"))

    def test_backs_do_not_change_with_payment_method(self):
        BackRule.objects.create(store=self.store, name="cast drink 50% (sc)", target_type="category_kind", category_kind="cast_drink",
                                calc_type="percent", value=Decimal(50), include_service_charge=True, effective_from=date(2020, 1, 1))
        basis = {}
        for i, payments in enumerate(([{"method": "cash", "amount": "7475", "tendered": "7475"}], [{"method": "card", "amount": "7800"}])):
            vid = self.open_visit(table=i)
            self.checkout(vid, payments)
            e = BackEntry.objects.get(visit_id=vid, order_line__isnull=False)
            basis[payments[0]["method"]] = (e.basis_amount, e.amount)
        # 1,500 + 標準 20% = 1,800 の 50%。現金で払っても 15% にはならない
        self.assertEqual(basis, {"cash": (Decimal("1800"), Decimal("900")), "card": (Decimal("1800"), Decimal("900"))})

    def test_terminal_sends_card_amount(self):
        TerminalConfig.objects.create(store=self.store, provider="mock")
        terminal = CardTerminal.objects.create(store=self.store, provider="mock", name="レジ横", device_id="mock_device_1", status="paired")
        vid = self.open_visit()
        r = self.c.post(f"/api/visits/{vid}/terminal-payments", {"terminal_id": terminal.id, "card_amount": "7475"}, format="json", **key())
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["error"]["code"], "amount_changed")
        r = self.c.post(f"/api/visits/{vid}/terminal-payments", {"terminal_id": terminal.id, "card_amount": "7800"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        r = self.c.post(f"/api/terminal-payments/{r.json()['id']}/simulate", {"result": "approve"}, format="json")
        self.assertEqual(r.json()["status"], "completed", r.content)
        self.assertEqual(Checkout.objects.get(visit_id=vid).total, Decimal("7800"))

    def test_store_without_method_rates_is_unchanged(self):
        st = self.fx.settings
        st.service_charge_rate_cash = None
        st.service_charge_rate_card = None
        st.save()
        vid = self.open_visit()
        q = self.c.get(f"/api/visits/{vid}").json()["quote"]
        self.assertEqual((Decimal(q["total"]), q["by_method"]), (Decimal("7800"), {}))
        self.assertEqual(Decimal(self.checkout(vid, [{"method": "cash", "amount": "7800", "tendered": "8000"}])["total"]), Decimal("7800"))

    def test_settings_api(self):
        r = self.c.put("/api/store/settings", {"service_charge_rate_cash": "12.5", "service_charge_rate_card": None}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((Decimal(r.json()["service_charge_rate_cash"]), r.json()["service_charge_rate_card"]), (Decimal("12.5"), None))
        r = self.c.put("/api/store/settings", {"service_charge_rate_card": "120"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
        r = self.c.put("/api/store/settings", {"service_charge_rate_receivable": "-1"}, format="json")
        self.assertEqual(r.status_code, 400, r.content)
