"""勤怠・支払集計（勤務時間、時給の適用日、日払いの二重控除防止、本人のみ閲覧、再認証）。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.cash.models import CashMovement
from apps.cash.services import day_summary, get_or_open_business_day
from apps.core.business_date import business_date_now
from apps.core.testing import client_for, standard_fixture
from apps.staff import payroll
from apps.staff.models import EmployeePayment, PayPeriod, PayStatement, Shift, ShiftBreak, WageSetting

TOKYO = ZoneInfo("Asia/Tokyo")


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class PayrollBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.cast1 = self.fx.employees["cast1"]  # 時給 1500
        self.cast2 = self.fx.employees["cast2"]
        self.today = business_date_now(self.store)
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.cast = client_for(self.fx.users["cast"], self.store)

    def shift(self, employee, hours=6, breaks=(), business_date=None):
        start = timezone.now().replace(microsecond=0) - timedelta(hours=hours)
        s = Shift.objects.create(employee=employee, store=self.store, business_date=business_date or self.today, clock_in=start, clock_out=start + timedelta(hours=hours))
        for b_start, b_min in breaks:
            ShiftBreak.objects.create(shift=s, started_at=start + timedelta(minutes=b_start), ended_at=start + timedelta(minutes=b_start + b_min))
        return s


class ShiftComputationTests(PayrollBase):
    def test_shift_minutes_with_breaks(self):
        s = self.shift(self.cast1, hours=6, breaks=[(60, 30), (200, 15)])
        self.assertEqual(payroll.shift_minutes(s), 360 - 45)

    def test_wage_amount_floor(self):
        s = self.shift(self.cast1, hours=6, breaks=[(60, 30)])  # 330分 × 1500/60 = 8250
        wage, amount = payroll.shift_wage(s, payroll.shift_minutes(s))
        self.assertEqual(wage, Decimal("1500"))
        self.assertEqual(amount, Decimal("8250"))
        s2 = self.shift(self.cast1, hours=1, breaks=[(0, 7)])  # 53分 × 25 = 1325
        self.assertEqual(payroll.shift_wage(s2, payroll.shift_minutes(s2))[1], Decimal("1325"))

    def test_wage_effective_date_uses_shift_business_date(self):
        past_day = self.today - timedelta(days=10)
        s_past = self.shift(self.cast1, hours=4, business_date=past_day)
        s_today = self.shift(self.cast1, hours=4)
        WageSetting.objects.create(employee=self.cast1, store=self.store, hourly_wage=Decimal(1800), effective_from=self.today)
        self.assertEqual(payroll.shift_wage(s_past, 240)[1], Decimal("6000"))
        self.assertEqual(payroll.shift_wage(s_today, 240)[1], Decimal("7200"))
        stmt = payroll.compute_statement(self.store, self.cast1, past_day, past_day)
        self.assertEqual(stmt["wage_amount"], "6000")
        self.assertEqual(stmt["worked_minutes"], 240)
        # 将来日の時給変更は今日の集計を変えない
        WageSetting.objects.create(employee=self.cast1, store=self.store, hourly_wage=Decimal(5000), effective_from=self.today + timedelta(days=1))
        self.assertEqual(payroll.compute_statement(self.store, self.cast1, self.today, self.today)["wage_amount"], "7200")

    def test_missing_wage_flagged(self):
        emp = self.fx.employees["cast2"]
        WageSetting.objects.filter(employee=emp).delete()
        self.shift(emp, hours=2)
        stmt = payroll.compute_statement(self.store, emp, self.today, self.today)
        self.assertTrue(stmt["missing_wage_setting"])
        self.assertEqual(stmt["wage_amount"], "0")
        self.assertIn("法定控除", stmt["notice"])

    def test_closed_period_snapshot_unaffected_by_later_wage_change(self):
        self.shift(self.cast1, hours=4)
        period = PayPeriod.objects.create(store=self.store, start_date=self.today - timedelta(days=14), end_date=self.today)
        payroll.close_pay_period(period, self.fx.users["owner"], [self.cast1])
        st = PayStatement.objects.get(pay_period=period, employee=self.cast1)
        self.assertEqual(st.wage_amount, Decimal("6000"))
        self.assertEqual(st.gross_amount, Decimal("6000"))
        self.assertEqual(st.balance_amount, Decimal("6000"))
        WageSetting.objects.create(employee=self.cast1, store=self.store, hourly_wage=Decimal(3000), effective_from=self.today - timedelta(days=100))
        self.assertEqual(payroll.compute_statement(self.store, self.cast1, self.today, self.today)["wage_amount"], "12000")  # 遡及設定は再計算に効く
        st.refresh_from_db()
        self.assertEqual(st.wage_amount, Decimal("6000"))  # 締め済みは固定
        period.refresh_from_db()
        self.assertEqual(period.status, "closed")


class DailyPayTests(PayrollBase):
    def test_daily_pay_from_register_counts_once(self):
        self.shift(self.cast1, hours=4)  # 6000
        self.manager.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        r = self.manager.post("/api/cash/movements", {"kind": "daily_pay", "amount": "5000", "reason": "日払い", "employee_id": self.cast1.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        mv = CashMovement.objects.get(id=r.json()["id"])
        payments = EmployeePayment.objects.filter(employee=self.cast1)
        self.assertEqual(payments.count(), 1)
        ep = payments.get()
        self.assertEqual(ep.cash_movement_id, mv.id)
        self.assertEqual(ep.amount, Decimal("5000"))
        self.assertEqual(ep.method, "cash")
        self.assertEqual(ep.business_date, mv.business_day.date)
        stmt = payroll.compute_statement(self.store, self.cast1, self.today, self.today)
        self.assertEqual(Decimal(stmt["paid_amount"]), Decimal("5000"))
        self.assertEqual(Decimal(stmt["balance_amount"]), Decimal("1000"))
        day = mv.business_day
        s = day_summary(day)
        self.assertEqual(Decimal(s["cash_out_daily_pay"]), Decimal("5000"))
        self.assertEqual(Decimal(s["theoretical_cash"]), Decimal("25000"))  # 30000 − 5000（一度だけ）
        # 日払いには従業員が必須
        r = self.manager.post("/api/cash/movements", {"kind": "daily_pay", "amount": "1000", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        # スタッフは日払い権限なし
        r = self.staff.post("/api/cash/movements", {"kind": "daily_pay", "amount": "1000", "reason": "x", "employee_id": self.cast1.id}, format="json", **key())
        self.assertEqual(r.status_code, 403)

    def test_void_movement_voids_payment(self):
        self.shift(self.cast1, hours=4)
        self.manager.post("/api/cash/open", {"opening_float": "30000"}, format="json", **key())
        r = self.manager.post("/api/cash/movements", {"kind": "daily_pay", "amount": "5000", "reason": "日払い", "employee_id": self.cast1.id}, format="json", **key())
        mv_id = r.json()["id"]
        r = self.manager.post(f"/api/cash/movements/{mv_id}/void", {}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.manager.post(f"/api/cash/movements/{mv_id}/void", {"reason": "誤登録"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        ep = EmployeePayment.objects.get(cash_movement_id=mv_id)
        self.assertTrue(ep.is_voided)
        self.assertEqual(Decimal(payroll.compute_statement(self.store, self.cast1, self.today, self.today)["paid_amount"]), Decimal("0"))
        day = CashMovement.objects.get(id=mv_id).business_day
        self.assertEqual(Decimal(day_summary(day)["theoretical_cash"]), Decimal("30000"))
        r = self.manager.post(f"/api/cash/movements/{mv_id}/void", {"reason": "x"}, format="json")
        self.assertEqual(r.status_code, 409)

    def test_non_cash_payment_api_rejects_cash(self):
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast1.id, "amount": "3000", "method": "cash"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.owner.post("/api/staff/payments", {"employee_id": self.cast1.id, "amount": "3000", "method": "bank", "kind": "settlement"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(payroll.compute_statement(self.store, self.cast1, self.today, self.today)["paid_amount"]), Decimal("3000"))
        self.assertIsNone(EmployeePayment.objects.get(id=r.json()["id"]).cash_movement_id)
        # 店長は支払集計（全員）を見られない / スタッフも不可
        self.assertEqual(self.manager.get("/api/staff/payments").status_code, 403)
        self.assertEqual(self.staff.get("/api/staff/payments").status_code, 403)
        self.assertEqual(self.owner.get("/api/staff/payments").status_code, 200)

    def test_pay_period_close_api(self):
        self.shift(self.cast1, hours=2)
        r = self.owner.post("/api/staff/pay-periods", {"start_date": (self.today - timedelta(days=14)).isoformat(), "end_date": self.today.isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        detail = self.owner.get(f"/api/staff/pay-periods/{pid}").json()
        self.assertEqual(detail["status"], "open")
        preview = {p["employee_id"]: p for p in detail["preview"]}
        self.assertEqual(preview[self.cast1.id]["wage_amount"], "3000")
        r = self.owner.post(f"/api/staff/pay-periods/{pid}/close", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "closed")
        statements = {s["employee"]: s for s in r.json()["statements"]}
        self.assertEqual(Decimal(statements[self.cast1.id]["gross_amount"]), Decimal("3000"))
        self.assertEqual(self.owner.post(f"/api/staff/pay-periods/{pid}/close", {}, format="json", **key()).status_code, 409)
        self.assertEqual(self.manager.get(f"/api/staff/pay-periods/{pid}").status_code, 403)


class SelfServiceTests(PayrollBase):
    def test_cast_sees_only_own_data(self):
        own = self.shift(self.cast1, hours=3)
        self.shift(self.cast2, hours=5)
        r = self.cast.get("/api/staff/me")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d["employee"]["id"], self.cast1.id)
        self.assertEqual([s["id"] for s in d["shifts"]], [own.id])
        self.assertEqual(d["statement"]["employee_id"], self.cast1.id)
        self.assertEqual(d["statement"]["wage_amount"], "4500")
        r = self.cast.get("/api/staff/shifts")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([s["id"] for s in r.json()["shifts"]], [own.id])
        # 他人の勤怠は操作できない
        r = self.cast.post("/api/staff/shifts/clock-in", {"employee_id": self.cast2.id}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        # キャストは従業員一覧・給与・営業操作不可
        self.assertEqual(self.cast.get("/api/staff/employees").status_code, 403)
        self.assertEqual(self.cast.get(f"/api/staff/employees/{self.cast1.id}/wages").status_code, 403)
        self.assertEqual(self.cast.get(f"/api/staff/employees/{self.cast2.id}/wages").status_code, 403)
        self.assertEqual(self.cast.get("/api/floor/state").status_code, 403)
        self.assertEqual(self.cast.get("/api/staff/statements?from=2026-01-01&to=2026-01-31").status_code, 403)

    def test_cast_clock_in_out_and_break(self):
        r = self.cast.post("/api/staff/shifts/clock-in", {}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.json()["id"]
        self.assertEqual(r.json()["employee"], self.cast1.id)
        self.assertEqual(self.cast.post("/api/staff/shifts/clock-in", {}, format="json", **key()).status_code, 409)
        r = self.cast.post(f"/api/staff/shifts/{sid}/break", {"action": "start"}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["breaks"]), 1)
        r = self.cast.post(f"/api/staff/shifts/{sid}/break", {"action": "end"}, format="json", **key())
        self.assertIsNotNone(r.json()["breaks"][0]["ended_at"])
        r = self.cast.post(f"/api/staff/shifts/{sid}/clock-out", {}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["clock_out"])
        self.assertEqual(self.cast.post(f"/api/staff/shifts/{sid}/clock-out", {}, format="json", **key()).status_code, 409)
        # 修正は管理者のみ・理由必須・履歴が残る
        self.assertEqual(self.cast.post(f"/api/staff/shifts/{sid}/correct", {"reason": "x", "note": "y"}, format="json").status_code, 403)
        self.assertEqual(self.manager.post(f"/api/staff/shifts/{sid}/correct", {"note": "y"}, format="json").status_code, 400)
        s = Shift.objects.get(id=sid)
        new_in = (s.clock_in - timedelta(minutes=30)).isoformat()
        r = self.manager.post(f"/api/staff/shifts/{sid}/correct", {"clock_in": new_in, "reason": "打刻忘れ"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(r.json()["corrections"]), 1)
        self.assertEqual(r.json()["corrections"][0]["reason"], "打刻忘れ")
        self.assertEqual(r.json()["corrections"][0]["before"]["clock_in"], s.clock_in.isoformat())

    def test_wages_require_cap_and_reauth(self):
        self.assertEqual(self.staff.get(f"/api/staff/employees/{self.cast1.id}/wages").status_code, 403)
        self.assertEqual(self.manager.get(f"/api/staff/employees/{self.cast1.id}/wages").status_code, 403)
        owner_noreauth = client_for(self.fx.users["owner"], self.store, reauth=False)
        r = owner_noreauth.get(f"/api/staff/employees/{self.cast1.id}/wages")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")
        r = owner_noreauth.post("/api/auth/reauth", {"password": "wrong"}, format="json")
        self.assertEqual(r.status_code, 401)
        r = owner_noreauth.post("/api/auth/reauth", {"password": "Test-pass-1234"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = owner_noreauth.get(f"/api/staff/employees/{self.cast1.id}/wages")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["current"], "1500.00")
        r = owner_noreauth.post(f"/api/staff/employees/{self.cast1.id}/wages", {"hourly_wage": "1700", "effective_from": self.today.isoformat()}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(payroll.wage_for(self.cast1, self.store, self.today), Decimal("1700"))
        self.assertEqual(len(owner_noreauth.get(f"/api/staff/employees/{self.cast1.id}/wages").json()["history"]), 2)

    def test_overnight_shift_minutes(self):
        start = timezone.now().replace(hour=22, minute=0, second=0, microsecond=0).astimezone(TOKYO)
        s = Shift.objects.create(employee=self.cast1, store=self.store, business_date=self.today, clock_in=start, clock_out=start + timedelta(hours=5))
        self.assertEqual(payroll.shift_minutes(s), 300)
        self.assertEqual(payroll.shift_wage(s, 300)[1], Decimal("7500"))
