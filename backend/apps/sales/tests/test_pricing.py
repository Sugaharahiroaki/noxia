"""料金計算（compute_totals / round_yen / course_amount）の純粋関数テスト。"""
from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase

from apps.core.models import StoreSettings
from apps.core.money import D, round_yen, tax_from_included, tax_on_excluded
from apps.sales.pricing import LineInput, compute_totals
from apps.sales.services import course_amount


def st(**kw) -> StoreSettings:
    base = dict(
        price_basis="per_person", prices_include_tax=True, default_tax_rate=Decimal("10.00"),
        service_charge_enabled=False, service_charge_rate=Decimal("0"), service_charge_target="all",
        service_charge_order="before_tax", service_charge_after_discount=True,
        tax_rounding="floor", service_charge_rounding="floor", total_rounding_unit=1, total_rounding_mode="floor",
    )
    base.update(kw)
    return StoreSettings(**base)


def line(line_type: str, amount, rate="10.00", incl=True, id=None) -> LineInput:
    return LineInput(id=id, line_type=line_type, amount=D(amount), tax_rate=D(rate), tax_included=incl)


class MoneyTests(SimpleTestCase):
    def test_round_yen_modes_and_units(self):
        self.assertEqual(round_yen(Decimal("1234.7"), "floor"), Decimal("1234"))
        self.assertEqual(round_yen(Decimal("1234.2"), "ceil"), Decimal("1235"))
        self.assertEqual(round_yen(Decimal("1234.5"), "half_up"), Decimal("1235"))
        self.assertEqual(round_yen(Decimal("5555"), "floor", 10), Decimal("5550"))
        self.assertEqual(round_yen(Decimal("5555"), "ceil", 10), Decimal("5560"))
        self.assertEqual(round_yen(Decimal("5555"), "half_up", 10), Decimal("5560"))
        self.assertEqual(round_yen(Decimal("5549"), "half_up", 100), Decimal("5500"))
        self.assertEqual(round_yen(Decimal("5550"), "half_up", 100), Decimal("5600"))
        # 負の値は絶対値で丸めて符号を戻す（値引きの切り捨てが大きくならない）
        self.assertEqual(round_yen(Decimal("-1234.7"), "floor"), Decimal("-1234"))

    def test_tax_helpers(self):
        self.assertEqual(tax_from_included(Decimal("5000"), Decimal("10")), Decimal("454"))
        self.assertEqual(tax_on_excluded(Decimal("5000"), Decimal("10")), Decimal("500"))
        self.assertEqual(tax_from_included(Decimal("500"), Decimal("8")), Decimal("37"))
        self.assertEqual(tax_from_included(Decimal("500"), Decimal("0")), Decimal("0"))


class CourseAmountTests(SimpleTestCase):
    def test_per_person_vs_per_group(self):
        self.assertEqual(course_amount(st(price_basis="per_person"), Decimal("2500"), 3), Decimal("7500"))
        self.assertEqual(course_amount(st(price_basis="per_group"), Decimal("2500"), 3), Decimal("2500"))


