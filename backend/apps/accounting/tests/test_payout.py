"""日払い（お渡し）と源泉徴収の統合テスト。"""
import calendar
from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting import services
from apps.accounting.models import DeductionUsage, StoreTaxProfile, WithholdingRecord
from apps.cash.models import CashMovement
from apps.core.business_date import TOKYO
from apps.core.testing import add_user, client_for, make_store, standard_fixture
from apps.staff.models import EmployeePayment, EmployeeStoreAssignment

from .helpers import contract, key, make_taxpayer, pay, payout, prev_month_first, work


class PayoutWithholdingTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.cast = self.fx.employees["cast1"]
        self.d = prev_month_first()

    def theoretical(self):
        return Decimal(self.manager.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])

    def test_daily_fee_withholding_and_cash(self):
        """1. 日額報酬 / 14. 現金残高との照合: 手渡し額だけがレジから出て、源泉所得税は預り金として残る"""
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)  # 1,500円×8時間 + 10,000 = 22,000
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(Decimal(row["gross_amount"]), Decimal("22000"))
        self.assertEqual(row["tax"]["status"], "calculated")
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), Decimal("1735"))
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("20265"))
        before = self.theoretical()
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(r.status_code, 201, r.content)
        body = r.json()
        self.assertEqual(Decimal(body["amount"]), Decimal("20265"))
        self.assertEqual(Decimal(body["withholding_amount"]), Decimal("1735"))
        self.assertEqual(self.theoretical(), before - Decimal("20265"))
        ep = EmployeePayment.objects.get(id=body["payment_id"])
        self.assertEqual((ep.amount, ep.withholding_amount), (Decimal("20265"), Decimal("1735")))
        rec = WithholdingRecord.objects.get(payment=ep)
        self.assertEqual(rec.status, "calculated")
        self.assertEqual((rec.period_start, rec.period_end, rec.period_days), (self.d, self.d, 1))
        self.assertEqual(rec.deduction_applied, Decimal("5000"))
        self.assertIn("防衛" if rec.paid_on.year >= 2027 else "復興", rec.rule_version)
        self.assertEqual(rec.calc_basis["engine_version"], "hostess-withholding/1")
        self.assertEqual({i["code"] for i in rec.items}, {"base_pay", "back_adjustment"})
        after = payout(self.manager, self.cast, self.d)
        self.assertEqual(after["status"], "paid")
        self.assertEqual(Decimal(after["withheld_amount"]), Decimal("1735"))
        self.assertEqual(Decimal(after["remaining_gross"]), Decimal("0"))

    def test_stale_screen_amount_rejected(self):
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        r = pay(self.manager, self.cast, self.d, expected_amount="22000")  # 源泉税を引く前の古い額
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "amount_changed")

    def test_monthly_contract_daily_advances(self):
        """2. 月単位契約の日払い: 控除は月の暦日数×5,000円を前払いの累計で使い、超えた日から源泉徴収する"""
        contract(self.fx, self.cast, period="monthly")
        days = calendar.monthrange(self.d.year, self.d.month)[1]
        budget = Decimal(5000 * days)
        total = Decimal(0)
        n = 0
        while total + Decimal(22000) <= budget:
            work(self.fx.store, self.cast, self.d + timedelta(days=n), hours=8, back=10000)
            r = pay(self.manager, self.cast, self.d + timedelta(days=n))
            self.assertEqual(Decimal(r.json()["withholding_amount"]), Decimal(0))
            total += Decimal(22000)
            n += 1
        work(self.fx.store, self.cast, self.d + timedelta(days=n), hours=8, back=10000)
        r = pay(self.manager, self.cast, self.d + timedelta(days=n))
        rest = budget - total
        expected = ((Decimal(22000) - rest) * Decimal("0.1021")).to_integral_value(rounding="ROUND_FLOOR")
        self.assertEqual(Decimal(r.json()["withholding_amount"]), expected)
        used = sum(u.amount for u in DeductionUsage.objects.filter(taxpayer=self.tp, employee=self.cast))
        self.assertEqual(used, budget)  # 月の控除額をちょうど使い切る（それ以上は使わない）

    def test_additional_payments_same_day(self):
        """3. 複数回の追加支払い: 一部だけ渡し、あとで残りを渡しても控除は1日分だけ・精算は報酬総額と一致"""
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        r1 = pay(self.manager, self.cast, self.d, amount="10000")
        self.assertEqual(r1.status_code, 201, r1.content)
        self.assertEqual(Decimal(r1.json()["amount"]), Decimal("10000"))
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(Decimal(row["tax"]["deduction_available"]), Decimal("0"))
        r2 = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(r2.status_code, 201, r2.content)
        recs = WithholdingRecord.objects.filter(employee=self.cast).order_by("id")
        self.assertEqual(sum(r.gross_amount for r in recs), Decimal("22000"))
        self.assertEqual(sum(r.deduction_applied for r in recs), Decimal("5000"))
        self.assertEqual(recs[1].deduction_applied, Decimal("0"))
        self.assertEqual(sum(u.amount for u in DeductionUsage.objects.filter(employee=self.cast, date=self.d)), Decimal("5000"))
        final = payout(self.manager, self.cast, self.d)
        self.assertEqual(final["status"], "paid")
        total_w = sum(r.withholding_amount for r in recs)
        self.assertLessEqual(abs(total_w - Decimal("1735")), Decimal("1"))  # 1回で払った場合との差は端数の1円以内

    def test_same_taxpayer_two_stores_share_deduction(self):
        """4. 同一期間の重複控除防止（同じ事業者の別店舗で同じキャストに払っても控除は共有）"""
        contract(self.fx, self.cast, period="monthly")
        fx2 = make_store("s2", company=self.fx.company, withholding_enabled=True)
        StoreTaxProfile.objects.create(store=fx2.store, taxpayer=self.tp)
        EmployeeStoreAssignment.objects.create(employee=self.cast, store=fx2.store, role="cast")
        from apps.staff.models import WageSetting

        WageSetting.objects.create(employee=self.cast, store=fx2.store, hourly_wage=Decimal(1500), effective_from=self.d - timedelta(days=30))
        m2 = add_user(fx2, "manager", store_role="manager", username="manager_s2")
        c2 = client_for(m2, fx2.store, reauth=False)
        days = calendar.monthrange(self.d.year, self.d.month)[1]
        work(self.fx.store, self.cast, self.d, hours=10, back=5000 * days - 15000)  # 店舗1で控除額ちょうど
        self.assertEqual(Decimal(pay(self.manager, self.cast, self.d).json()["withholding_amount"]), Decimal(0))
        work(fx2.store, self.cast, self.d + timedelta(days=1), hours=8, back=0)  # 店舗2で 12,000
        r = pay(c2, self.cast, self.d + timedelta(days=1))
        self.assertEqual(Decimal(r.json()["withholding_amount"]), Decimal("1225"))  # 12,000 × 10.21% = 1,225.2（控除なし）

    def test_zero_tax_payment_recorded(self):
        """5. 源泉税0円でも明細を残し、人数・件数に数える"""
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=3)  # 4,500円
        r = pay(self.manager, self.cast, self.d)
        self.assertEqual(Decimal(r.json()["withholding_amount"]), Decimal(0))
        rec = WithholdingRecord.objects.get(employee=self.cast)
        self.assertEqual((rec.status, rec.withholding_amount, rec.deduction_applied), ("calculated", Decimal(0), Decimal("4500")))
        v = services.month_view(self.tp, "hostess_fee", rec.paid_on)
        self.assertEqual(v["totals"]["people"], 1)
        self.assertEqual(v["totals"]["payment_count"], 1)

    def test_salary_vs_outsourcing(self):
        """7. 給与と業務委託の区分: 給与はホステス報酬の計算式を使わない。未確認は計算せず要確認"""
        contract(self.fx, self.cast, category="salary", contract_type="employment")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(row["tax"]["status"], "external")
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), Decimal(0))
        r = pay(self.manager, self.cast, self.d)
        self.assertEqual(WithholdingRecord.objects.get(id=r.json()["withholding_record_id"]).status, "external")
        # 業務委託と書いてあっても、経営者の確認前は計算しない
        cast2 = self.fx.employees["cast2"]
        contract(self.fx, cast2, category="hostess_fee", confirmed=False)
        work(self.fx.store, cast2, self.d, hours=8, back=10000)
        row2 = payout(self.manager, cast2, self.d)
        self.assertEqual(row2["tax"]["status"], "needs_review")
        self.assertEqual(Decimal(row2["remaining_amount"]), Decimal("22000"))
        self.assertTrue(any("確認前" in m for m in row2["tax"]["reasons"]))
        r2 = pay(self.manager, cast2, self.d)
        self.assertEqual(WithholdingRecord.objects.get(id=r2.json()["withholding_record_id"]).status, "needs_review")
        # 契約がない人も要確認
        staff1 = self.fx.employees["staff1"]
        work(self.fx.store, staff1, self.d, hours=4)
        self.assertEqual(payout(self.manager, staff1, self.d)["tax"]["status"], "needs_review")

    def test_other_deductions_are_separate(self):
        """厚生費などは源泉所得税とは別項目。源泉の対象額を減らさない"""
        contract(self.fx, self.cast, period="daily", deductions=[{"code": "welfare", "label": "厚生費", "calc": "percent", "value": "10"}])
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), Decimal("1735"))
        self.assertEqual(Decimal(row["tax"]["other_deduction_amount"]), Decimal("2200"))
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("18065"))
        r = pay(self.manager, self.cast, self.d)
        ep = EmployeePayment.objects.get(id=r.json()["payment_id"])
        self.assertEqual((ep.amount, ep.withholding_amount, ep.deduction_amount), (Decimal("18065"), Decimal("1735"), Decimal("2200")))

    def test_month_boundary_uses_payment_date(self):
        """10. 月末をまたぐ支払い: 同じ営業日の勤務分でも、実際の支払日の月で集計する"""
        contract(self.fx, self.cast, period="daily")
        year = timezone.localdate().year - 1
        oct31, nov1 = datetime(year, 10, 31, 23, 30, tzinfo=TOKYO), datetime(year, 11, 1, 1, 0, tzinfo=TOKYO)
        for paid_at in (oct31, nov1):
            st = services.settle_simple(self.fx.store, self.cast, amount=Decimal(20000), on_date=oct31.date(), paid_on=paid_at.date(), mode="gross",
                                        period=(paid_at.date(), paid_at.date()))
            ep = EmployeePayment.objects.create(employee=self.cast, store=self.fx.store, kind="daily", amount=st.net, withholding_amount=st.withholding,
                                                method="bank", paid_at=paid_at, business_date=oct31.date())
            services.record_withholding(ep, st, work_date=oct31.date(), user=None)
        oct_v = services.month_view(self.tp, "hostess_fee", oct31.date())
        nov_v = services.month_view(self.tp, "hostess_fee", nov1.date())
        self.assertEqual(oct_v["totals"]["payment_count"], 1)
        self.assertEqual(nov_v["totals"]["payment_count"], 1)
        self.assertEqual(oct_v["due_date"][:7], f"{year}-11")
        self.assertEqual(nov_v["due_date"][:7], f"{year}-12")

    def test_void_restores_deduction(self):
        """11. 支払いの取消: 明細は取消として残り、使った控除額は戻る"""
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        body = pay(self.manager, self.cast, self.d).json()
        r = self.manager.post(f"/api/cash/movements/{body['cash_movement_id']}/void", {"reason": "渡していなかった"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        rec = WithholdingRecord.objects.get(id=body["withholding_record_id"])
        self.assertTrue(rec.is_voided)
        self.assertEqual(rec.status, "voided")
        self.assertFalse(DeductionUsage.objects.filter(record=rec).exists())
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), Decimal("1735"))  # もう一度控除を使って計算できる
        self.assertEqual(Decimal(pay(self.manager, self.cast, self.d).json()["withholding_amount"]), Decimal("1735"))

    def test_store_without_taxpayer_needs_review(self):
        StoreTaxProfile.objects.all().delete()
        contract(self.fx, self.cast, period="daily")
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual(row["tax"]["status"], "needs_review")
        self.assertTrue(any("事業者" in m for m in row["tax"]["reasons"]))

    def test_bank_settlement_gross_mode(self):
        """振込の締め支払い（報酬額で登録）: ホステス報酬は源泉所得税を差し引いた額を払う"""
        contract(self.fx, self.cast, period="semimonthly")
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast.id, "amount": "300000", "amount_basis": "gross", "method": "bank",
                                                     "period_start": "2026-10-16", "period_end": "2026-10-31"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["withholding_amount"]), Decimal("22462"))
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("277538"))
        # 渡した額だけの登録（既定）は源泉を計算せず要確認として残す
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast.id, "amount": "10000", "method": "bank"}, format="json", **key())
        self.assertEqual(WithholdingRecord.objects.get(payment_id=r.json()["id"]).status, "needs_review")


class EmployeeCashMovementTests(TestCase):
    def test_manual_cash_daily_pay_marked_for_review(self):
        fx = standard_fixture()
        make_taxpayer(fx)
        m = client_for(fx.users["manager"], fx.store, reauth=False)
        cast = fx.employees["cast1"]
        contract(fx, cast, period="daily")
        r = m.post("/api/cash/movements", {"kind": "daily_pay", "amount": "5000", "reason": "前払い", "employee_id": cast.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        ep = EmployeePayment.objects.get(cash_movement_id=r.json()["id"])
        self.assertEqual(WithholdingRecord.objects.get(payment=ep).status, "needs_review")
        self.assertEqual(CashMovement.objects.get(id=r.json()["id"]).amount, Decimal("5000"))
