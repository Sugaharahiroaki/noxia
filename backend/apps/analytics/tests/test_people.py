"""M3: キャスト・スタッフ集計と報酬明細。伝票の小計に対するバック（A8）。式は参考設計 6.4 の検算例で確かめる。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import Product
from apps.core.business_date import business_date_now, store_tz
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.staff import payroll
from apps.staff.models import BackEntry, BackRule, EmployeePayment, Shift, WageSetting

PAST = timezone.localdate().replace(year=2020)
D = Decimal


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.today = business_date_now(self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        self.staff1 = self.fx.employees["staff1"]
        self.month = f"{self.today.year}-{self.today.month:02d}"

    def rule(self, **kw):
        base = dict(store=self.store, name="r", calc_type="percent", value=D(5), effective_from=PAST)
        base.update(kw)
        return BackRule.objects.create(**base)

    def visit(self, table=0, guests=1):
        return services.open_visit(self.store, self.user, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": guests, "course_id": self.fx.hourly.id}]})

    def pay(self, v, method="card", **extra):
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return services.confirm_checkout(self.store, self.user, v.id, {"payments": [p], **extra})

    def cast_report(self, client=None):
        r = (client or self.owner).get("/api/reports/cast", {"month": self.month})
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def person(self, emp, client=None):
        return (client or self.owner).get(f"/api/reports/people/{emp.id}", {"month": self.month})


class SubtotalBackTests(Base):
    def test_no_rule_creates_nothing(self):
        v = self.visit()
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})
        self.pay(v)
        self.assertFalse(BackEntry.objects.filter(kind__in=["yobi_subtotal", "douhan_subtotal"]).exists())

    def test_yobi_subtotal_back_and_split_between_casts(self):
        self.rule(target_type="nomination_subtotal", value=D(5))
        self.fx.settings.jounai_fee = D(800)
        self.fx.settings.save()
        v = self.visit(guests=2)                                                  # 5,000
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})   # 1,000
        services.add_fee_line(self.store, self.user, v.id, "jounai", {"employee_id": self.cast2.id})       # 800
        co = self.pay(v)                                                          # 小計 6,800
        got = {b.employee_id: (b.basis_amount, b.amount, b.status) for b in BackEntry.objects.filter(checkout=co, kind="yobi_subtotal")}
        self.assertEqual(got, {self.cast1.id: (D(3400), D(170), "confirmed"), self.cast2.id: (D(3400), D(170), "confirmed")})

    def test_douhan_subtotal_and_discount_flag_and_individual_rule(self):
        self.rule(target_type="douhan_subtotal", value=D(5), include_discount=True)
        self.rule(target_type="douhan_subtotal", value=D(10), employee=self.cast2, include_discount=False)
        v = self.visit(guests=2)                                                  # 5,000
        services.add_fee_line(self.store, self.user, v.id, "douhan", {"employee_id": self.cast1.id})        # 2,000
        services.add_discount(self.store, self.user, v.id, {"amount": "1000", "reason": "サービス"})
        co = self.pay(v)                                                          # 小計 7,000、値引き 1,000
        b = BackEntry.objects.get(checkout=co, kind="douhan_subtotal")
        self.assertEqual((b.employee_id, b.basis_amount, b.amount), (self.cast1.id, D(6000), D(300)))  # 値引き後 6,000 × 5%
        v2 = self.visit(table=1, guests=2)
        services.add_fee_line(self.store, self.user, v2.id, "douhan", {"employee_id": self.cast2.id})
        services.add_discount(self.store, self.user, v2.id, {"amount": "1000", "reason": "サービス"})
        co2 = self.pay(v2)
        b2 = BackEntry.objects.get(checkout=co2, kind="douhan_subtotal")
        self.assertEqual((b2.basis_amount, b2.amount), (D(7000), D(700)))  # 個人別ルール 10%・値引きを反映しない

    def test_void_reverses_and_delete_removes(self):
        self.rule(target_type="nomination_subtotal", value=D(10))
        v = self.visit()
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})
        co = self.pay(v)                                                          # 小計 3,500 → 350
        self.assertEqual(payroll.back_total(self.store, self.cast1, self.today, self.today), D(350))
        services.void_checkout(self.store, self.user, co.id, "打ち間違い")
        self.assertEqual(payroll.back_total(self.store, self.cast1, self.today, self.today), D(0))  # 打消しで 0
        # 会計し直す → もう一度付く。会計の削除（記録ごと消す）では、そのエントリも消える
        co2 = self.pay(v)
        self.assertEqual(payroll.back_total(self.store, self.cast1, self.today, self.today), D(350))
        services.delete_checkout(self.store, self.user, co2.id)
        self.assertEqual(payroll.back_total(self.store, self.cast1, self.today, self.today), D(0))
        self.assertFalse(BackEntry.objects.filter(checkout_id=co2.id).exists())

    def test_existing_fee_back_rules_are_not_affected(self):
        # 指名料そのもののバック（既存）と、小計バック（新規）は別のルール。片方があってももう片方に効かない
        self.rule(target_type="nomination", calc_type="fixed", value=D(500))
        v = self.visit()
        line = services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})
        co = self.pay(v)
        self.assertEqual([(b.kind, b.amount) for b in BackEntry.objects.filter(checkout=co)], [("nomination", D(500))])
        self.assertEqual(line.back_entries.count(), 1)


class ReferenceExampleTests(Base):
    def test_reference_example_6_4(self):
        """
        参考設計 6.4 の検算例:
          22:00〜02:15・時給 1,400 → 4.25 時間・5,950 ／ 呼び小計 81,500 × 5% → 4,075 ／
          バック 300 × 20 ＋ 500 × 1 → 6,500 ／ 総支給額 5,950 ＋ 4,075 ＋ 6,500 = 16,525
        """
        WageSetting.objects.filter(employee=self.cast1).delete()
        WageSetting.objects.create(employee=self.cast1, store=self.store, hourly_wage=D(1400), effective_from=self.today - timedelta(days=30))
        tz = store_tz(self.store)
        start = datetime.combine(self.today, datetime.min.time(), tzinfo=tz).replace(hour=22)
        sh = Shift.objects.create(employee=self.cast1, store=self.store, business_date=self.today, clock_in=start, clock_out=start + timedelta(hours=4, minutes=15))
        self.assertEqual(payroll.shift_minutes(sh), 255)
        self.assertEqual(payroll.shift_wage(sh, 255), (D(1400), D(5950)))
        cat_d, cat_s, cat_b = self.fx.categories["cast_drink"], self.fx.categories["shot"], self.fx.categories["bottle"]
        drink = Product.objects.create(store=self.store, category=cat_d, name="ドリンク1000", price=D(1000))
        shot = Product.objects.create(store=self.store, category=cat_s, name="ショット1500", price=D(1500))
        other = Product.objects.create(store=self.store, category=cat_b, name="その他", price=D(56500))
        self.rule(target_type="product", product=drink, calc_type="fixed", value=D(300))
        self.rule(target_type="product", product=shot, calc_type="fixed", value=D(500))
        self.rule(target_type="nomination_subtotal", value=D(5), include_discount=False)
        v = self.visit()                                                                                    # 2,500
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})   # 1,000
        t = [{"employee_id": self.cast1.id}]
        services.add_product_line(self.store, self.user, v.id, {"product_id": drink.id, "quantity": 20, "back_targets": t})   # 20,000
        services.add_product_line(self.store, self.user, v.id, {"product_id": shot.id, "quantity": 1, "back_targets": t})     # 1,500
        services.add_product_line(self.store, self.user, v.id, {"product_id": other.id, "quantity": 1})                        # 56,500
        co = self.pay(v)
        self.assertEqual(co.subtotal, D(81500))
        row = [r for r in self.cast_report()["rows"] if r["employee_id"] == self.cast1.id][0]
        self.assertEqual((row["minutes"], D(row["wage"])), (255, D(5950)))
        self.assertEqual((D(row["yobi_subtotal"]), D(row["back_yobi_subtotal"])), (D(81500), D(4075)))
        self.assertEqual((D(row["back_drink"]), D(row["back_bottle"]), D(row["qty_drink"])), (D(6000), D(500), D(20)))
        self.assertEqual(D(row["back_drink"]) + D(row["back_bottle"]), D(6500))
        self.assertEqual(D(row["back_total"]), D(10575))
        self.assertEqual(D(row["gross"]), D(16525))
        self.assertEqual(D(row["avg_hourly"]), D(1400))
        # 既存の支払集計（compute_statement）と同じ結果
        st = payroll.compute_statement(self.store, self.cast1, self.today, self.today)
        self.assertEqual((D(st["gross_amount"]), D(st["balance_amount"])), (D(row["gross"]), D(row["balance"])))

    def test_remaining_after_daily_pay(self):
        # 残り支給額 = 総支給額 − 日払い（例: 11,550 − 10,000 = 1,550）
        BackEntry.objects.create(store=self.store, employee=self.cast1, business_date=self.today, kind="adjustment", amount=D(11550), status="confirmed")
        EmployeePayment.objects.create(employee=self.cast1, store=self.store, kind="daily", amount=D(10000), paid_at=timezone.now(), business_date=self.today, work_date=self.today)
        row = [r for r in self.cast_report()["rows"] if r["employee_id"] == self.cast1.id][0]
        self.assertEqual((D(row["gross"]), D(row["paid"]), D(row["balance"]), D(row["bonus"])), (D(11550), D(10000), D(1550), D(11550)))


class ReportTests(Base):
    def seed(self):
        self.rule(target_type="nomination", calc_type="fixed", value=D(500))
        self.rule(target_type="category_kind", category_kind="cast_drink", value=D(50))
        yesterday = self.today - timedelta(days=1) if self.today.day > 1 else self.today
        for d, emp, hours in ((self.today, self.cast1, 4), (yesterday, self.cast1, 3), (self.today, self.staff1, 5)):
            start = timezone.now().replace(microsecond=0) - timedelta(hours=hours)
            Shift.objects.create(employee=emp, store=self.store, business_date=d, clock_in=start, clock_out=start + timedelta(hours=hours))
        v = self.visit()
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id})
        services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products["cast_drink"].id, "quantity": 2, "back_targets": [{"employee_id": self.cast1.id}]})
        self.pay(v)
        BackEntry.objects.create(store=self.store, employee=self.cast1, business_date=self.today, kind="adjustment", amount=D(-300), status="confirmed", reason="遅刻")
        EmployeePayment.objects.create(employee=self.cast1, store=self.store, kind="daily", amount=D(5000), withholding_amount=D(100), deduction_amount=D(200),
                                       paid_at=timezone.now(), business_date=self.today, work_date=self.today)

    def test_month_total_equals_sum_of_days(self):
        self.seed()
        row = [r for r in self.cast_report()["rows"] if r["employee_id"] == self.cast1.id][0]
        p = self.person(self.cast1)
        self.assertEqual(p.status_code, 200, p.content)
        p = p.json()
        keys = ["wage", "back_total", "gross", "paid", "withheld", "deducted", "balance", "back_nomination", "back_drink", "minus", "yobi_subtotal", "order_subtotal"]
        for k in keys:
            self.assertEqual(D(p["totals"][k]), sum(D(d[k]) for d in p["days"]), k)   # 月の合計 = 日別の合計
            self.assertEqual(D(p["totals"][k]), D(row[k]), k)                           # 集計表の行 = 明細の合計
        self.assertEqual(p["totals"]["minutes"], sum(d["minutes"] for d in p["days"]))
        self.assertEqual(D(p["totals"]["days"]), D(len([d for d in p["days"] if d["shifts"]])))
        self.assertEqual((D(row["back_nomination"]), D(row["back_drink"]), D(row["minus"])), (D(500), D(1500), D(300)))
        self.assertEqual((D(row["sales_drink"]), D(row["qty_drink"]), D(row["nomination_count"]), D(row["nomination_groups"])), (D(3000), D(2), D(1), D(1)))
        self.assertEqual(D(row["balance"]), D(row["gross"]) - D(5000) - D(100) - D(200))
        # 既存の支払集計と一致
        st = payroll.compute_statement(self.store, self.cast1, *self._range())
        self.assertEqual((D(st["gross_amount"]), D(st["balance_amount"])), (D(row["gross"]), D(row["balance"])))

    def _range(self):
        from apps.analytics.reports import month_range

        return month_range(self.today.year, self.today.month)

    def test_totals_row_and_staff_report_split_by_role(self):
        self.seed()
        cast = self.cast_report()
        self.assertEqual({r["employee_name"] for r in cast["rows"]}, {"cast1", "cast2"})  # キャストだけ（勤怠が無い人も配属中なら出る）
        for k in ("gross", "wage", "back_total", "paid", "balance"):
            self.assertEqual(D(cast["totals"][k]), sum(D(r[k]) for r in cast["rows"]), k)
        staff = self.owner.get("/api/reports/staff", {"month": self.month}).json()
        self.assertEqual([r["employee_name"] for r in staff["rows"]], ["staff1"])
        self.assertEqual(D(staff["rows"][0]["wage"]), D(6000))

    def test_permissions_all_vs_self(self):
        self.seed()
        manager = client_for(self.fx.users["manager"], self.store)   # payroll.view_all なし
        cast = client_for(self.fx.users["cast"], self.store)         # 自分（cast1）の分だけ
        no_reauth = client_for(self.fx.users["owner"], self.store, reauth=False)
        for path in ("/api/reports/cast", "/api/reports/staff", "/api/reports/people-cast.csv"):
            self.assertEqual(manager.get(path).status_code, 403, path)
            self.assertEqual(cast.get(path).status_code, 403, path)
            self.assertEqual(no_reauth.get(path).status_code, 403, path)   # 全員分は再認証が必要
        self.assertEqual(self.person(self.cast1, cast).status_code, 200)   # 本人は自分の分を見られる
        self.assertEqual(self.person(self.cast2, cast).status_code, 403)   # 他人の分は見られない
        self.assertEqual(self.person(self.staff1, cast).status_code, 403)
        self.assertEqual(self.person(self.cast1, manager).status_code, 403)
        self.assertEqual(self.person(self.cast1, no_reauth).status_code, 403)
        self.assertEqual(self.person(self.cast1).status_code, 200)

    def test_other_company_employee_and_other_store_data(self):
        self.seed()
        other = standard_fixture("s2", company_code="c2")
        self.assertEqual(self.person(other.employees["cast1"]).status_code, 404)
        # 別店舗（別会社）の勤怠・バックは混ざらない
        Shift.objects.create(employee=other.employees["cast1"], store=other.store, business_date=self.today, clock_in=timezone.now() - timedelta(hours=2), clock_out=timezone.now())
        names = {r["employee_name"] for r in self.cast_report()["rows"]}
        self.assertEqual(names, {"cast1", "cast2"})

    def test_csv_export_and_withholding_column(self):
        self.seed()
        r = self.owner.get("/api/reports/people-cast.csv", {"month": self.month})
        self.assertEqual(r.status_code, 200)
        text = r.content.decode("utf-8-sig")
        header = text.splitlines()[0]
        self.assertIn("総支給額", header)
        self.assertNotIn("源泉所得税", header)   # 源泉徴収がオフの店舗では列を出さない
        self.assertTrue(text.splitlines()[1].startswith("合計,"))
        self.fx.settings.withholding_enabled = True
        self.fx.settings.save()
        header2 = self.owner.get("/api/reports/people-cast.csv", {"month": self.month}).content.decode("utf-8-sig").splitlines()[0]
        self.assertIn("源泉所得税", header2)
        self.assertTrue(self.cast_report()["settings"]["withholding_enabled"])
