"""開店・現金出納・日締め・締め後修正のテスト。"""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.cash import services
from apps.cash.models import BankDeposit, BusinessDay, ClosingRevision
from apps.core.testing import client_for, standard_fixture
from apps.sales import services as sales


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class ClosingBase(APITestCase):
    open_register = True  # 開店処理済みで始める（開店そのものを試すクラスは False）

    def setUp(self):
        self.fx = standard_fixture(open_register=self.open_register)
        self.store = self.fx.store
        self.manager = client_for(self.fx.users["manager"], self.store)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.user = self.fx.users["manager"]
        self.cast1 = self.fx.employees["cast1"]

    def open_day(self, amount="30000"):
        r = self.manager.post("/api/cash/open", {"opening_float": amount}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def sale(self, method, amount, product=None):
        v = sales.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        totals, _ = sales.quote(v)
        self.assertEqual(totals.total, Decimal("2500"))
        if amount != Decimal("2500"):
            sales.add_discount(self.store, self.user, v.id, {"amount": str(Decimal("2500") - Decimal(amount)), "reason": "調整"})
        return sales.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": method, "amount": str(amount), "tendered": str(amount), "debtor_name": "x"}]})

    def move(self, kind, amount, **extra):
        r = self.manager.post("/api/cash/movements", {"kind": kind, "amount": str(amount), "reason": "テスト", **extra}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def current(self):
        return self.manager.get("/api/cash/current").json()


class OpenDayTests(ClosingBase):
    open_register = False

    def test_open_with_float_and_defaults(self):
        cur = self.current()
        self.assertIsNone(cur["day"])
        self.assertEqual(Decimal(cur["suggested_opening_float"]), Decimal("50000"))  # 店舗設定の既定値
        self.assertFalse(cur["suggested_from_previous_day"])
        d = self.open_day("30000")
        self.assertEqual(d["status"], "open")
        self.assertEqual(Decimal(d["opening_float"]), Decimal("30000"))
        self.assertFalse(d["opening_float_carried"])
        self.assertEqual(Decimal(d["summary"]["theoretical_cash"]), Decimal("30000"))
        # 再度 open すると釣銭を更新するだけ（営業日は増えない）
        d2 = self.open_day("31000")
        self.assertEqual(d2["id"], d["id"])
        self.assertEqual(Decimal(d2["opening_float"]), Decimal("31000"))
        self.assertEqual(BusinessDay.objects.filter(store=self.store).count(), 1)
        r = self.manager.post("/api/cash/open", {"opening_float": "-1"}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_nothing_can_be_sold_until_register_is_opened(self):
        """開店（レジ開け）していなければ、入店・入出金はできない（POS と同じ運用）。営業日も勝手に開かない。"""
        r = self.manager.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "register_not_open")
        r = self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "500", "reason": "氷"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertFalse(BusinessDay.objects.filter(store=self.store).exists())
        # 出勤は開店前でもできる
        r = self.manager.post("/api/staff/shifts/clock-in", {"employee_id": self.fx.employees["cast1"].id}, format="json", **key())
        self.assertEqual(r.status_code, 201)
        # 開店すれば売れる
        self.open_day("30000")
        self.sale("cash", Decimal("2500"))
        self.assertEqual(BusinessDay.objects.get(store=self.store).opening_float, Decimal("30000"))


class TheoreticalCashTests(ClosingBase):
    def test_formula(self):
        self.open_day("30000")
        self.sale("cash", Decimal("2500"))
        self.sale("cash", Decimal("2500"))
        co_card = self.sale("card", Decimal("2500"))
        self.sale("receivable", Decimal("2500"))
        self.move("expense", 1200)
        self.move("daily_pay", 3000, employee_id=self.cast1.id)
        self.move("other_in", 500)
        self.move("other_out", 700)
        self.move("mid_collection", 20000)
        self.move("float_adjust_in", 1000)
        sales.refund_checkout(self.store, self.user, co_card.id, {"amount": "300", "method": "card", "reason": "x"})
        co_cash = self.sale("cash", Decimal("2500"))
        sales.refund_checkout(self.store, self.user, co_cash.id, {"amount": "800", "method": "cash", "reason": "x"})
        s = self.current()["day"]["summary"]
        self.assertEqual(Decimal(s["cash_sales"]), Decimal("7500"))
        self.assertEqual(Decimal(s["card_sales"]), Decimal("2500"))
        self.assertEqual(Decimal(s["receivable_sales"]), Decimal("2500"))
        self.assertEqual(Decimal(s["sales_total"]), Decimal("12500"))
        self.assertEqual(s["checkout_count"], 5)
        self.assertEqual(Decimal(s["cash_in_other"]), Decimal("1500"))
        self.assertEqual(Decimal(s["cash_out_expense"]), Decimal("1200"))
        self.assertEqual(Decimal(s["cash_out_daily_pay"]), Decimal("3000"))
        self.assertEqual(Decimal(s["cash_out_other"]), Decimal("700"))
        self.assertEqual(Decimal(s["cash_out_mid_collection"]), Decimal("20000"))
        self.assertEqual(Decimal(s["cash_refunds"]), Decimal("800"))
        self.assertEqual(Decimal(s["card_refunds"]), Decimal("300"))
        expected = Decimal("30000") + 7500 + 1500 - 800 - 1200 - 3000 - 700 - 20000
        self.assertEqual(Decimal(s["theoretical_cash"]), expected)  # 13300: カード売上・未収は含まない

    def test_voided_movement_excluded(self):
        self.open_day("10000")
        m = self.move("expense", 1000)
        self.assertEqual(Decimal(self.current()["day"]["summary"]["theoretical_cash"]), Decimal("9000"))
        self.manager.post(f"/api/cash/movements/{m['id']}/void", {"reason": "誤り"}, format="json")
        self.assertEqual(Decimal(self.current()["day"]["summary"]["theoretical_cash"]), Decimal("10000"))

    def test_movement_validation(self):
        self.open_day()
        r = self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "0", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "100"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.manager.post("/api/cash/movements", {"kind": "bogus", "amount": "100", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 400)


class CloseDayTests(ClosingBase):
    def _prepare(self):
        self.open_day("30000")
        self.sale("cash", Decimal("2500"))  # theoretical 32500
        return self.current()["day"]

    def test_close_with_denominations(self):
        day = self._prepare()
        denoms = {"10000": 3, "1000": 2, "500": 1}  # 32500
        r = self.manager.post("/api/cash/close", {"denominations": denoms, "counted_cash": "32000", "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 金種合計と不一致
        r = self.manager.post("/api/cash/close", {"denominations": denoms, "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d["status"], "closed")
        self.assertEqual(Decimal(d["counted_cash"]), Decimal("32500"))
        self.assertEqual(Decimal(d["theoretical_cash"]), Decimal("32500"))
        self.assertEqual(Decimal(d["difference"]), Decimal("0"))
        self.assertEqual(Decimal(d["set_aside_amount"]), Decimal("2500"))
        self.assertEqual(Decimal(d["shortage_amount"]), Decimal("0"))
        self.assertEqual(Decimal(d["cash_sales"]), Decimal("2500"))
        self.assertEqual(d["denominations"], denoms)
        self.assertEqual(d["closed_by_name"], str(self.fx.users["manager"]))
        self.assertIsNotNone(d["closed_at"])
        self.assertEqual(d["id"], day["id"])
        # 締め後は営業中の営業日なし → 再締めは 409
        r = self.manager.post("/api/cash/close", {"counted_cash": "1", "next_day_float": "0"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "no_open_day")
        self.assertIsNone(self.current()["day"])
        from apps.core.exceptions import ApiError
        with self.assertRaises(ApiError):
            services.close_business_day(BusinessDay.objects.get(id=day["id"]), self.user, counted_cash=1, next_day_float=0)

    def test_difference_requires_reason(self):
        self._prepare()
        r = self.manager.post("/api/cash/close", {"counted_cash": "32000", "next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertIn("差額", r.json()["error"]["message"])
        r = self.manager.post("/api/cash/close", {"counted_cash": "32000", "next_day_float": "30000", "difference_reason": "釣銭渡し間違い"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["difference"]), Decimal("-500"))
        self.assertEqual(r.json()["difference_reason"], "釣銭渡し間違い")

    def test_shortage_when_counted_below_next_float(self):
        self._prepare()
        r = self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "40000"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["set_aside_amount"]), Decimal("0"))
        self.assertEqual(Decimal(r.json()["shortage_amount"]), Decimal("7500"))

    def test_missing_inputs(self):
        self._prepare()
        r = self.manager.post("/api/cash/close", {"counted_cash": "32500"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.manager.post("/api/cash/close", {"next_day_float": "30000"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "-1"}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_next_day_float_carried(self):
        day = self._prepare()
        self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "12345"}, format="json", **key())
        cur = self.current()
        self.assertEqual(Decimal(cur["suggested_opening_float"]), Decimal("12345"))
        self.assertTrue(cur["suggested_from_previous_day"])
        self.assertEqual(cur["previous_day"]["date"], day["date"])
        from datetime import date as _d
        nxt = services.get_or_open_business_day(self.store, self.user, for_date=_d.fromisoformat(day["date"]) + timedelta(days=1))
        self.assertEqual(nxt.opening_float, Decimal("12345"))
        self.assertTrue(nxt.opening_float_carried)
        codes = [w["code"] for w in services.precheck(nxt)]
        self.assertIn("float_carried", codes)
        # 営業中の営業日がある間は、締め済みの日付を指定しても新しい営業日は作られない
        same = services.get_or_open_business_day(self.store, self.user, for_date=_d.fromisoformat(day["date"]))
        self.assertEqual(same.id, nxt.id)
        self.assertEqual(BusinessDay.objects.filter(store=self.store).count(), 2)

    def test_bank_deposit_separate_from_sales(self):
        day = self._prepare()
        self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "30000"}, format="json", **key())
        before = self.manager.get("/api/sales/history").json()["summary"]
        r = self.manager.post(f"/api/cash/days/{day['id']}/bank-deposits", {"amount": "2500", "note": "ATM"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        after = self.manager.get("/api/sales/history").json()["summary"]
        self.assertEqual(before["total"], after["total"])
        detail = self.manager.get(f"/api/cash/days/{day['id']}").json()
        self.assertEqual(Decimal(detail["summary"]["bank_deposits_total"]), Decimal("2500"))
        self.assertEqual(Decimal(detail["summary"]["sales_total"]), Decimal("2500"))
        self.assertEqual(len(detail["bank_deposits"]), 1)
        self.assertEqual(BankDeposit.objects.get().business_day_id, day["id"])
        r = self.manager.post(f"/api/cash/days/{day['id']}/bank-deposits", {"amount": "0"}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_precheck_lists_open_visits_and_shifts(self):
        self.open_day()
        sales.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        from django.utils import timezone
        from apps.staff.models import Shift
        Shift.objects.create(employee=self.cast1, store=self.store, business_date=timezone.localdate(), clock_in=timezone.now())
        codes = {w["code"] for w in self.current()["day"]["precheck"]}
        self.assertIn("open_visits", codes)
        self.assertIn("open_shifts", codes)

    def test_day_list(self):
        self._prepare()
        self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "30000"}, format="json", **key())
        r = self.manager.get("/api/cash/days")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 1)
        self.assertEqual(r.json()[0]["status"], "closed")


class RevisionTests(ClosingBase):
    def _closed(self):
        self.open_day("30000")
        self.sale("cash", Decimal("2500"))
        r = self.manager.post("/api/cash/close", {"counted_cash": "32500", "next_day_float": "30000"}, format="json", **key())
        return r.json()

    def test_revise_requires_cap_and_writes_history(self):
        day = self._closed()
        body = {"changes": {"counted_cash": "32000", "next_day_float": "31000"}, "reason": "数え直し"}
        r = self.staff.post(f"/api/cash/days/{day['id']}/revise", body, format="json")
        self.assertEqual(r.status_code, 403)
        # 店舗の操作なのでパスワードの再確認は要らない（共有タブレットでログインしたまま使う運用）
        manager_noreauth = client_for(self.fx.users["manager"], self.store, reauth=False)
        r = manager_noreauth.post(f"/api/cash/days/{day['id']}/revise", {"changes": {"counted_cash": "32000"}}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.manager.post(f"/api/cash/days/{day['id']}/revise", {"changes": {"counted_cash": "32000"}}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.manager.post(f"/api/cash/days/{day['id']}/revise", body, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(Decimal(d["counted_cash"]), Decimal("32000"))
        self.assertEqual(Decimal(d["difference"]), Decimal("-500"))
        self.assertEqual(Decimal(d["set_aside_amount"]), Decimal("1000"))
        self.assertEqual(len(d["revisions"]), 1)
        rev = ClosingRevision.objects.get()
        self.assertEqual(rev.reason, "数え直し")
        self.assertEqual(rev.before["counted_cash"], "32500.00")
        self.assertEqual(rev.after["counted_cash"], "32000")
        self.assertEqual(rev.changed_by, self.fx.users["manager"])
        # 同じ営業日は再オープンできない（締め済み）
        r = self.manager.post("/api/cash/open", {"opening_float": "1"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        # 締め前（翌営業日）の営業日は revise できない
        from datetime import date as _d
        open_day = services.get_or_open_business_day(self.store, self.user, for_date=_d.fromisoformat(day["date"]) + timedelta(days=1))
        r = self.manager.post(f"/api/cash/days/{open_day.id}/revise", body, format="json")
        self.assertEqual(r.status_code, 409)

    def test_staff_cannot_void_movement_after_close(self):
        self.open_day("30000")
        m = self.move("expense", 1000)
        self.manager.post("/api/cash/close", {"counted_cash": "29000", "next_day_float": "29000"}, format="json", **key())
        r = self.staff.post(f"/api/cash/movements/{m['id']}/void", {"reason": "x"}, format="json")
        self.assertEqual(r.status_code, 409)
        # 店長でも締め済みのままでは取り消せない（締めの数字と合わなくなるため）
        r = self.manager.post(f"/api/cash/movements/{m['id']}/void", {"reason": "x"}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "day_closed")
        # 営業日を再開すれば取り消せる（再開は履歴に残る）
        day_id = self.manager.get("/api/cash/days").json()[0]["id"]
        self.assertEqual(self.manager.post(f"/api/cash/days/{day_id}/reopen", {"reason": "経費の入力ミス"}, format="json").status_code, 200)
        r = self.manager.post(f"/api/cash/movements/{m['id']}/void", {"reason": "x"}, format="json")
        self.assertEqual(r.status_code, 200)
