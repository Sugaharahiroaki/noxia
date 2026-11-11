"""申告書データの API: 集計→入力→計算→版→完成→出力、未対応の検出、権限、入力ガイド、監査ログの連鎖。"""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting.tests.helpers import key, make_taxpayer
from apps.core.models import CompanyMembership
from apps.core.testing import client_for, make_user, standard_fixture
from apps.tax.models import IndividualTaxProfile, TaxAuditLog, TaxReturn, verify_chain

MEDIA = tempfile.mkdtemp(prefix="noxia-test-tax-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


@override_settings(MEDIA_ROOT=MEDIA)
class TaxReturnTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor", withholding=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.year = timezone.localdate().year
        IndividualTaxProfile.objects.create(taxpayer=self.tp, trade_name="ラウンジ夜", blue_approval_status="approved", blue_approved_from_year=self.year - 1)
        d = timezone.localdate() - timedelta(days=10)
        # 売上 1,000,000（手入力仕訳）、経費 消耗品 110,000・地代家賃 300,000・事業主借で立替
        self.owner.post("/api/accounting/journals/manual", {"issue_date": str(d), "description": "売上", "lines": [
            {"side": "debit", "key": "cash", "amount": "1000000"}, {"side": "credit", "key": "sales", "amount": "1000000", "tax": "sales_10"}]}, format="json", **key())
        self.owner.post("/api/accounting/journals/manual", {"issue_date": str(d), "description": "経費", "lines": [
            {"side": "debit", "key": "supplies", "amount": "110000", "tax": "purchase_10"}, {"side": "debit", "key": "rent", "amount": "300000", "tax": "purchase_10"},
            {"side": "credit", "key": "owner_in", "amount": "410000"}]}, format="json", **key())

    def test_full_flow_blue(self):
        r = self.owner.get(f"/api/tax/returns/{self.year}")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["filing_type"], "blue_10")
        inputs = {"social_insurance": "300000", "spouse": {"has": True, "income": "0"}, "salary": {"revenue": "0", "withheld": "0"},
                  "life_insurance": {"new_general": "50000"}, "estimated_tax_paid": "0"}
        r = self.owner.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": inputs, "version": r.json()["version"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.owner.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        v = data["computed"]["values"]
        self.assertEqual(Decimal(data["business_summary"]["values"]["revenue_total"]), Decimal(1000000))
        self.assertEqual(Decimal(data["business_summary"]["values"]["expenses_total"]), Decimal(410000))
        self.assertEqual(Decimal(v["business.blue_deduction"]), Decimal(100000))
        self.assertEqual(Decimal(v["business.income"]), Decimal(490000))
        # 所得控除: 社保 300,000 + 生保 32,500 + 配偶者 380,000 + 基礎 1,040,000 → 課税所得 0
        self.assertEqual(Decimal(v["taxable_income"]), Decimal(0))
        self.assertEqual(Decimal(v["income_tax"]), Decimal(0))
        self.assertEqual(Decimal(v["tax_due"]), Decimal(0))
        self.assertEqual(data["unsupported_items"], [])
        self.assertEqual(len(data["versions"]), 1)
        rows = {x["key"]: x for x in data["computed"]["statement_rows"]}
        self.assertEqual(Decimal(rows["business_income"]["amount"]), Decimal(490000))
        self.assertEqual(Decimal(rows["blue_deduction"]["amount"]), Decimal(100000))
        # 完成 → XLSX / PDF 出力。XTX は対応表が無いので 409
        self.assertEqual(self.owner.post(f"/api/tax/returns/{self.year}/complete", {}, format="json", **key()).json()["status"], "complete")
        r = self.owner.post(f"/api/tax/returns/{self.year}/exports", {"format": "xlsx_return"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        f = self.owner.get(f"/api/tax/returns/{self.year}/exports/{r.json()['id']}/file")
        self.assertEqual(f.status_code, 200)
        r = self.owner.post(f"/api/tax/returns/{self.year}/exports", {"format": "pdf_forms"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        r = self.owner.post(f"/api/tax/returns/{self.year}/exports", {"format": "xtx"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertIn(r.json()["error"]["code"], ("xtx_not_released", "xtx_not_available"))
        # ガイド
        g = self.owner.get(f"/api/tax/returns/{self.year}/guide").json()
        item = next(i for i in g["items"] if i["key"] == "business_income")
        self.assertEqual(Decimal(item["value"]), Decimal(490000))
        self.assertFalse(item["verified"])  # 作成コーナー令和8年分の項目名は未確認
        self.owner.put(f"/api/tax/returns/{self.year}/guide/progress", {"items": [{"item_key": "business_income", "checked": True}]}, format="json")
        g = self.owner.get(f"/api/tax/returns/{self.year}/guide").json()
        self.assertEqual(g["progress"]["done"], 1)
        self.assertEqual(self.owner.get(f"/api/tax/returns/{self.year}/guide.pdf").status_code, 200)
        # 申告済みの記録と監査ログの連鎖
        r = self.owner.post(f"/api/tax/returns/{self.year}/mark-filed", {"note": "e-Tax 送信済"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["versions"][0]["is_final"])
        a = self.owner.get("/api/tax/audit").json()
        self.assertTrue(a["chain_ok"])
        self.assertGreaterEqual(len(a["rows"]), 5)
        self.assertTrue(verify_chain(self.tp))

    def test_white_without_blue_and_tax_due_rounding(self):
        prof = self.tp.individual_profile
        prof.blue_approval_status = "none"
        prof.save()
        r = self.owner.get(f"/api/tax/returns/{self.year}")
        # 年度の申告区分を白色に
        r = self.owner.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": {"other_incomes": [{"kind": "miscellaneous", "amount": "3000000"}]},
                                                                    "filing_type": "white", "version": r.json()["version"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        v = self.owner.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key()).json()["computed"]["values"]
        self.assertEqual(Decimal(v["business.blue_deduction"]), Decimal(0))
        # 事業 590,000 + 雑 3,000,000 = 3,590,000 → 基礎控除 1,040,000 → 課税 2,550,000 → 税 2,550,000×10%−97,500 = 157,500 → 復興 3,307 → 160,807 → 納付 160,800
        self.assertEqual(Decimal(v["total_income"]), Decimal(3590000))
        self.assertEqual(Decimal(v["taxable_income"]), Decimal(2550000))
        self.assertEqual(Decimal(v["income_tax"]), Decimal(157500))
        self.assertEqual(Decimal(v["reconstruction_tax"]), Decimal(3307))
        self.assertEqual(Decimal(v["tax_due"]), Decimal(160800))

    def test_unsupported_blocks_completion(self):
        r = self.owner.get(f"/api/tax/returns/{self.year}")
        r = self.owner.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": {"housing_loan_credit": "100000", "other_incomes": [{"kind": "real_estate", "amount": "1"}]},
                                                                    "version": r.json()["version"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        data = self.owner.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key()).json()
        codes = {u["code"] for u in data["unsupported_items"]}
        self.assertEqual(codes, {"deduction.housing_loan", "income.real_estate"})
        self.assertEqual(data["status"], "in_review")
        r = self.owner.post(f"/api/tax/returns/{self.year}/complete", {}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        r = self.owner.post(f"/api/tax/returns/{self.year}/exports", {"format": "pdf_forms"}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_my_number_like_input_rejected(self):
        r = self.owner.get(f"/api/tax/returns/{self.year}")
        r = self.owner.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": {"memo": "番号 123456789012"}, "version": r.json()["version"]}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_permissions(self):
        self.owner.get(f"/api/tax/returns/{self.year}")
        admin = make_user("admin_tax")
        CompanyMembership.objects.create(user=admin, company=self.fx.company, role="admin")
        a = client_for(admin, self.fx.store)
        self.assertEqual(a.get(f"/api/tax/returns/{self.year}").status_code, 200)  # 閲覧は可
        self.assertEqual(a.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": {}}, format="json").status_code, 403)
        self.assertEqual(a.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key()).status_code, 403)
        manager = client_for(self.fx.users["manager"], self.fx.store)
        self.assertEqual(manager.get(f"/api/tax/returns/{self.year}").status_code, 403)
        # ログイン直後の自動再認証では入力できない
        c = client_for(self.fx.users["owner"], self.fx.store, reauth=False)
        s = c.session
        s["reauth_until"] = timezone.now().timestamp() + 3600
        s["reauth_source"] = "login"
        s.save()
        r = c.put(f"/api/tax/returns/{self.year}/inputs", {"inputs": {}}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")
        # 税理士は閲覧のみ、記帳代行は閲覧のみ
        from apps.accounting.models import AccountingAccessGrant

        adv = make_user("adv_tax")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=adv, role="tax_advisor")
        ac = client_for(adv, self.fx.store)
        self.assertEqual(ac.get(f"/api/tax/returns/{self.year}").status_code, 200)
        self.assertEqual(ac.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key()).status_code, 403)
        # 法人の事業者は対象外
        corp_fx = standard_fixture("s9", company_code="c9")
        make_taxpayer(corp_fx, name="株式会社テスト", entity_type="corporation", withholding=False)
        r = client_for(corp_fx.users["owner"], corp_fx.store).get(f"/api/tax/returns/{self.year}")
        self.assertEqual(r.status_code, 409)

    def test_profile_and_year_config(self):
        r = self.owner.put("/api/tax/profile", {"trade_name": "新屋号", "consumption_tax_status": "taxable_general"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.owner.get(f"/api/tax/returns/{self.year}")
        data = self.owner.post(f"/api/tax/returns/{self.year}/compute", {}, format="json", **key()).json()
        self.assertIn("consumption_tax.taxable", {u["code"] for u in data["unsupported_items"]})
        ys = self.owner.get("/api/tax/years").json()
        self.assertEqual(ys[0]["year"], 2026)
        cfg = self.owner.get("/api/tax/years/2026/config").json()
        self.assertEqual(cfg["deductions"]["basic"]["table"][0][1], 1040000)
        # 設定の変更は運営者だけ
        self.assertEqual(self.owner.put("/api/tax/years/2026/config", {"note": "x"}, format="json").status_code, 403)
        op = make_user("op_tax", is_platform_operator=True)
        self.assertEqual(client_for(op, self.fx.store).put("/api/tax/years/2026/config", {"note": "確認済み", "status": "verified"}, format="json").status_code, 200)
        # 計算の版には設定ハッシュが入る
        tr = TaxReturn.objects.get(taxpayer=self.tp)
        self.assertEqual(tr.versions.first().config_hash, tr.config.content_hash)
        self.assertGreater(TaxAuditLog.objects.filter(taxpayer=self.tp).count(), 0)