class ComputeTotalsTests(SimpleTestCase):
    def test_hourly_plus_extension_is_5000(self):
        t = compute_totals([line("course_set", 2500), line("extension", 2500)], st())
        self.assertEqual(t.total, Decimal("5000"))
        self.assertEqual(t.subtotal, Decimal("5000"))
        self.assertEqual(t.tax_total, Decimal("454"))
        self.assertEqual(t.total_excl_tax, Decimal("4546"))
        self.assertEqual(t.breakdown["course_set"], Decimal("2500"))
        self.assertEqual(t.breakdown["extension"], Decimal("2500"))
        self.assertEqual(t.rounding_adjustment, Decimal("0"))

    def test_unlimited_from_start_is_3500(self):
        t = compute_totals([line("course_set", 3500)], st())
        self.assertEqual(t.total, Decimal("3500"))
        self.assertEqual(t.tax_total, Decimal("318"))

    def test_tax_excluded_prices(self):
        t = compute_totals([line("course_set", 2500, incl=False)], st(prices_include_tax=False))
        self.assertEqual(t.tax_total, Decimal("250"))
        self.assertEqual(t.total, Decimal("2750"))
        self.assertEqual(t.total_excl_tax, Decimal("2500"))
        self.assertEqual(t.tax_breakdown[0]["taxable_excl"], "2500")

    def test_service_charge_tax_included_prices(self):
        s = st(service_charge_enabled=True, service_charge_rate=Decimal("10"))
        t = compute_totals([line("course_set", 11000)], s)
        self.assertEqual(t.service_charge, Decimal("1100"))
        self.assertEqual(t.service_charge_tax, Decimal("0"))
        self.assertEqual(t.total, Decimal("12100"))
        self.assertEqual(t.tax_total, Decimal("1100"))

    def test_service_charge_before_tax_external(self):
        s = st(prices_include_tax=False, service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_order="before_tax")
        t = compute_totals([line("product", 10000, incl=False)], s)
        self.assertEqual(t.service_charge, Decimal("1000"))
        self.assertEqual(t.service_charge_tax, Decimal("100"))
        self.assertEqual(t.tax_total, Decimal("1100"))
        self.assertEqual(t.total, Decimal("12100"))

    def test_service_charge_after_tax_external(self):
        s = st(prices_include_tax=False, service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_order="after_tax")
        t = compute_totals([line("product", 10000, incl=False)], s)
        self.assertEqual(t.service_charge, Decimal("1100"))
        self.assertEqual(t.service_charge_tax, Decimal("0"))
        self.assertEqual(t.tax_total, Decimal("1000"))
        self.assertEqual(t.total, Decimal("12100"))
        rates = {row["rate"] for row in t.tax_breakdown}
        self.assertIn("0.00", rates)

    def test_service_charge_target(self):
        lines = [line("course_set", 5000), line("product", 3000)]
        t_course = compute_totals(lines, st(service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_target="course_only"))
        self.assertEqual(t_course.service_charge, Decimal("500"))
        self.assertEqual(t_course.total, Decimal("8500"))
        t_prod = compute_totals(lines, st(service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_target="products_only"))
        self.assertEqual(t_prod.service_charge, Decimal("300"))
        self.assertEqual(t_prod.total, Decimal("8300"))
        t_all = compute_totals(lines, st(service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_target="all"))
        self.assertEqual(t_all.service_charge, Decimal("800"))

    def test_service_charge_after_discount_flag(self):
        lines = [line("product", 10000), line("discount", -1000)]
        t_after = compute_totals(lines, st(service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_after_discount=True))
        self.assertEqual(t_after.service_charge, Decimal("900"))
        self.assertEqual(t_after.total, Decimal("9900"))
        t_before = compute_totals(lines, st(service_charge_enabled=True, service_charge_rate=Decimal("10"), service_charge_after_discount=False))
        self.assertEqual(t_before.service_charge, Decimal("1000"))
        self.assertEqual(t_before.total, Decimal("10000"))

    def test_discount(self):
        t = compute_totals([line("product", 10000), line("discount", -1000)], st())
        self.assertEqual(t.subtotal, Decimal("10000"))
        self.assertEqual(t.discount_total, Decimal("1000"))
        self.assertEqual(t.total, Decimal("9000"))
        self.assertEqual(t.tax_total, Decimal("818"))

    def test_mixed_tax_rates(self):
        t = compute_totals([line("product", 3000, "10.00"), line("product", 500, "8.00")], st())
        self.assertEqual(t.total, Decimal("3500"))
        self.assertEqual(t.tax_total, Decimal("309"))
        self.assertEqual(t.tax_breakdown[0]["rate"], "10.00")
        self.assertEqual(t.tax_breakdown[0]["tax"], "272")
        self.assertEqual(t.tax_breakdown[1]["rate"], "8.00")
        self.assertEqual(t.tax_breakdown[1]["tax"], "37")

    def test_rounding_units_keep_tax(self):
        lines = [line("product", 5555)]
        t = compute_totals(lines, st(total_rounding_unit=10, total_rounding_mode="floor"))
        self.assertEqual(t.total, Decimal("5550"))
        self.assertEqual(t.rounding_adjustment, Decimal("-5"))
        self.assertEqual(t.tax_total, Decimal("505"))
        self.assertEqual(t.total_excl_tax, Decimal("5045"))
        t = compute_totals(lines, st(total_rounding_unit=10, total_rounding_mode="ceil"))
        self.assertEqual(t.total, Decimal("5560"))
        self.assertEqual(t.rounding_adjustment, Decimal("5"))
        self.assertEqual(t.tax_total, Decimal("505"))
        t = compute_totals(lines, st(total_rounding_unit=10, total_rounding_mode="half_up"))
        self.assertEqual(t.total, Decimal("5560"))
        t = compute_totals(lines, st(total_rounding_unit=100, total_rounding_mode="floor"))
        self.assertEqual(t.total, Decimal("5500"))
        self.assertEqual(t.rounding_adjustment, Decimal("-55"))
        self.assertEqual(t.tax_total, Decimal("505"))

    def test_tax_rounding_mode(self):
        t = compute_totals([line("product", 5000)], st(tax_rounding="ceil"))
        self.assertEqual(t.tax_total, Decimal("455"))
        t = compute_totals([line("product", 5000)], st(tax_rounding="half_up"))
        self.assertEqual(t.tax_total, Decimal("455"))

    def test_as_dict_is_json_friendly(self):
        d = compute_totals([line("course_set", 2500)], st()).as_dict()
        self.assertEqual(d["total"], "2500")
        self.assertEqual(d["breakdown"]["course_set"], "2500")
        self.assertIn("settings_snapshot", d)
