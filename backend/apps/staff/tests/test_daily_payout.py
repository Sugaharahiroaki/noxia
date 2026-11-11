"""日払い: 退勤 → お渡し額（時給×勤務時間＋確定バック）→「渡した」でレジ出金と支払記録 → レジ締めに反映。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.cash.models import CashMovement
from apps.core.business_date import business_date_now
from apps.core.testing import add_employee, client_for, make_store, standard_fixture
from apps.staff.models import BackEntry, EmployeePayment, Shift, ShiftBreak


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class PayoutBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.cast_client = client_for(self.fx.users["cast"], self.store)
        self.cast1 = self.fx.employees["cast1"]  # 時給 1,500
        r = self.manager.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.bd = business_date_now(self.store)

    def work(self, emp, minutes=330, break_minutes=30, clocked_out=True):
        end = timezone.now() - timedelta(minutes=1)
        start = end - timedelta(minutes=minutes)
        s = Shift.objects.create(employee=emp, store=self.store, business_date=self.bd, clock_in=start, clock_out=end if clocked_out else None)
        if break_minutes:
            ShiftBreak.objects.create(shift=s, started_at=start + timedelta(minutes=60), ended_at=start + timedelta(minutes=60 + break_minutes))
        return s

    def back(self, emp, amount, status="confirmed"):
        return BackEntry.objects.create(store=self.store, employee=emp, kind="product", business_date=self.bd, amount=Decimal(amount), status=status)

    def row(self, client=None, emp=None):
        data = (client or self.manager).get("/api/staff/daily-payouts").json()
        emp = emp or self.cast1
        return [r for r in data["rows"] if r["employee_id"] == emp.id][0], data

    def pay(self, client=None, **body):
        body.setdefault("employee_id", self.cast1.id)
        return (client or self.manager).post("/api/staff/daily-payouts/pay", body, format="json", **key())

    def theoretical(self):
        return Decimal(self.manager.get("/api/cash/current").json()["day"]["summary"]["theoretical_cash"])


class PayoutCalculationTests(PayoutBase):
    def test_amount_is_wage_plus_confirmed_backs(self):
        self.work(self.cast1)  # 5時間30分 − 休憩30分 = 5時間 → 7,500
        self.back(self.cast1, 4100)
        self.back(self.cast1, 999, status="pending")  # 未確定は含めない
        r, data = self.row()
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["worked_minutes"], 300)
        self.assertEqual(Decimal(r["wage_amount"]), Decimal(7500))
        self.assertEqual(Decimal(r["back_confirmed"]), Decimal(4100))
        self.assertEqual(Decimal(r["back_pending"]), Decimal(999))
        self.assertEqual(Decimal(r["gross_amount"]), Decimal(11600))
        self.assertEqual(Decimal(r["remaining_amount"]), Decimal(11600))
        self.assertEqual(Decimal(data["totals"]["remaining"]), Decimal(11600))

    def test_working_employee_cannot_be_paid_yet(self):
        self.work(self.cast1, clocked_out=False)
        r, _ = self.row()
        self.assertEqual(r["status"], "working")
        res = self.pay(expected_amount=r["remaining_amount"])
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "still_working")
        self.assertFalse(CashMovement.objects.filter(kind="daily_pay").exists())

    def test_missing_wage_blocks_payment(self):
        nowage = add_employee(self.fx, "nowage", wage=None)
        self.work(nowage)
        r, _ = self.row(emp=nowage)
        self.assertEqual(r["status"], "missing_wage")
        res = self.pay(employee_id=nowage.id)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "missing_wage")

    def test_hourly_rate_only_visible_with_wage_permission(self):
        self.work(self.cast1)
        r_manager, _ = self.row(self.manager)
        r_owner, _ = self.row(self.owner)
        self.assertIsNone(r_manager["shifts"][0]["hourly_wage"])
        self.assertEqual(Decimal(r_owner["shifts"][0]["hourly_wage"]), Decimal(1500))
        self.assertEqual(r_manager["wage_amount"], r_owner["wage_amount"])  # 金額は同じ


class PayoutPaymentTests(PayoutBase):
    def test_pay_creates_one_cash_out_and_one_payment(self):
        self.work(self.cast1)
        self.back(self.cast1, 4100)
        before = self.theoretical()
        res = self.pay(expected_amount="11600")
        self.assertEqual(res.status_code, 201, res.content)
        mv = CashMovement.objects.get(id=res.json()["cash_movement_id"])
        self.assertEqual((mv.kind, mv.amount, mv.employee_id), ("daily_pay", Decimal(11600), self.cast1.id))
        ep = EmployeePayment.objects.get(cash_movement=mv)
        self.assertEqual((ep.amount, ep.work_date, ep.method), (Decimal(11600), self.bd, "cash"))
        self.assertEqual(self.theoretical(), before - Decimal(11600))
        r, _ = self.row()
        self.assertEqual((r["status"], Decimal(r["remaining_amount"])), ("paid", Decimal(0)))
        # 支払集計（期間）でも1回だけ数える
        stmt = self.owner.get(f"/api/staff/statements?from={self.bd}&to={self.bd}&employee={self.cast1.id}").json()[0]
        self.assertEqual(Decimal(stmt["paid_amount"]), Decimal(11600))
        self.assertEqual(Decimal(stmt["balance_amount"]), Decimal(0))

    def test_double_tap_and_second_payment_are_rejected(self):
        self.work(self.cast1)
        k = key()
        body = {"employee_id": self.cast1.id, "expected_amount": "7500"}
        r1 = self.manager.post("/api/staff/daily-payouts/pay", body, format="json", **k)
        r2 = self.manager.post("/api/staff/daily-payouts/pay", body, format="json", **k)  # 連打・再送
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        r3 = self.pay(expected_amount="7500")  # 別の操作として再度押した
        self.assertEqual(r3.status_code, 409)
        self.assertIn(r3.json()["error"]["code"], ("nothing_to_pay", "amount_changed"))
        self.assertEqual(CashMovement.objects.filter(kind="daily_pay").count(), 1)

    def test_stale_screen_amount_is_rejected(self):
        self.work(self.cast1)
        self.back(self.cast1, 1000)  # 画面を開いた後にバックが確定した
        res = self.pay(expected_amount="7500")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["error"]["code"], "amount_changed")
        self.assertEqual(res.json()["error"]["details"]["remaining_amount"], "8500.00")
        self.assertFalse(EmployeePayment.objects.exists())

    def test_partial_then_rest(self):
        self.work(self.cast1)
        self.assertEqual(self.pay(amount="5000").status_code, 201)
        r, _ = self.row()
        self.assertEqual((r["status"], Decimal(r["remaining_amount"])), ("partial", Decimal(2500)))
        self.assertEqual(self.pay(amount="3000").status_code, 400)  # 未渡しより多くは渡せない
        self.assertEqual(self.pay(expected_amount="2500").status_code, 201)
        r, _ = self.row()
        self.assertEqual(r["status"], "paid")

    def test_undo_by_voiding_cash_movement_restores_remaining(self):
        self.work(self.cast1)
        res = self.pay()
        mv_id = res.json()["cash_movement_id"]
        before = self.theoretical()
        v = self.manager.post(f"/api/cash/movements/{mv_id}/void", {"reason": "入力ミス（操作直後の取り消し）"}, format="json")
        self.assertEqual(v.status_code, 200, v.content)
        self.assertTrue(EmployeePayment.objects.get(cash_movement_id=mv_id).is_voided)
        self.assertEqual(self.theoretical(), before + Decimal(7500))
        r, _ = self.row()
        self.assertEqual((r["status"], Decimal(r["remaining_amount"])), ("ready", Decimal(7500)))


class PayoutPermissionTests(PayoutBase):
    def test_staff_and_cast_cannot_view_or_pay(self):
        self.work(self.cast1)
        for c in (self.staff, self.cast_client):
            self.assertEqual(c.get("/api/staff/daily-payouts").status_code, 403)
            self.assertEqual(self.pay(client=c).status_code, 403)
        self.assertFalse(CashMovement.objects.exists())

    def test_other_company_employee_cannot_be_paid(self):
        other = make_store("s2", company_code="c2")
        foreign = add_employee(other, "foreign")
        res = self.pay(employee_id=foreign.id)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(CashMovement.objects.exists())

    def test_cast_sees_own_today_payout(self):
        self.work(self.cast1)
        self.back(self.cast1, 500)
        me = self.cast_client.get("/api/staff/me").json()
        self.assertEqual(Decimal(me["today_payout"]["gross_amount"]), Decimal(8000))
        self.assertEqual(me["today_payout"]["employee_id"], self.cast1.id)


class PayoutClosingTests(PayoutBase):
    def test_closing_warns_unpaid_and_net_estimate_reflects_payouts(self):
        self.work(self.cast1)
        cur = self.manager.get("/api/cash/current").json()["day"]
        self.assertIn("unpaid_payouts", [w["code"] for w in cur["precheck"]])
        self.pay()
        self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "1200", "reason": "氷"}, format="json", **key())
        cur = self.manager.get("/api/cash/current").json()["day"]
        self.assertNotIn("unpaid_payouts", [w["code"] for w in cur["precheck"]])
        s = cur["summary"]
        self.assertEqual(Decimal(s["cash_out_daily_pay"]), Decimal(7500))
        self.assertEqual(Decimal(s["net_estimate"]), Decimal(s["sales_total"]) - Decimal(7500) - Decimal(1200))
        # 未渡しの日払いは締めを止めない（警告のみ）
        r = self.manager.post("/api/cash/close", {"counted_cash": str(Decimal(s["theoretical_cash"])), "next_day_float": "20000"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
