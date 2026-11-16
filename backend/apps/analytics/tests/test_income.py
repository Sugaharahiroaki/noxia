"""M2: 日次・月次収支、現金の月間一覧。式は参考設計 6.6 の検算例で確かめる。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.analytics import reports
from apps.cash.models import CashMovement
from apps.cash.services import current_open_day, day_summary
from apps.core.business_date import business_date_now
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.staff.models import BackEntry, BackRule, Shift

PAST = timezone.localdate().replace(year=2020)


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class FormulaTests(SimpleTestCase):
    def test_reference_example_gross_profit_and_pay_ratio(self):
        # 売上 97,800・人件費 28,075 → 粗利 69,725、給率 28.71%
        m = reports.profit_metrics(sales=Decimal(97800), labor_cast=Decimal(28075))
        self.assertEqual(m["gross_profit"], Decimal(69725))
        self.assertEqual(m["pay_ratio"], Decimal("28.71"))

    def test_reference_example_per_guest_and_per_group(self):
        # 売上 488,300・41 人・20 組 → 客単価 11,910、組単価 24,415
        m = reports.profit_metrics(sales=Decimal(488300), guests=41, groups=20)
        self.assertEqual(m["per_guest"], Decimal(11910))
        self.assertEqual(m["per_group"], Decimal(24415))

    def test_income_expenses_and_staff_labor(self):
        m = reports.profit_metrics(sales=Decimal(100000), income=Decimal(5000), labor_cast=Decimal(20000), labor_staff=Decimal(10000), expenses=Decimal(3000))
        self.assertEqual((m["labor_total"], m["cost_total"], m["gross_profit"]), (Decimal(30000), Decimal(33000), Decimal(72000)))
        self.assertEqual(m["pay_ratio"], Decimal("20.00"))  # 給率の分子はキャストの人件費

    def test_zero_denominators(self):
        m = reports.profit_metrics(sales=Decimal(0))
        self.assertEqual((m["pay_ratio"], m["per_guest"], m["per_group"]), (None, None, None))

    def test_month_range(self):
        from datetime import date

        self.assertEqual(reports.month_range(2026, 2), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(reports.month_range(2026, 12), (date(2026, 12, 1), date(2026, 12, 31)))


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.today = business_date_now(self.store)
        self.cast1 = self.fx.employees["cast1"]   # 時給 1,500
        self.staff1 = self.fx.employees["staff1"]  # 時給 1,200

    def paid_visit(self, table=0, guests=2, method="cash", nominate=None, beer=0):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": guests, "course_id": self.fx.hourly.id}]})
        if nominate is not None:
            services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": nominate.id})
        if beer:
            services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products["beer"].id, "quantity": beer})
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return v, services.confirm_checkout(self.store, self.user, v.id, {"payments": [p]})

    def shift(self, emp, hours):
        start = timezone.now().replace(microsecond=0) - timedelta(hours=hours)
        return Shift.objects.create(employee=emp, store=self.store, business_date=self.today, clock_in=start, clock_out=start + timedelta(hours=hours))

    def move(self, kind, amount):
        return CashMovement.objects.create(store=self.store, business_day=current_open_day(self.store), kind=kind, amount=Decimal(amount), reason="t", occurred_at=timezone.now())

    def month(self):
        r = self.c.get("/api/reports/monthly", {"month": f"{self.today.year}-{self.today.month:02d}"})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def day(self):
        r = self.c.get("/api/reports/daily", {"date": self.today.isoformat()})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def today_row(self):
        return [r for r in self.month()["rows"] if r["date"] == self.today.isoformat()][0]


class DailyMonthlyTests(Base):
    def test_day_row_numbers(self):
        BackRule.objects.create(store=self.store, name="指名 500", target_type="nomination", calc_type="fixed", value=Decimal(500), effective_from=PAST)
        self.paid_visit(0, guests=2, method="cash", nominate=self.cast1)        # 5,000 + 1,000 = 6,000
        self.paid_visit(1, guests=1, method="card", beer=1)                      # 2,500 + 800 = 3,300
        self.shift(self.cast1, 4)                                                # 6,000
        self.shift(self.staff1, 5)                                               # 6,000
        self.move("expense", 1000)
        self.move("other_out", 200)
        self.move("other_in", 300)
        self.move("daily_pay", 5000)       # 粗利には含めない
        self.move("mid_collection", 9000)  # 粗利には含めない
        self.move("float_adjust_in", 7000) # 粗利には含めない
        r = self.today_row()
        D = Decimal
        self.assertEqual((D(r["sales"]), D(r["cash"]), D(r["card"]), D(r["receivable"])), (D(9300), D(6000), D(3300), D(0)))
        self.assertEqual((r["guests"], r["groups"], r["yobi_groups"]), (3, 2, 1))
        self.assertEqual((D(r["cast_sales"]), D(r["yobi_subtotal"]), D(r["nomination_count"])), (D(6000), D(6000), D(1)))  # 女子売上 = 指名のある伝票の本計
        self.assertEqual((D(r["labor_cast"]), D(r["labor_staff"])), (D(6500), D(6000)))  # 時給 6,000 + 指名バック 500 ／ スタッフ 6,000
        self.assertEqual((D(r["income"]), D(r["expenses"])), (D(300), D(1200)))
        self.assertEqual(D(r["gross_profit"]), D(9300) + D(300) - D(12500) - D(1200))
        self.assertEqual(r["pay_ratio"], "69.89")   # 6,500 / 9,300
        self.assertEqual((D(r["per_guest"]), D(r["per_group"])), (D(3100), D(4650)))
        # 人件費は既存の計算（shift_wage と確定バック）の結果そのもの
        from apps.staff.payroll import compute_statement

        st = compute_statement(self.store, self.cast1, self.today, self.today)
        self.assertEqual(D(r["labor_cast"]), D(st["gross_amount"]))

    def test_month_totals_equal_sum_of_days_and_total_row(self):
        self.paid_visit(0, method="cash")
        self.paid_visit(1, method="receivable")
        m = self.month()
        for k in ("sales", "cash", "card", "receivable", "gross_profit", "labor_cast"):
            self.assertEqual(Decimal(m["totals"][k]), sum(Decimal(r[k]) for r in m["rows"]), k)
        self.assertEqual(Decimal(m["totals"]["cash"]) + Decimal(m["totals"]["card"]) + Decimal(m["totals"]["receivable"]), Decimal(m["totals"]["sales"]))
        self.assertEqual(m["active_days"], 1)
        self.assertEqual(len(m["rows"]), reports.month_range(self.today.year, self.today.month)[1].day)
        self.assertEqual(Decimal(m["average"]["sales"]), Decimal(m["totals"]["sales"]))  # 伝票のある日は 1 日だけ
        self.assertIn("日払い", m["profit_kind_note"])
        self.assertEqual(Decimal(m["rows"][-1]["gross_profit_cumulative"]), Decimal(m["totals"]["gross_profit"]))

    def test_voided_and_cancelled_are_excluded(self):
        v, co = self.paid_visit(0)
        services.void_checkout(self.store, self.user, co.id, "打ち間違い")
        v2 = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[1].id, "entries": [{"guest_count": 3, "course_id": self.fx.hourly.id}]})
        services.cancel_visit(self.store, self.user, v2.id, "入力ミス")
        r = self.today_row()
        self.assertEqual(Decimal(r["sales"]), Decimal(0))
        self.assertEqual((r["guests"], r["groups"]), (2, 1))  # 会計を取り消した伝票は営業中に戻る（来店は残る）。取消の伝票は数えない

    def test_voided_cash_movement_and_pending_back_are_excluded(self):
        self.paid_visit(0)
        m = self.move("expense", 1000)
        m.is_voided = True
        m.save()
        BackEntry.objects.create(store=self.store, employee=self.cast1, business_date=self.today, amount=Decimal(999), status="pending")
        r = self.today_row()
        self.assertEqual((Decimal(r["expenses"]), Decimal(r["labor_cast"])), (Decimal(0), Decimal(0)))

    def test_adjustment_shown_as_bonus_or_minus(self):
        BackEntry.objects.create(store=self.store, employee=self.cast1, business_date=self.today, kind="adjustment", amount=Decimal(3000), status="confirmed")
        BackEntry.objects.create(store=self.store, employee=self.cast1, business_date=self.today, kind="adjustment", amount=Decimal(-1000), status="confirmed")
        r = self.today_row()
        self.assertEqual((Decimal(r["bonus"]), Decimal(r["minus"]), Decimal(r["labor_cast"])), (Decimal(3000), Decimal(1000), Decimal(2000)))

    def test_daily_report(self):
        v, co = self.paid_visit(0, nominate=self.cast1)
        self.move("expense", 500)
        d = self.day()
        self.assertEqual(Decimal(d["today"]["sales"]), Decimal(6000))
        self.assertEqual(Decimal(d["cumulative"]["sales"]), Decimal(6000))
        self.assertEqual((d["active_days"], Decimal(d["average"]["sales"])), (1, Decimal(6000)))
        self.assertEqual([s["id"] for s in d["slips"]], [v.id])
        self.assertEqual([(m["kind"], Decimal(m["amount"]), m["in_profit"]) for m in d["movements"]], [("expense", Decimal(500), True)])
        # 現金管理は既存の cash.day_summary の値そのまま
        s = day_summary(current_open_day(self.store))
        self.assertEqual((d["cash"]["theoretical_cash"], d["cash"]["cash_sales"]), (s["theoretical_cash"], s["cash_sales"]))
        self.assertIn("既存の式", d["cash_formula_note"])

    def test_other_store_not_mixed_and_permissions(self):
        other = make_store("s2", company_code="c2")
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 5, "course_id": other.hourly.id}]})
        services.confirm_checkout(other.store, self.user, ov.id, {"payments": [{"method": "card", "amount": "12500"}]})
        self.assertEqual(Decimal(self.today_row()["sales"]), Decimal(0))
        # dashboard.view が無い役割（スタッフ・キャスト）は見られない
        for who in ("staff", "cast"):
            c = client_for(self.fx.users[who], self.store)
            for path in ("/api/reports/monthly", "/api/reports/daily", "/api/reports/cash"):
                self.assertEqual(c.get(path).status_code, 403, (who, path))
        self.assertEqual(self.c.get("/api/reports/monthly", {"month": "2026-13"}).status_code, 400)


class CashMonthTests(Base):
    def test_cash_month_uses_existing_summary_and_closing_snapshot(self):
        self.c.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        self.paid_visit(0, method="cash")          # 5,000
        self.move("expense", 1000)
        self.move("other_in", 200)
        r = self.c.post("/api/cash/close", {"counted_cash": "34000", "next_day_float": "30000", "difference_reason": "原因不明"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        d = self.c.get("/api/reports/cash", {"month": f"{self.today.year}-{self.today.month:02d}"}).json()
        row = d["rows"][0]
        D = Decimal
        self.assertEqual((D(row["opening_float"]), D(row["cash_sales"]), D(row["cash_in"]), D(row["cash_out"])), (D(30000), D(5000), D(200), D(1000)))
        self.assertEqual((D(row["theoretical_cash"]), D(row["counted_cash"]), D(row["difference"]), D(row["set_aside_amount"]), D(row["next_day_float"])),
                         (D(34200), D(34000), D(-200), D(4000), D(30000)))
        self.assertEqual((D(d["totals"]["difference"]), D(d["totals"]["set_aside_amount"])), (D(-200), D(4000)))
        self.assertEqual(row["status"], "closed")
