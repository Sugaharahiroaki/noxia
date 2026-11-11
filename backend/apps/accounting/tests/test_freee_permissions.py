"""仕訳・freee 連携・権限・テナント境界のテスト。"""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from apps.accounting import freee, journals
from apps.accounting.freee import MockFreeeClient
from apps.accounting.models import AccountingAccessGrant, CastContract, FreeeConnection, FreeeSync, Journal
from apps.core.business_date import business_date_now
from apps.core.models import CompanyMembership
from apps.core.testing import client_for, make_user, standard_fixture

from .helpers import contract, key, make_taxpayer, pay, prev_month_first, tiny_pdf, tiny_png, work

MEDIA = tempfile.mkdtemp(prefix="noxia-test-media-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


def close_day(client):
    s = client.get("/api/cash/current").json()["day"]["summary"]
    r = client.post("/api/cash/close", {"counted_cash": s["theoretical_cash"], "next_day_float": "0"}, format="json", **key())
    assert r.status_code == 200, r.content


class JournalFreeeTests(TestCase):
    def setUp(self):
        MockFreeeClient.calls = []
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.cast = self.fx.employees["cast1"]
        contract(self.fx, self.cast, period="daily")
        self.d = prev_month_first()
        self.today = business_date_now(self.fx.store)

    def build(self):
        r = self.owner.post("/api/accounting/journals/build", {"from": str(self.d), "to": str(self.today)}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_daily_journal_balances_with_withholding(self):
        """14. 日払いの現金と預り金: 日次仕訳は 報酬 = 現金 + 預り金 で貸借一致"""
        work(self.fx.store, self.cast, self.d, hours=8, back=10000)
        pay(self.manager, self.cast, self.d)
        self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "1100", "reason": "氷"}, format="json", **key())
        close_day(self.manager)
        self.build()
        j = Journal.objects.get(source_type="daily")
        dr = sum(Decimal(x["amount"]) for x in j.lines if x["side"] == "debit")
        cr = sum(Decimal(x["amount"]) for x in j.lines if x["side"] == "credit")
        self.assertEqual(dr, cr)
        # 貸借は合う（仮勘定の警告なし）。科目未設定の現金経費は「雑費として仮に計上」の案内だけ出る
        self.assertEqual([w for w in j.warnings if "仮勘定" in w], [])
        self.assertTrue(any("勘定科目が未設定" in w for w in j.warnings))
        keys = {(x["side"], x["key"]): Decimal(x["amount"]) for x in j.lines}
        self.assertEqual(keys[("debit", "cast_fee")], Decimal("22000"))
        self.assertEqual(keys[("credit", "withholding_payable")], Decimal("1735"))
        self.assertEqual(keys[("debit", "misc_expense")], Decimal("1100"))
        pl = self.owner.get(f"/api/accounting/pl?from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(pl["profit"]), Decimal("-23100"))  # 売上なし、報酬と経費の分だけ赤字（実際の記録どおり）

    def test_owner_funds_are_not_sales_or_expense(self):
        """個人資金の投入は売上・経費にしない（法人は役員借入金、個人事業主は事業主借）"""
        r = self.owner.post("/api/accounting/owner-transfers", {"date": str(self.d), "direction": "in", "amount": "500000", "method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        r = self.owner.post("/api/accounting/expenses", {"date": str(self.d), "amount": "33000", "account_key": "rent", "paid_from": "owner_personal",
                                                          "counterparty": "大家"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.build()
        names = journals.account_names(self.tp)
        self.assertEqual(names["owner_in"], "役員借入金")
        t = Journal.objects.get(source_type="owner_transfer")
        self.assertEqual({x["key"] for x in t.lines}, {"bank", "owner_in"})
        e = Journal.objects.get(source_type="expense")
        self.assertEqual({(x["side"], x["key"]) for x in e.lines}, {("debit", "rent"), ("credit", "owner_in")})
        pl = self.owner.get(f"/api/accounting/pl?from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(pl["total_income"]), Decimal(0))
        self.assertEqual(Decimal(pl["total_expense"]), Decimal("33000"))
        self.tp.entity_type = "sole_proprietor"
        self.tp.save()
        self.assertEqual(journals.account_names(self.tp)["owner_in"], "事業主借")
        self.assertEqual(journals.account_names(self.tp)["owner_out"], "事業主貸")

    def test_freee_no_double_registration(self):
        """13. freee への二重登録防止: 同じ仕訳は一度だけ作成。変更は更新、取消は削除。送信途中で止まったものは自動で再送しない"""
        self.owner.post("/api/accounting/expenses", {"date": str(self.d), "amount": "5500", "account_key": "supplies", "paid_from": "bank"}, format="json", **key())
        self.build()
        self.assertEqual(self.owner.post("/api/accounting/freee/connect", {"mode": "mock"}, format="json").json()["status"], "connected")
        r = self.owner.post("/api/accounting/freee/send", {}, format="json")
        self.assertEqual(r.json()["result"]["sent"], 1)
        self.assertEqual([c["action"] for c in MockFreeeClient.calls], ["create"])
        sync = FreeeSync.objects.get()
        self.assertEqual(sync.status, "synced")
        self.assertIsNotNone(sync.freee_id)
        # 再送・作り直しをしても作成は1回だけ
        self.owner.post("/api/accounting/freee/send", {}, format="json")
        self.build()
        self.owner.post("/api/accounting/freee/send", {}, format="json")
        self.assertEqual([c["action"] for c in MockFreeeClient.calls], ["create"])
        self.assertIn("[NOXIA:", MockFreeeClient.calls[0]["body"]["details"][0]["description"])
        # 取消＋訂正 → 元は freee から削除、訂正後の仕訳を新しく作成
        exp_id = Journal.objects.get().source_id
        r = self.owner.post(f"/api/accounting/expenses/{exp_id}/void", {"reason": "金額誤り", "correction": {"amount": "6600"}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.build()
        self.assertEqual(FreeeSync.objects.get(journal__source_id=exp_id).status, "needs_delete")
        self.owner.post("/api/accounting/freee/send", {}, format="json")
        self.assertEqual([c["action"] for c in MockFreeeClient.calls], ["create", "delete", "create"])
        # 送信中のまま止まった仕訳は「結果不明」にして送らない
        self.owner.post("/api/accounting/owner-transfers", {"date": str(self.d), "direction": "in", "amount": "1000", "method": "bank"}, format="json", **key())
        self.build()
        stuck = FreeeSync.objects.get(journal__source_type="owner_transfer")
        stuck.status = "sending"
        stuck.last_attempt_at = stuck.updated_at - timedelta(hours=1)
        stuck.save()
        self.owner.post("/api/accounting/freee/send", {}, format="json")
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, "unknown")
        self.assertEqual(len(MockFreeeClient.calls), 3)
        r = self.owner.post(f"/api/accounting/freee/syncs/{stuck.id}/resolve", {"freee_id": 777}, format="json")
        self.assertEqual(r.json()["status"], "synced")

    def test_failed_send_can_be_retried(self):
        self.owner.post("/api/accounting/expenses", {"date": str(self.d), "amount": "5500", "account_key": "supplies", "paid_from": "bank"}, format="json", **key())
        self.build()
        self.owner.post("/api/accounting/freee/connect", {"mode": "mock"}, format="json")
        original = MockFreeeClient.create_manual_journal

        def boom(self_, body):
            raise freee.FreeeError("freee API エラー (503)", status=503)

        MockFreeeClient.create_manual_journal = boom
        try:
            r = self.owner.post("/api/accounting/freee/send", {}, format="json").json()
        finally:
            MockFreeeClient.create_manual_journal = original
        self.assertEqual(r["result"]["failed"], 1)
        sync = FreeeSync.objects.get()
        self.assertEqual(sync.status, "failed")
        self.assertIn("503", sync.last_error)
        r = self.owner.post("/api/accounting/freee/send", {}, format="json").json()
        self.assertEqual(r["result"]["sent"], 1)
        self.assertEqual(sync.logs.count(), 2)

    def test_live_requires_server_flag_and_mapping(self):
        conn = FreeeConnection.objects.create(taxpayer=self.tp, mode="live", status="connected", freee_company_id=1, access_token="x")
        with override_settings(FREEE_LIVE_ENABLED=False):
            r = self.owner.post("/api/accounting/freee/connect", {"mode": "live"}, format="json")
            self.assertEqual(r.json()["error"]["code"], "freee_live_disabled")
        self.owner.post("/api/accounting/expenses", {"date": str(self.d), "amount": "5500", "account_key": "supplies", "paid_from": "bank"}, format="json", **key())
        self.build()
        with self.assertRaises(freee.FreeeError):
            freee.manual_journal_body(Journal.objects.get(), conn, require_ids=True)  # 勘定科目の対応付けが未設定


@override_settings(MEDIA_ROOT=MEDIA)
class PermissionTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store)
        self.cast_c = client_for(self.fx.users["cast"], self.fx.store)
        self.bk_user = make_user("bookkeeper1")
        self.tax_user = make_user("zeirishi1")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=self.bk_user, role="bookkeeper")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=self.tax_user, role="tax_advisor")
        self.bk = client_for(self.bk_user, self.fx.store)
        self.adv = client_for(self.tax_user, self.fx.store)
        self.month = prev_month_first().strftime("%Y-%m")

    def post(self, c, url, data=None):
        return c.post(url, data or {}, format="json", **key())

    def test_role_restrictions(self):
        """19. 権限別の操作制限"""
        cast = self.fx.employees["cast1"]
        r = self.post(self.manager, "/api/accounting/contracts", {"employee": cast.id, "contract_type": "outsourcing", "start_date": "2026-01-01", "calc_period": "daily"})
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()["id"]
        self.assertEqual(r.json()["tax_review_status"], "unreviewed")  # 登録しただけでは税務区分は確定しない
        # 税務区分の確定は経営者だけ（店長・記帳代行・税理士は不可）
        review = {"tax_category": "hostess_fee", "tax_review_status": "confirmed", "review_note": "実態を確認"}
        for c in (self.manager, self.bk, self.adv):
            self.assertEqual(self.post(c, f"/api/accounting/contracts/{cid}/review", review).status_code, 403)
        self.assertEqual(self.post(self.owner, f"/api/accounting/contracts/{cid}/review", review).status_code, 200)
        # 税理士はコメントだけ
        self.assertEqual(self.post(self.adv, f"/api/accounting/contracts/{cid}/advice", {"advisor_note": "実態に照らして妥当"}).status_code, 200)
        # 記帳代行: 集計・仕訳・証憑はできるが、月次の経営者確認・納付記録はできない
        self.assertEqual(self.post(self.bk, "/api/accounting/withholding/month/aggregate", {"month": self.month}).status_code, 200)
        self.assertEqual(self.post(self.bk, "/api/accounting/withholding/month/confirm", {"month": self.month}).status_code, 403)
        self.assertEqual(self.post(self.bk, "/api/accounting/withholding/remittances", {"month": self.month}).status_code, 403)
        self.assertEqual(self.post(self.bk, "/api/accounting/journals/build").status_code, 200)
        up = self.bk.post("/api/accounting/evidences", {"file": SimpleUploadedFile("r.pdf", tiny_pdf(), content_type="application/pdf"),
                                                        "kind": "receipt", "transaction_date": "2026-10-01", "amount": "1100", "counterparty": "酒屋"})
        self.assertEqual(up.status_code, 201, up.content)
        self.assertEqual(self.bk.get("/api/accounting/evidences", {"counterparty": "酒屋"}).json()[0]["id"], up.json()["id"])
        self.assertEqual(self.bk.get(f"/api/accounting/evidences/{up.json()['id']}/file").status_code, 200)
        # 記帳代行は従業員管理・日払いはできない
        self.assertEqual(self.bk.get("/api/staff/daily-payouts").status_code, 403)
        # 税理士は集計できない（閲覧とコメント）
        self.assertEqual(self.post(self.adv, "/api/accounting/withholding/month/aggregate", {"month": self.month}).status_code, 403)
        self.assertEqual(self.adv.get(f"/api/accounting/withholding/month?month={self.month}").status_code, 200)
        # キャストは経理を見られない
        self.assertEqual(self.cast_c.get(f"/api/accounting/withholding/month?month={self.month}").status_code, 403)
        # 運営者・会社管理者も税務上の判断はできない
        op = make_user("operator_x", is_platform_operator=True)
        self.assertEqual(self.post(client_for(op, self.fx.store), "/api/accounting/withholding/month/aggregate", {"month": self.month}).status_code, 200)
        self.assertEqual(self.post(client_for(op, self.fx.store), "/api/accounting/withholding/month/confirm", {"month": self.month}).status_code, 403)
        admin = make_user("admin_x")
        CompanyMembership.objects.create(user=admin, company=self.fx.company, role="admin")
        self.assertEqual(self.post(client_for(admin, self.fx.store), f"/api/accounting/contracts/{cid}/review", review).status_code, 403)

    def test_grant_expiry_and_revocation(self):
        g = AccountingAccessGrant.objects.get(user=self.bk_user)
        self.assertEqual(self.bk.get(f"/api/accounting/withholding/month?month={self.month}").status_code, 200)
        g.is_active = False
        g.save()
        self.assertEqual(self.bk.get(f"/api/accounting/withholding/month?month={self.month}").status_code, 403)

    def test_invoice_named_deduction_rejected(self):
        cast = self.fx.employees["cast1"]
        r = self.post(self.owner, "/api/accounting/contracts", {"employee": cast.id, "contract_type": "outsourcing", "start_date": "2026-01-01",
                                                               "deductions": [{"label": "インボイス控除", "calc": "percent", "value": "10"}]})
        self.assertEqual(r.status_code, 400)

    def test_contract_type_change_resets_review(self):
        c = contract(self.fx, self.fx.employees["cast1"], period="daily")
        r = self.manager.patch(f"/api/accounting/contracts/{c.id}", {"contract_type": "employment"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        c.refresh_from_db()
        self.assertEqual(c.tax_review_status, "unreviewed")


@override_settings(MEDIA_ROOT=MEDIA)
class TenancyTests(TestCase):
    """16. 他テナントへのアクセス拒否"""

    def test_cross_company_access_denied(self):
        a = standard_fixture("sa", company_code="ca")
        b = standard_fixture("sb", company_code="cb")
        tpa, tpb = make_taxpayer(a, name="A社"), make_taxpayer(b, name="B社")
        contract(a, a.employees["cast1"], period="daily")
        owner_b = client_for(b.users["owner"], b.store)
        m = prev_month_first().strftime("%Y-%m")
        # 事業者IDの差し替え
        self.assertEqual(owner_b.get(f"/api/accounting/withholding/month?month={m}&taxpayer_id={tpa.id}").status_code, 404)
        # 店舗IDの差し替え
        self.assertEqual(client_for(b.users["owner"], a.store).get(f"/api/accounting/withholding/month?month={m}").status_code, 403)
        # 他社の契約・従業員
        ca = CastContract.objects.get(company=a.company)
        self.assertEqual(owner_b.patch(f"/api/accounting/contracts/{ca.id}", {"calc_period": "monthly"}, format="json").status_code, 404)
        r = owner_b.post("/api/accounting/contracts", {"employee": a.employees["cast2"].id, "contract_type": "outsourcing", "start_date": "2026-01-01"},
                         format="json", **key())
        self.assertEqual(r.status_code, 400)
        # 他社の店舗に自社の事業者を設定できない
        self.assertEqual(owner_b.put(f"/api/accounting/stores/{a.store.id}/taxpayer", {"taxpayer_id": tpb.id}, format="json").status_code, 404)
        # A社の記帳代行担当は B社を見られない
        bk = make_user("bk_a")
        AccountingAccessGrant.objects.create(company=a.company, user=bk, role="bookkeeper")
        self.assertEqual(client_for(bk, a.store).get(f"/api/accounting/withholding/month?month={m}").status_code, 200)
        self.assertEqual(client_for(bk, b.store).get(f"/api/accounting/withholding/month?month={m}").status_code, 403)
        # 証憑の ID 差し替え
        up = client_for(a.users["owner"], a.store).post("/api/accounting/evidences", {
            "file": SimpleUploadedFile("r.png", tiny_png(), content_type="image/png"), "kind": "receipt", "transaction_date": "2026-10-01"})
        self.assertEqual(up.status_code, 201, up.content)
        self.assertEqual(owner_b.get(f"/api/accounting/evidences/{up.json()['id']}/file").status_code, 404)
