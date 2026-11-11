"""月次源泉所得税管理・帳票・訂正・納付のテスト。"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting import reports, services
from apps.accounting.models import StoreTaxProfile, WithholdingRecord
from apps.core.testing import add_employee, add_user, client_for, make_store, standard_fixture
from apps.staff.models import EmployeeStoreAssignment, WageSetting

from .helpers import contract, key, make_taxpayer, pay, prev_month_first, work


class MonthlyTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.d = prev_month_first()
        self.c1, self.c2 = self.fx.employees["cast1"], self.fx.employees["cast2"]
        contract(self.fx, self.c1, period="daily")
        contract(self.fx, self.c2, period="daily")

    def paid_month(self):
        return WithholdingRecord.objects.order_by("-id").first().paid_on.strftime("%Y-%m")

    def month(self, client=None, category="hostess_fee"):
        return (client or self.owner).get(f"/api/accounting/withholding/month?month={self.paid_month()}&category={category}")

    def act(self, action, client=None, **extra):
        return (client or self.owner).post(f"/api/accounting/withholding/month/{action}", {"month": self.paid_month(), "category": "hostess_fee", **extra},
                                           format="json", **key())

    def test_multi_cast_multi_store_aggregation(self):
        """8. 複数キャスト・複数店舗 / 9. 同一事業者の複数店舗を事業者単位で集計（店舗別は管理用の内訳）"""
        work(self.fx.store, self.c1, self.d, hours=8, back=10000)
        work(self.fx.store, self.c2, self.d, hours=4)
        pay(self.manager, self.c1, self.d)
        pay(self.manager, self.c2, self.d)
        fx2 = make_store("s2", company=self.fx.company, withholding_enabled=True)
        StoreTaxProfile.objects.create(store=fx2.store, taxpayer=self.tp)
        EmployeeStoreAssignment.objects.create(employee=self.c1, store=fx2.store, role="cast")
        WageSetting.objects.create(employee=self.c1, store=fx2.store, hourly_wage=Decimal(2000), effective_from=self.d - timedelta(days=30))
        m2 = client_for(add_user(fx2, "manager", store_role="manager", username="m_s2"), fx2.store, reauth=False)
        work(fx2.store, self.c1, self.d + timedelta(days=1), hours=5)  # 10,000
        pay(m2, self.c1, self.d + timedelta(days=1))
        # 別の事業者の店舗の支払いは含まれない
        fx3 = make_store("s3", company=self.fx.company, withholding_enabled=True)
        tp3 = services.TaxPayer.objects.create(company=self.fx.company, name="個人 夜子", entity_type="sole_proprietor")
        StoreTaxProfile.objects.create(store=fx3.store, taxpayer=tp3)
        e3 = add_employee(fx3, "other", wage=Decimal(1500))
        contract(fx3, e3, period="daily")
        m3 = client_for(add_user(fx3, "manager", store_role="manager", username="m_s3"), fx3.store, reauth=False)
        work(fx3.store, e3, self.d, hours=8)
        pay(m3, e3, self.d)
        v = self.month().json()
        t = v["totals"]
        self.assertEqual(t["people"], 2)  # 実人員（2店舗で払った cast1 は1人）
        self.assertEqual(t["payment_count"], 3)
        self.assertEqual(Decimal(t["gross_total"]), Decimal("22000") + Decimal("6000") + Decimal("10000"))
        # cast1: 1,735 ／ cast2: 6,000 → 102 ／ 店舗2の cast1: 10,000 − 5,000 → 510
        self.assertEqual(Decimal(t["tax_total"]), Decimal("1735") + Decimal("102") + Decimal("510"))
        self.assertEqual(Decimal(t["deduction_total"]), Decimal("15000"))
        self.assertEqual({s["store_name"] for s in v["by_store"]}, {self.fx.store.name, fx2.store.name})
        self.assertEqual(v["status"], "not_aggregated")
        # 店舗単位の管理集計
        # 支払日は「今日」なので、期間の終わりは実行日まで（月の 11 日以降に実行すると 40 日では足りない）
        until = timezone.localdate() + timedelta(days=1)
        s = self.manager.get(f"/api/accounting/withholding/store-summary?from={self.d}&to={until}")
        self.assertEqual(s.status_code, 403)  # 店長は経理の集計を見られない
        s = self.owner.get(f"/api/accounting/withholding/store-summary?from={self.d}&to={until}").json()
        self.assertEqual(s["payment_count"], 2)

    def test_workflow_and_correction_after_confirm(self):
        """12. 月次確定後の修正: 確認後に支払いがあれば「訂正あり」になり、集計し直すまで確認できない"""
        work(self.fx.store, self.c1, self.d, hours=8, back=10000)
        pay(self.manager, self.c1, self.d)
        self.assertEqual(self.act("confirm").status_code, 409)  # 集計前
        r = self.act("aggregate")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "aggregated")
        r = self.act("confirm")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "owner_confirmed")
        work(self.fx.store, self.c2, self.d, hours=8)
        pay(self.manager, self.c2, self.d)
        v = self.month().json()
        self.assertEqual(v["status"], "corrected")
        self.assertIn("tax_total", v["snapshot_diff"])
        self.assertEqual(self.act("confirm").json()["error"]["code"], "stale")
        self.act("aggregate")
        self.assertEqual(self.act("confirm").json()["status"], "owner_confirmed")

    def test_needs_review_blocks_confirm_and_resolve(self):
        cast = self.fx.employees["staff1"]  # 契約なし → 要確認
        work(self.fx.store, cast, self.d, hours=10)  # 12,000
        pay(self.manager, cast, self.d)
        r = self.act("aggregate")
        self.assertEqual(r.json()["status"], "needs_review")
        self.assertEqual(r.json()["totals"]["needs_review_count"], 1)
        self.assertEqual(self.act("confirm").json()["error"]["code"], "needs_review")
        rec = WithholdingRecord.objects.get(employee=cast)
        r = self.owner.post(f"/api/accounting/withholding/records/{rec.id}/resolve",
                            {"category": "hostess_fee", "period_start": str(self.d), "period_end": str(self.d), "note": "業務委託の実態を確認"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        v = self.month().json()
        self.assertEqual(v["totals"]["needs_review_count"], 0)
        self.assertEqual(Decimal(v["totals"]["adjustment_total"]), Decimal("714"))  # (12,000 − 5,000) × 10.21% を支払時に未徴収
        self.assertEqual(v["adjustments"][0]["kind"], "unwithheld")

    def test_overpayment_refund_adjustment(self):
        """11. 報酬訂正・過払い: 元の明細は変えず、訂正（減額）を記録した月に集計する"""
        work(self.fx.store, self.c1, self.d, hours=8, back=10000)
        body = pay(self.manager, self.c1, self.d).json()
        rec = WithholdingRecord.objects.get(id=body["withholding_record_id"])
        r = self.owner.post("/api/accounting/withholding/adjustments", {
            "record_id": rec.id, "employee": self.c1.id, "kind": "refund", "applies_on": str(rec.paid_on),
            "target_delta": "-10000", "deduction_delta": "0", "amount_delta": "-1021", "reason": "バックの計上誤り（過払い分を返還）",
        }, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        v = self.month().json()
        self.assertEqual(Decimal(v["totals"]["tax_total"]), Decimal("714"))
        self.assertEqual(Decimal(v["totals"]["target_total"]), Decimal("12000"))
        rec.refresh_from_db()
        self.assertEqual(rec.withholding_amount, Decimal("1735"))  # 元の明細はそのまま
        self.assertEqual(self.manager.post("/api/accounting/withholding/adjustments", {}, format="json").status_code, 403)

    def test_remittance_and_status(self):
        work(self.fx.store, self.c1, self.d, hours=8, back=10000)
        pay(self.manager, self.c1, self.d)
        self.act("aggregate")
        r = self.owner.post("/api/accounting/withholding/remittances", {"month": self.paid_month(), "category": "hostess_fee", "paid_on": str(self.d),
                                                                         "amount": "1735", "method": "etax_direct"}, format="json", **key())
        self.assertEqual(r.status_code, 409)  # 経営者確認の前は記録できない
        self.act("confirm")
        self.assertEqual(self.act("awaiting").json()["status"], "awaiting_payment")
        r = self.owner.post("/api/accounting/withholding/remittances", {"month": self.paid_month(), "category": "hostess_fee", "paid_on": str(self.d),
                                                                         "amount": "1000", "method": "etax_direct"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertFalse(r.json()["check"]["matched"])
        self.assertEqual(Decimal(r.json()["month"]["unpaid_total"]), Decimal("735"))
        r = self.owner.post("/api/accounting/withholding/remittances", {"month": self.paid_month(), "category": "hostess_fee", "paid_on": str(self.d),
                                                                         "amount": "735", "method": "etax_direct"}, format="json", **key())
        self.assertTrue(r.json()["check"]["matched"])
        self.assertEqual(r.json()["month"]["status"], "paid")

    def test_forms_and_exports(self):
        """17. 帳票出力の正確性: 区分コード08・実人員・支払額・税額・摘要（支払額からの控除額）・納期等の区分"""
        work(self.fx.store, self.c1, self.d, hours=8, back=10000)
        work(self.fx.store, self.c2, self.d, hours=4)
        pay(self.manager, self.c1, self.d)
        pay(self.manager, self.c2, self.d)
        rec = WithholdingRecord.objects.first()
        form = reports.hostess_form(self.tp, rec.paid_on)
        row = form["rows"][0]
        self.assertEqual((row["code"], row["label"], row["people"]), ("08", "ホステス等の報酬・料金", 2))
        self.assertEqual(Decimal(row["payment_amount"]), Decimal("28000"))
        self.assertEqual(Decimal(row["tax"]), Decimal("1837"))
        self.assertEqual([t["label"] for t in form["tekiyo"]], ["支払額からの控除額"])
        self.assertEqual(Decimal(form["tekiyo"][0]["amount"]), Decimal("10000"))
        self.assertEqual(form["nouki"]["code"], f"5{rec.paid_on.year - 2018:02d}{rec.paid_on.month:02d}")
        self.assertIn("取り込める形式ではありません", form["notice"])
        m = rec.paid_on.strftime("%Y-%m")
        for kind in ("form.pdf", "summary.pdf"):
            r = self.owner.get(f"/api/accounting/withholding/{kind}?month={m}")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r["Content-Type"], "application/pdf")
            self.assertTrue(r.content.startswith(b"%PDF"))
        r = self.owner.get(f"/api/accounting/withholding/detail.csv?month={m}")
        text = r.content.decode("utf-8-sig")
        self.assertIn("源泉所得税", text.splitlines()[0])
        self.assertEqual(len(text.strip().splitlines()), 3)
        r = self.manager.get(f"/api/accounting/daily-payouts.pdf?date={self.d}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_salary_form_from_external_payroll(self):
        """給与は給与計算ソフトの結果を入力して集計する（納期の特例は半年ごと）"""
        self.tp.payroll_due_special = True
        self.tp.save()
        r = self.owner.post("/api/accounting/withholding/salary-entries", {"employee": self.fx.employees["staff1"].id, "kind": "regular",
                                                                           "paid_on": "2026-03-25", "gross_amount": "180000", "withholding_amount": "3770"},
                            format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        form = reports.salary_form(self.tp, services.date(2026, 3, 1))
        self.assertIn("納期特例分", form["form"])
        self.assertEqual(form["due_date"], "2026-07-10")
        self.assertEqual(form["nouki"]["code"], "自50801 至50806")
        regular = next(x for x in form["rows"] if x["kind"] == "regular")
        self.assertEqual((regular["people"], Decimal(regular["payment_amount"]), Decimal(regular["tax"])), (1, Decimal("180000"), Decimal("3770")))
