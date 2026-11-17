"""
日払い＋締め払い（契約の支払方法 mixed と 1日の日払いの額）。
源泉所得税は差し引かない店舗（既定）で、日払いは固定額だけレジから渡し、残りは支払集計の残額（締めで渡す額）になる。
"""
from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from apps.accounting.models import CastContract
from apps.accounting.tests.helpers import contract, key, pay, payout, prev_month_first, work
from apps.cash.models import CashMovement
from apps.cash.services import current_open_day
from apps.core.testing import client_for, standard_fixture

D = Decimal


class DailySettlementTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.cast = self.fx.employees["cast1"]   # 時給 1,500
        # 日払いは勤務した営業日に渡す（支払集計は渡した営業日で数える）
        self.d = current_open_day(self.fx.store).date

    def mixed(self, amount=10000, **kw):
        c = contract(self.fx, self.cast, period="monthly", **kw)
        CastContract.objects.filter(id=c.id).update(payment_method="mixed", daily_pay_amount=D(amount))
        return c

    def theoretical(self):
        return D(self.manager.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])

    def statement(self):
        rows = self.owner.get("/api/staff/statements", {"from": self.d.isoformat(), "to": self.d.isoformat(), "employee": self.cast.id}).json()
        return rows[0]

    def test_fixed_amount_is_paid_and_rest_goes_to_settlement(self):
        self.mixed(10000)
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)   # 12,000 + 10,000 = 22,000
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((row["pay_plan"], row["status"]), ("daily_settlement", "ready"))
        self.assertEqual((D(row["remaining_amount"]), D(row["daily_due_amount"]), D(row["carry_amount"]), D(row["daily_fixed_amount"])),
                         (D(22000), D(10000), D(12000), D(10000)))
        before = self.theoretical()
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((D(r.json()["amount"]), D(r.json()["carry_amount"])), (D(10000), D(12000)))
        # レジから 1 万円だけ出る（日払いの出金として理論現金から引かれる）
        self.assertEqual(self.theoretical(), before - D(10000))
        self.assertEqual(CashMovement.objects.get(id=r.json()["cash_movement_id"]).amount, D(10000))
        after = payout(self.manager, self.cast, self.d)
        self.assertEqual((after["status"], D(after["paid_amount"]), D(after["daily_due_amount"]), D(after["carry_amount"])),
                         ("paid", D(10000), D(0), D(12000)))
        # 日払い分を渡したあとは、もう「渡した」では出さない
        again = pay(self.manager, self.cast, self.d, expected_amount=after["remaining_amount"])
        self.assertEqual((again.status_code, again.json()["error"]["code"]), (409, "nothing_to_pay"))
        self.assertIn("締めで渡します", again.json()["error"]["message"])
        # 支払集計: 残額 = 締めで渡す額
        s = self.statement()
        self.assertEqual((D(s["gross_amount"]), D(s["paid_amount"]), D(s["balance_amount"])), (D(22000), D(10000), D(12000)))
        self.assertEqual((s["payment_method"], s["payment_method_label"]), ("mixed", "日払い＋締め払い（1日 ¥10,000）"))
        # 締め払いを記録（レジ以外の現金）→ 残額 0。レジの理論現金は変わらない
        before = self.theoretical()
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast.id, "amount": 12000, "method": "other", "kind": "settlement",
                                                    "business_date": self.d.isoformat()}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(D(self.statement()["balance_amount"]), D(0))
        self.assertEqual(self.theoretical(), before)

    def test_settlement_recorded_later_counts_in_the_settled_period(self):
        """締め払いは締め日のあとに渡すことが多い。画面は期間の最終日の分として記録する（business_date）→ その期間の残額が 0 になる"""
        last = prev_month_first()
        self.mixed(10000)
        work(self.fx.store, self.cast, last, hours=8, back=10000)   # 先月 1 日に 22,000（日払いはしていない想定）
        rows = self.owner.get("/api/staff/statements", {"from": last.isoformat(), "to": last.isoformat(), "employee": self.cast.id}).json()
        self.assertEqual(D(rows[0]["balance_amount"]), D(22000))
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast.id, "amount": 22000, "method": "bank", "kind": "settlement",
                                                    "business_date": last.isoformat()}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        rows = self.owner.get("/api/staff/statements", {"from": last.isoformat(), "to": last.isoformat(), "employee": self.cast.id}).json()
        self.assertEqual(D(rows[0]["balance_amount"]), D(0))
        # 今月の集計には入らない
        rows = self.owner.get("/api/staff/statements", {"from": self.d.isoformat(), "to": self.d.isoformat(), "employee": self.cast.id}).json()
        self.assertEqual(D(rows[0]["paid_amount"]), D(0))

    def test_day_below_fixed_amount_pays_everything(self):
        self.mixed(10000)
        work(self.fx.store, self.cast, self.d, hours=4)   # 6,000
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((D(row["daily_due_amount"]), D(row["carry_amount"])), (D(6000), D(0)))
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(D(r.json()["amount"]), D(6000))
        after = payout(self.manager, self.cast, self.d)
        self.assertEqual((after["status"], D(after["carry_amount"])), ("paid", D(0)))
        self.assertEqual(D(self.statement()["balance_amount"]), D(0))

    def test_partial_and_custom_amounts(self):
        self.mixed(10000)
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)   # 22,000
        # 4,000 だけ先に渡す → 一部渡し済み。今日あと 6,000 渡せる
        r = pay(self.manager, self.cast, self.d, amount="4000", expected_amount="22000")
        self.assertEqual(r.status_code, 201, r.content)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((row["status"], D(row["daily_due_amount"]), D(row["daily_fixed_remaining"]), D(row["carry_amount"])),
                         ("partial", D(6000), D(6000), D(12000)))
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(D(r.json()["amount"]), D(6000))
        # 本人の希望で固定額より多く渡すこともできる（「金額を変えて渡す」。上限は未渡しの全額）
        r = pay(self.manager, self.cast, self.d, amount="2000", expected_amount="12000")
        self.assertEqual(r.status_code, 201, r.content)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((row["status"], D(row["paid_amount"]), D(row["carry_amount"])), ("paid", D(12000), D(10000)))
        self.assertEqual(pay(self.manager, self.cast, self.d, amount="10001", expected_amount="10000").status_code, 400)

    def test_contract_deductions_are_taken_on_the_daily_payment(self):
        self.mixed(10000, deductions=[{"code": "welfare", "label": "厚生費", "calc": "percent", "value": "5"}])
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)   # 22,000、厚生費 5% = 1,100
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((D(row["remaining_amount"]), D(row["daily_due_amount"]), D(row["carry_amount"])), (D(20900), D(10000), D(10900)))
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual((D(r.json()["amount"]), D(r.json()["other_deduction_amount"])), (D(10000), D(1100)))
        s = self.statement()
        self.assertEqual((D(s["paid_amount"]), D(s["deducted_amount"]), D(s["balance_amount"])), (D(10000), D(1100), D(10900)))
        self.assertEqual(D(payout(self.manager, self.cast, self.d)["carry_amount"]), D(10900))

    def test_other_payment_methods_are_unchanged(self):
        contract(self.fx, self.cast, period="daily")   # 既定の支払方法（日払い）
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        row = payout(self.manager, self.cast, self.d)
        self.assertEqual((row["pay_plan"], D(row["daily_due_amount"]), D(row["carry_amount"]), row["daily_fixed_amount"]),
                         ("daily", D(22000), D(0), None))
        r = pay(self.manager, self.cast, self.d, expected_amount=row["remaining_amount"])
        self.assertEqual(D(r.json()["amount"]), D(22000))
        self.assertEqual(self.statement()["payment_method_label"], "日払い（レジ現金）")
        # 契約のない人
        other = self.fx.employees["cast2"]
        work(self.fx.store, other, self.d, hours=2)
        row = payout(self.manager, other, self.d)
        self.assertEqual((row["pay_plan"], row["payment_method_label"], D(row["daily_due_amount"])), ("daily", "契約なし", D(3000)))
        # 一覧の合計: お渡し待ちは今日渡す額、締めに回す額は日払い＋締め払いの人だけ
        totals = self.manager.get(f"/api/staff/daily-payouts?date={self.d.isoformat()}").json()["totals"]
        self.assertEqual((D(totals["remaining"]), D(totals["carry"])), (D(3000), D(0)))


class ContractDailyPayAmountTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.cast = self.fx.employees["cast1"]

    def test_amount_is_required_for_mixed_and_cleared_otherwise(self):
        body = {"employee": self.cast.id, "contract_type": "outsourcing", "start_date": "2026-11-01", "calc_period": "monthly", "payment_method": "mixed"}
        r = self.owner.post("/api/accounting/contracts", body, format="json", **key())
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("daily_pay_amount", str(r.json()))
        self.assertEqual(self.owner.post("/api/accounting/contracts", {**body, "daily_pay_amount": "1000.5"}, format="json", **key()).status_code, 400)
        r = self.owner.post("/api/accounting/contracts", {**body, "daily_pay_amount": "10000"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        self.assertEqual(D(r.json()["daily_pay_amount"]), D(10000))
        # 日払い＋締め払い以外に変えると、日払いの額は消える
        r = self.owner.patch(f"/api/accounting/contracts/{cid}", {"payment_method": "daily_cash"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIsNone(r.json()["daily_pay_amount"])
