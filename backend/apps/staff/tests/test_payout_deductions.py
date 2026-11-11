"""日払いの控除: 源泉所得税は店舗設定で差し引かない（既定）。備品代などの控除項目は、お渡しのときにチェックしたものだけ引く。"""
from __future__ import annotations

from decimal import Decimal

from apps.accounting.models import WithholdingRecord
from apps.accounting.tests.helpers import contract, enable_withholding, make_taxpayer
from apps.staff.models import EmployeePayment

from .test_daily_payout import PayoutBase, key


class WithholdingOffTests(PayoutBase):
    def test_default_off_pays_full_without_tax_review(self):
        """既定（オフ）: 事業者・契約が未設定でも「要確認」にせず全額を渡す。源泉徴収明細は作らない"""
        self.work(self.cast1)  # 5時間 × 1,500 = 7,500
        row, data = self.row()
        self.assertFalse(data["withholding_enabled"])
        self.assertEqual(row["tax"]["status"], "disabled")
        self.assertEqual(data["totals"]["tax_review_count"], 0)
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("7500"))
        r = self.pay(expected_amount=row["remaining_amount"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["withholding_amount"]), 0)
        self.assertEqual(r.json()["withholding_status"], "disabled")
        self.assertIsNone(r.json()["withholding_record_id"])
        self.assertFalse(WithholdingRecord.objects.exists())

    def test_off_even_for_confirmed_hostess_fee(self):
        """ホステス報酬と確認済みでも、オフの間は計算しない。オンにすると計算する"""
        make_taxpayer(self.fx, withholding=False)
        contract(self.fx, self.cast1, period="daily")
        self.work(self.cast1, minutes=630)  # 10時間 → 15,000
        row, _ = self.row()
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), 0)
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("15000"))
        enable_withholding(self.store)
        row, _ = self.row()
        self.assertEqual(Decimal(row["tax"]["withholding_amount"]), Decimal("1021"))  # (15,000 − 5,000) × 10.21%
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("13979"))

    def test_other_payment_paths(self):
        """レジの手入力の日払いは要確認にしない。振込の支払いで源泉徴収額を入れると拒否する"""
        make_taxpayer(self.fx, withholding=False)
        contract(self.fx, self.cast1, period="daily")
        r = self.manager.post("/api/cash/movements", {"kind": "daily_pay", "amount": "5000", "reason": "前払い", "employee_id": self.cast1.id},
                              format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(WithholdingRecord.objects.exists())
        body = {"employee_id": self.cast1.id, "amount": "10000", "method": "bank"}
        r = self.owner.post("/api/staff/payments", {**body, "withholding_amount": "500"}, format="json", **key())
        self.assertEqual(r.status_code, 400, r.content)
        r = self.owner.post("/api/staff/payments", {**body, "amount_basis": "gross"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("10000"))
        self.assertFalse(WithholdingRecord.objects.exists())

    def test_only_owner_can_change_withholding(self):
        self.assertEqual(self.manager.put("/api/store/settings", {"withholding_enabled": True}, format="json").status_code, 403)
        # 変えなければ店長も他の設定を保存できる
        self.assertEqual(self.manager.put("/api/store/settings", {"withholding_enabled": False, "alarm_mode": "10"}, format="json").status_code, 200)
        r = self.owner.put("/api/store/settings", {"withholding_enabled": True}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["withholding_enabled"])


class OptionalDeductionTests(PayoutBase):
    def setUp(self):
        super().setUp()
        r = self.manager.put("/api/store/settings", {"payout_deductions": [
            {"label": "備品代", "percent": "10"},
            {"label": "ヘアメイク", "percent": "5", "default_on": True},
        ]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.items = {d["label"]: d for d in r.json()["payout_deductions"]}
        self.supplies = self.items["備品代"]["code"]
        self.work(self.cast1)  # 7,500

    def options(self):
        row, _ = self.row()
        return row, {o["label"]: o for o in row["tax"]["deduction_options"]}

    def test_settings_validation_keeps_codes(self):
        self.assertEqual(self.items["備品代"]["percent"], "10.00")
        self.assertFalse(self.items["備品代"]["default_on"])
        r = self.manager.put("/api/store/settings", {"payout_deductions": list(self.items.values())}, format="json")
        self.assertEqual({d["code"] for d in r.json()["payout_deductions"]}, {d["code"] for d in self.items.values()})
        for bad in ([{"label": "", "percent": "10"}], [{"label": "備品代", "percent": "0"}], [{"label": "備品代", "percent": "101"}],
                    [{"label": "備品代", "percent": "abc"}], "備品代"):
            self.assertEqual(self.manager.put("/api/store/settings", {"payout_deductions": bad}, format="json").status_code, 400, bad)

    def test_options_on_screen(self):
        """選択肢は報酬総額の%。「最初からチェック」の項目だけがお渡し額に入っている"""
        row, opts = self.options()
        self.assertEqual(Decimal(opts["備品代"]["amount"]), Decimal("750"))
        self.assertFalse(opts["備品代"]["checked"])
        self.assertEqual(Decimal(opts["ヘアメイク"]["amount"]), Decimal("375"))
        self.assertTrue(opts["ヘアメイク"]["checked"])
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("7125"))
        self.assertEqual(Decimal(row["tax"]["net_without_options"]), Decimal("7500"))

    def test_checked_items_are_deducted(self):
        r = self.pay(deductions=[self.supplies], expected_amount="6750")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("6750"))
        ep = EmployeePayment.objects.get(id=r.json()["payment_id"])
        self.assertEqual(ep.deduction_amount, Decimal("750"))
        self.assertEqual([(d["label"], d["amount"]) for d in ep.deduction_items], [("備品代", "750")])
        self.assertEqual(ep.cash_movement.amount, Decimal("6750"))
        self.assertEqual(self.row()[0]["status"], "paid")

    def test_nothing_checked(self):
        r = self.pay(deductions=[], expected_amount="7500")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["other_deduction_amount"]), 0)

    def test_expected_amount_must_match_selection(self):
        """画面の額（チェックの状態で変わる）とサーバーの計算が違えば渡さない"""
        r = self.pay(deductions=[self.supplies], expected_amount="7500")
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["error"]["code"], "amount_changed")
        self.assertFalse(EmployeePayment.objects.exists())

    def test_partial_payment_does_not_deduct_twice(self):
        """金額を変えて渡したときに引いた項目は、同じ日の残りを渡すときには引かない"""
        r = self.pay(deductions=[self.supplies], amount=3000)  # 3,000 ＋ 備品代 750 = 報酬 3,750 を精算
        self.assertEqual(r.status_code, 201, r.content)
        row, opts = self.options()
        self.assertEqual(Decimal(row["remaining_gross"]), Decimal("3750"))
        self.assertTrue(opts["備品代"]["done"])
        self.assertFalse(opts["備品代"]["checked"])
        self.assertEqual(Decimal(row["remaining_amount"]), Decimal("3375"))  # ヘアメイク（最初からチェック）375 を引く
        r = self.pay(deductions=[self.supplies], expected_amount="3750")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["other_deduction_amount"]), 0)
        self.assertEqual(sum(p.deduction_amount for p in EmployeePayment.objects.all()), Decimal("750"))

    def test_statement_pdf(self):
        r = self.pay(deductions=[self.supplies], expected_amount="6750")
        self.assertEqual(r.status_code, 201, r.content)
        pdf = self.manager.get(f"/api/accounting/daily-payouts.pdf?date={self.bd.isoformat()}&employee={self.cast1.id}")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(b"".join(pdf.streaming_content if pdf.streaming else [pdf.content]).startswith(b"%PDF"))

    def test_void_makes_item_available_again(self):
        r = self.pay(deductions=[self.supplies], expected_amount="6750")
        self.manager.post(f"/api/cash/movements/{r.json()['cash_movement_id']}/void", {"reason": "取消"}, format="json")
        _, opts = self.options()
        self.assertFalse(opts["備品代"]["done"])
        self.assertEqual(Decimal(opts["備品代"]["amount"]), Decimal("750"))

    def test_deduction_does_not_reduce_withholding(self):
        """源泉所得税を差し引く設定でも、控除項目で源泉の対象額は減らない"""
        make_taxpayer(self.fx)
        contract(self.fx, self.cast1, period="daily")
        r = self.pay(deductions=[self.supplies], expected_amount="6495")  # 7,500 − 源泉 255 −備品代 750
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["withholding_amount"]), Decimal("255"))
        rec = WithholdingRecord.objects.get(payment_id=r.json()["payment_id"])
        self.assertEqual(rec.other_deduction_amount, Decimal("750"))
        self.assertEqual([d["label"] for d in rec.other_deductions], ["備品代"])
