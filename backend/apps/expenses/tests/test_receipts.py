"""Phase 2 領収書ワークフロー: 登録・AI 読取（模擬）・重複・承認（店舗現金／口座／個人資金）・差戻し・却下・訂正・権限。"""
import io
import random
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.business_date import business_date_now

from apps.accounting import books, journals
from apps.accounting.models import Expense, Journal, JournalLine
from apps.accounting.tests.helpers import key, make_taxpayer, tiny_pdf, tiny_png
from apps.cash.models import CashMovement
from apps.core import jobs
from apps.core.models import CompanySettings
from apps.core.testing import add_user, client_for, make_store, make_user, standard_fixture
from apps.expenses.models import ExpenseReceipt, ExpenseReceiptEvent
from apps.expenses.ocr import providers

MEDIA = tempfile.mkdtemp(prefix="noxia-test-expenses-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


def pattern_png(seed: int, dot: bool = False) -> bytes:
    """ハッシュが互いに離れる画像（seed ごとに違う模様）。dot=True は同じ模様に小さな点を足した「よく似た別ファイル」。"""
    from PIL import Image, ImageDraw

    rnd = random.Random(seed)
    img = Image.new("RGB", (1200, 900), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x0, y0 = rnd.randint(0, 1100), rnd.randint(0, 800)
        d.rectangle([x0, y0, x0 + rnd.randint(20, 300), y0 + rnd.randint(20, 300)], fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    if dot:
        d.rectangle([5, 5, 8, 8], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def upload(client, data: bytes, name="r.png", ctype="image/png", **fields):
    payload = {"file": SimpleUploadedFile(name, data, content_type=ctype), "payment_method": "store_cash", **fields}
    return client.post("/api/expenses/receipts", payload, **key())


def close_day(client):
    s = client.get("/api/cash/current").json()["day"]["summary"]
    r = client.post("/api/cash/close", {"counted_cash": s["theoretical_cash"], "next_day_float": "0"}, format="json", **key())
    assert r.status_code == 200, r.content
    return r.json()


@override_settings(MEDIA_ROOT=MEDIA, RECEIPT_AI_PROVIDER="mock")
class ReceiptFlowTests(TestCase):
    def setUp(self):
        providers.MOCK_OVERRIDES.clear()
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor", withholding=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.staff = client_for(self.fx.users["staff"], self.fx.store, reauth=False)
        self.today = business_date_now(self.fx.store)  # 暦日ではなく営業日（深夜 0〜6 時のずれを避ける）
        self.yesterday = self.today - timedelta(days=1)

    def enable_ai(self, on=True):
        cs, _ = CompanySettings.objects.get_or_create(company=self.fx.company)
        cs.ai_enabled = on
        cs.save()

    def submit_receipt(self, client=None, data=None, **fields):
        client = client or self.staff
        fields.setdefault("receipt_date", self.yesterday.isoformat())
        fields.setdefault("total_amount", "3240")
        fields.setdefault("vendor", "○○マート")
        fields.setdefault("summary", "氷・レモン")
        r = upload(client, data or tiny_png(), submit="1", **fields)
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def account_id(self, key_):
        return books.account_for(self.tp, key_).id

    # --- 登録・AI ---
    def test_ai_disabled_goes_to_manual_pending(self):
        d = self.submit_receipt()
        self.assertEqual(d["status"], "pending")
        self.assertEqual(d["ocr"]["status"], "skipped")
        self.assertIn("手入力", d["ocr"]["error"])
        self.assertEqual(d["poll_after_ms"], 0)
        # スタッフ向けの応答に科目・税区分は無い
        for k in ("account", "approved_tax_category", "duplicate_candidates", "approved_note"):
            self.assertNotIn(k, d)
        self.assertEqual(ExpenseReceiptEvent.objects.filter(receipt_id=d["id"]).values_list("action", flat=True).count(), 3)  # create, ocr_skipped, submit

    def test_ai_mock_reads_and_prefills_values(self):
        self.enable_ai()
        r = upload(self.staff, tiny_png())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["status"], "ocr_running")
        self.assertEqual(d["poll_after_ms"], 2000)
        self.assertEqual(jobs.run_pending(), 1)
        d = self.staff.get(f"/api/expenses/receipts/{d['id']}").json()
        self.assertEqual(d["status"], "draft")  # まだ「登録」していない
        self.assertEqual(d["ocr"]["status"], "done")
        self.assertIsNotNone(d["ocr"]["total"])
        self.assertEqual(d["total_amount"], d["ocr"]["total"])  # 空だった確認値に AI の値が入る
        self.assertEqual(d["receipt_date"], d["ocr"]["date"])
        self.assertNotIn("suggested_accounts", d["ocr"])  # スタッフには科目の提案を見せない
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        self.assertTrue(full["ocr"]["suggested_accounts"])
        # 値を直して登録 → 確認待ち
        r = self.staff.patch(f"/api/expenses/receipts/{d['id']}", {"total_amount": "5000", "version": d["version"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.staff.post(f"/api/expenses/receipts/{d['id']}/submit", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "pending")
        self.assertEqual(r.json()["total_amount"], "5000.00")

    def test_ai_failure_falls_back_to_manual(self):
        self.enable_ai()
        data = tiny_png()
        import hashlib

        providers.MOCK_OVERRIDES[hashlib.sha256(data).hexdigest()[:16]] = providers.OcrError("模擬の失敗")
        d = upload(self.staff, data, submit="1").json()
        self.assertEqual(d["status"], "ocr_running")
        jobs.run_pending()
        d = self.staff.get(f"/api/expenses/receipts/{d['id']}").json()
        self.assertEqual(d["ocr"]["status"], "failed")
        self.assertEqual(d["status"], "pending")  # 登録済みなので確認待ちになる（手入力で続ける）
        # 再読取は AI が有効なら可能
        r = self.staff.post(f"/api/expenses/receipts/{d['id']}/ocr/retry", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["ocr"]["status"], "running")

    def test_ai_budget_exhausted_skips(self):
        self.enable_ai()
        cs = CompanySettings.objects.get(company=self.fx.company)
        cs.ai_monthly_budget_jpy = Decimal("1")
        cs.save()
        d = upload(self.staff, tiny_png()).json()
        jobs.run_pending()
        d = self.staff.get(f"/api/expenses/receipts/{d['id']}").json()
        # mock の費用は 0 円なので予算内。費用を積んで再登録すると予算超過でスキップ
        ExpenseReceipt.objects.filter(id=d["id"]).update(ocr_cost_jpy=Decimal("5"))
        d2 = upload(self.staff, pattern_png(7)).json()
        self.assertEqual(d2["ocr"]["status"], "skipped")
        self.assertIn("予算", d2["ocr"]["error"])
        avail = self.owner.get("/api/expenses/settings/ai").json()
        self.assertFalse(avail["available"])

    def test_multiple_receipts_warning_and_breakdown_mismatch(self):
        from apps.expenses.ocr.schema import validate_result

        res = validate_result({"is_receipt": True, "receipt_count": 2, "date": "2026-11-10", "vendor": "x", "total_amount": "1,100", "tax_amount": None,
                               "tax_breakdown": [{"rate": "10", "taxable_incl": "1000", "tax": None}], "invoice_registration_number": "T12345678901234",
                               "payment_method": "cash", "summary": "s", "suggested_accounts": [{"key": "nope", "reason": ""}], "missing_fields": [],
                               "warnings": []}, account_keys={"supplies"}, provider="mock", model="m")
        codes = {w["code"] for w in res.warnings}
        self.assertIn("multiple_receipts", codes)
        self.assertIn("breakdown_mismatch", codes)
        self.assertIn("bad_invoice_number", codes)
        self.assertEqual(res.total, Decimal("1100"))
        self.assertEqual(res.suggested_accounts, [])
        self.assertTrue(res.needs_retry)

    def test_manual_submit_requires_amount_and_date(self):
        r = upload(self.staff, tiny_png(), submit="1")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertEqual(r.json()["error"]["details"].get("total_amount") or r.json()["error"]["details"].get("receipt_date"), "必須")
        r = upload(self.staff, tiny_png(), receipt_date=(timezone.localdate() + timedelta(days=1)).isoformat())
        rid = r.json()["id"]
        r = self.staff.patch(f"/api/expenses/receipts/{rid}", {"receipt_date": (timezone.localdate() + timedelta(days=1)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.staff.patch(f"/api/expenses/receipts/{rid}", {"invoice_number": "T123"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.staff.patch(f"/api/expenses/receipts/{rid}", {"invoice_number": "t1234567890123", "total_amount": "100", "receipt_date": self.today.isoformat()}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["invoice_number"], "T1234567890123")

    def test_invalid_file_rejected(self):
        r = upload(self.staff, b"\x89PNG....not an image", name="r.png")
        self.assertEqual(r.status_code, 400)
        r = upload(self.staff, b"MZ\x90\x00binary", name="r.jpg", ctype="image/jpeg")
        self.assertEqual(r.status_code, 400)
        r = upload(self.staff, tiny_pdf(), name="r.pdf", ctype="application/pdf", submit="1", receipt_date=self.yesterday.isoformat(), total_amount="800")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["content_type"], "application/pdf")

    def test_idempotent_upload_returns_same_receipt(self):
        k = key()
        payload = lambda: {"file": SimpleUploadedFile("r.png", tiny_png(), content_type="image/png"), "payment_method": "store_cash", "total_amount": "1000",  # noqa: E731
                           "receipt_date": self.yesterday.isoformat(), "submit": "1"}
        r1 = self.staff.post("/api/expenses/receipts", payload(), **k)
        r2 = self.staff.post("/api/expenses/receipts", payload(), **k)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(ExpenseReceipt.objects.count(), 1)

    # --- 重複 ---
    def test_duplicate_same_file_blocks_approval_without_reason(self):
        data = pattern_png(1)
        a = self.submit_receipt(data=data)
        b = self.submit_receipt(data=data)
        full = self.owner.get(f"/api/expenses/receipts/{b['id']}").json()
        reasons = {c["receipt_id"]: c["reasons"] for c in full["duplicate_candidates"]}
        self.assertIn("sha256", reasons[a["id"]])
        self.assertTrue(b["has_duplicate_warning"])
        r = self.owner.post(f"/api/expenses/receipts/{b['id']}/approve", {"account": "supplies", "version": full["version"], "payment_method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "duplicate_suspected")
        r = self.owner.post(f"/api/expenses/receipts/{b['id']}/approve", {"account": "supplies", "version": full["version"], "duplicate_override_reason": "別日の同額",
                                                                        "payment_method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["duplicate_override_reason"], "別日の同額")

    def test_duplicate_similar_image_phash(self):
        a = self.submit_receipt(data=pattern_png(2), total_amount="1000")
        b = self.submit_receipt(data=pattern_png(2, dot=True), total_amount="2000", vendor="別の店")
        full = self.owner.get(f"/api/expenses/receipts/{b['id']}").json()
        reasons = {c["receipt_id"]: c["reasons"] for c in full["duplicate_candidates"]}
        self.assertIn("phash", reasons.get(a["id"], []))
        self.assertNotIn("sha256", reasons.get(a["id"], []))

    def test_duplicate_date_amount_vendor(self):
        a = self.submit_receipt(data=pattern_png(3), vendor="酒のやまや", total_amount="4400")
        b = self.submit_receipt(data=pattern_png(4), vendor="酒のヤマヤ", total_amount="4400", receipt_date=(self.yesterday - timedelta(days=2)).isoformat())
        c = self.submit_receipt(data=pattern_png(5), vendor="酒のやまや", total_amount="4401")
        full_b = self.owner.get(f"/api/expenses/receipts/{b['id']}").json()
        reasons = {x["receipt_id"]: x["reasons"] for x in full_b["duplicate_candidates"]}
        self.assertEqual(reasons.get(a["id"]), ["date_amount_vendor"])
        full_c = self.owner.get(f"/api/expenses/receipts/{c['id']}").json()
        self.assertEqual([x["receipt_id"] for x in full_c["duplicate_candidates"]], [])

    # --- 承認 ---
    def test_approve_store_cash_creates_cash_movement_and_daily_journal_uses_account(self):
        d = self.submit_receipt(receipt_date=self.today.isoformat())  # 営業日は本日（開店済み）
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "tax_category": "purchase_10", "version": full["version"]},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j["status"], "approved")
        self.assertEqual(j["account_key"], "supplies")
        self.assertEqual(j["approved_payment_method"], "store_cash")
        mv = CashMovement.objects.get(id=j["cash_movement"])
        self.assertEqual(mv.kind, "expense")
        self.assertEqual(mv.amount, Decimal("3240"))
        self.assertEqual(mv.account.key, "supplies")
        self.assertEqual(mv.expense_receipt.id, d["id"])
        self.assertFalse(Expense.objects.exists())
        # レジ締めの理論現金から引かれる（既存の現金経費と同じ扱い）
        s = self.manager.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(s["cash_out_expense"]), Decimal("3240"))
        close_day(self.manager)
        res = journals.build(self.tp, self.today - timedelta(days=40), self.today)
        self.assertGreaterEqual(res["built"], 1)
        jr = Journal.objects.get(source_type="daily")
        line = JournalLine.objects.get(journal=jr, account__key="supplies")
        self.assertEqual(line.amount, Decimal("3240"))
        self.assertEqual(line.counterparty, "○○マート")
        self.assertFalse(any("勘定科目が未設定" in w for w in jr.warnings))
        self.assertFalse(Journal.objects.filter(source_type="expense").exists())  # 経費としての二重計上なし
        # 証憑の検索項目が確定値に同期される
        self.assertEqual(mv.expense_receipt.evidence.amount, Decimal("3240"))
        self.assertEqual(mv.expense_receipt.evidence.counterparty, "○○マート")

    def test_approve_syncs_journal_without_manual_build(self):
        """承認した経費は「仕訳を作る」を押さなくても仕訳行になり、帳簿（経費帳）に出る。訂正すると元の仕訳は取消になる。"""
        from apps.accounting import ledgers

        d = self.submit_receipt(payment_method="bank", total_amount="3300", vendor="氷屋")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        exp = Expense.objects.get(id=r.json()["expense"])
        jr = Journal.objects.get(source_type="expense", source_id=str(exp.id), is_void=False)
        self.assertEqual(JournalLine.objects.get(journal=jr, side="debit").amount, Decimal("3300"))
        book = ledgers.expense_book(self.tp, exp.date, exp.date)
        self.assertEqual(sum(Decimal(str(row["amount"])) for row in book["rows"]), Decimal("3300"))
        # 訂正 → 元の経費の仕訳は取消、新しい経費の仕訳ができる
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/correct", {"account": "supplies", "amount": "3000", "payment_method": "bank",
                                                                         "version": full["version"], "reason": "金額の読み違い"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(Journal.objects.get(source_type="expense", source_id=str(exp.id)).is_void)
        new_exp = Expense.objects.get(id=r.json()["expense"])
        self.assertEqual(JournalLine.objects.get(journal__source_type="expense", journal__source_id=str(new_exp.id), side="debit").amount, Decimal("3000"))

    def test_approve_links_existing_cash_movement(self):
        r = self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "1100", "reason": "氷"}, format="json", **key())
        mv_id = r.json()["id"]
        d = self.submit_receipt(receipt_date=self.today.isoformat(), total_amount="1100", vendor="氷屋")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        self.assertEqual(full["cash_movement_candidates"][0]["cash_movement_id"], mv_id)
        self.assertTrue(full["cash_movement_candidates"][0]["amount_match"])
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "purchases", "cash_movement_id": mv_id, "version": full["version"]},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(CashMovement.objects.filter(kind="expense").count(), 1)
        mv = CashMovement.objects.get(id=mv_id)
        self.assertEqual(mv.account.key, "purchases")
        self.assertEqual(mv.expense_receipt.id, d["id"])
        # 金額が違う出金には結び付けられない
        d2 = self.submit_receipt(data=pattern_png(9), receipt_date=self.today.isoformat(), total_amount="999")
        full2 = self.owner.get(f"/api/expenses/receipts/{d2['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d2['id']}/approve", {"account": "purchases", "cash_movement_id": mv_id, "version": full2["version"]},
                            format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_approve_bank_and_owner_personal_create_expense(self):
        d = self.submit_receipt(payment_method="bank", tax_breakdown='[{"rate":"8","taxable_incl":"1080","tax":"80"},{"rate":"10","taxable_incl":"2160","tax":"196"}]')
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": self.account_id("purchases"), "version": full["version"],
                                                                        "tax_breakdown": [{"rate": "8", "taxable_incl": "1080", "tax": "80"},
                                                                                          {"rate": "10", "taxable_incl": "2160", "tax": "196"}]},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        exp = Expense.objects.get(id=r.json()["expense"])
        self.assertEqual(exp.paid_from, "bank")
        self.assertEqual(exp.account.key, "purchases")
        self.assertEqual(len(exp.tax_breakdown), 2)
        journals.build(self.tp, self.yesterday - timedelta(days=5), self.today)
        jr = Journal.objects.get(source_type="expense", source_id=str(exp.id))
        lines = {(l.side, l.account.key, l.tax_category): l.amount for l in JournalLine.objects.filter(journal=jr)}
        self.assertEqual(lines[("debit", "purchases", "purchase_8")], Decimal("1080"))
        self.assertEqual(lines[("debit", "purchases", "purchase_10")], Decimal("2160"))
        self.assertEqual(lines[("credit", "bank", "out_of_scope")], Decimal("3240"))
        d2 = self.submit_receipt(data=pattern_png(11), payment_method="owner_personal", total_amount="500", vendor="コンビニ")
        full2 = self.owner.get(f"/api/expenses/receipts/{d2['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d2['id']}/approve", {"account": "supplies", "version": full2["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Expense.objects.get(id=r.json()["expense"]).paid_from, "owner_personal")

    def test_other_payment_method_cannot_be_approved(self):
        d = self.submit_receipt(payment_method="other")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"], "payment_method": "bank"},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_closed_business_day_requires_choice(self):
        close_day(self.manager)
        d = self.submit_receipt(receipt_date=self.today.isoformat())
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "business_day_closed")
        self.assertIn("office_cash", r.json()["error"]["details"]["options"])
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"], "closed_day_mode": "office_cash"},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        exp = Expense.objects.get(id=r.json()["expense"])
        self.assertEqual(exp.paid_from, "office_cash")
        self.assertIsNone(r.json()["cash_movement"])
        # 日付に営業日が無いときも同じ案内
        d2 = self.submit_receipt(data=pattern_png(12), receipt_date=(self.today - timedelta(days=10)).isoformat())
        full2 = self.owner.get(f"/api/expenses/receipts/{d2['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d2['id']}/approve", {"account": "supplies", "version": full2["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "business_day_missing")

    def test_return_resubmit_approve_and_reject(self):
        d = self.submit_receipt()
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/return", {"version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 理由必須
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/return", {"reason": "金額が読めません", "version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "returned")
        mine = self.staff.get("/api/expenses/receipts?status=returned").json()["items"]
        self.assertEqual(mine[0]["return_reason"], "金額が読めません")
        # 直して再提出
        r = self.staff.patch(f"/api/expenses/receipts/{d['id']}", {"total_amount": "3300", "version": mine[0]["version"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.staff.post(f"/api/expenses/receipts/{d['id']}/submit", {"version": r.json()["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "pending")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"], "payment_method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["approved_amount"], "3300.00")
        actions = list(ExpenseReceiptEvent.objects.filter(receipt_id=d["id"]).order_by("id").values_list("action", flat=True))
        self.assertEqual(actions, ["create", "ocr_skipped", "submit", "return", "edit", "resubmit", "approve"])
        # 却下
        d2 = self.submit_receipt(data=pattern_png(13))
        full2 = self.owner.get(f"/api/expenses/receipts/{d2['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d2['id']}/reject", {"reason": "私物", "version": full2["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "rejected")
        self.assertFalse(Expense.objects.filter(evidence_id=full2["evidence_id"]).exists())
        # 却下済みは承認できない
        r = self.owner.post(f"/api/expenses/receipts/{d2['id']}/approve", {"account": "supplies", "version": r.json()["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 409)

    def test_version_conflict(self):
        d = self.submit_receipt()
        r = self.staff.patch(f"/api/expenses/receipts/{d['id']}", {"memo": "x", "version": 999}, format="json")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "version_conflict")

    def test_correct_approved_receipt(self):
        d = self.submit_receipt(payment_method="bank")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        old_exp = r.json()["expense"]
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/correct", {"account": "entertainment", "amount": "3000", "version": r.json()["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 400)  # 理由必須
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/correct", {"account": "entertainment", "amount": "3000", "reason": "科目の誤り", "version": r.json().get("version") or full["version"] + 1},
                            format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        j = r.json()
        self.assertEqual(j["status"], "approved")
        self.assertEqual(j["account_key"], "entertainment")
        self.assertEqual(j["approved_amount"], "3000.00")
        self.assertTrue(Expense.objects.get(id=old_exp).is_voided)
        new_exp = Expense.objects.get(id=j["expense"])
        self.assertEqual(new_exp.corrected_from_id, old_exp)
        self.assertEqual(new_exp.amount, Decimal("3000"))
        ev = ExpenseReceiptEvent.objects.filter(receipt_id=d["id"], action="correct").first()
        self.assertEqual(ev.reason, "科目の誤り")

    def test_year_locked_blocks_approval(self):
        d = self.submit_receipt(payment_method="bank")
        books.close_year(books.get_fiscal_year(self.tp, self.yesterday.year, create=True), self.fx.users["owner"])
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        r = self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "year_locked")

    # --- 権限・見える範囲 ---
    def test_staff_sees_only_own_store_and_own_submissions(self):
        mine = self.submit_receipt()
        other_user = add_user(self.fx, "staff2", store_role="staff", username="staff2_x")
        c2 = client_for(other_user, self.fx.store, reauth=False)
        theirs = self.submit_receipt(client=c2, data=pattern_png(21))
        ids = {x["id"] for x in self.staff.get("/api/expenses/receipts").json()["items"]}
        self.assertEqual(ids, {mine["id"]})
        self.assertEqual(self.staff.get(f"/api/expenses/receipts/{theirs['id']}").status_code, 404)
        # 店長は店舗の全件
        ids = {x["id"] for x in self.manager.get("/api/expenses/receipts").json()["items"]}
        self.assertEqual(ids, {mine["id"], theirs["id"]})
        # 他店舗のスタッフは見られない
        fx2 = make_store("s2", company=self.fx.company)
        u2 = add_user(fx2, "staff", store_role="staff", username="staff_s2")
        c3 = client_for(u2, fx2.store, reauth=False)
        self.assertEqual(c3.get(f"/api/expenses/receipts/{mine['id']}").status_code, 404)
        self.assertEqual(c3.get(f"/api/expenses/receipts/{mine['id']}/file").status_code, 404)
        # スタッフは承認・却下できない
        self.assertEqual(self.staff.post(f"/api/expenses/receipts/{mine['id']}/approve", {"account": "supplies"}, format="json", **key()).status_code, 403)
        self.assertEqual(self.staff.post(f"/api/expenses/receipts/{mine['id']}/reject", {"reason": "x"}, format="json", **key()).status_code, 403)
        # 原本は自分のものなら見られる（inline）
        r = self.staff.get(f"/api/expenses/receipts/{mine['id']}/file?inline=1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("inline", r["Content-Disposition"])
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        # キャストは何も見られない
        cast = client_for(self.fx.users["cast"], self.fx.store, reauth=False)
        self.assertEqual(cast.get("/api/expenses/receipts").status_code, 403)
        # 他社のオーナーには見えない
        fxb = standard_fixture("sb", company_code="cb")
        make_taxpayer(fxb, name="B", withholding=False)
        ob = client_for(fxb.users["owner"], fxb.store)
        self.assertEqual(ob.get(f"/api/expenses/receipts/{mine['id']}").status_code, 404)
        self.assertEqual(ob.get("/api/expenses/receipts").json()["total"], 0)

    def test_manager_approval_requires_store_setting_and_limit(self):
        d = self.submit_receipt(receipt_date=self.today.isoformat(), total_amount="60000")
        self.assertEqual(self.manager.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": d["version"]}, format="json", **key()).status_code, 403)
        self.fx.settings.manager_can_approve_expenses = True
        self.fx.settings.manager_approve_limit = Decimal("50000")
        self.fx.settings.save()
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        r = self.manager.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": d["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 403)  # 上限超え
        self.assertIn("までです", r.json()["error"]["message"])
        d2 = self.submit_receipt(data=pattern_png(31), receipt_date=self.today.isoformat(), total_amount="4000")
        r = self.manager.post(f"/api/expenses/receipts/{d2['id']}/approve", {"account": "supplies", "version": d2["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "approved")
        # 店長の一覧には科目が見える（承認者）
        self.assertEqual(r.json()["account_key"], "supplies")

    def test_bookkeeper_can_approve_and_pending_count(self):
        from apps.accounting.models import AccountingAccessGrant

        bk = make_user("bk_exp")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=bk, role="bookkeeper")
        c = client_for(bk, self.fx.store)
        d = self.submit_receipt(payment_method="bank")
        counts = c.get("/api/expenses/receipts/pending-count").json()
        self.assertEqual(counts["pending"], 1)
        r = c.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": d["version"]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        counts = c.get("/api/expenses/receipts/pending-count").json()
        self.assertEqual((counts["pending"], counts["approved"]), (0, 1))
        self.assertEqual(c.get("/api/expenses/receipts/duplicates").json()["items"], [])

    def test_register_check(self):
        self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "700", "reason": "ガム"}, format="json", **key())
        d = self.submit_receipt(receipt_date=self.today.isoformat(), total_amount="3240")
        full = self.owner.get(f"/api/expenses/receipts/{d['id']}").json()
        self.owner.post(f"/api/expenses/receipts/{d['id']}/approve", {"account": "supplies", "version": full["version"]}, format="json", **key())
        rc = self.owner.get(f"/api/expenses/register-check?date={self.today}").json()
        self.assertEqual(Decimal(rc["pos_expense_total"]), Decimal("3940"))
        self.assertEqual(Decimal(rc["receipt_total"]), Decimal("3240"))
        self.assertEqual(len(rc["no_receipt"]), 1)
        self.assertEqual(len(rc["matched"]), 1)
        self.assertEqual(rc["status"], "diff")
        rng = self.owner.get(f"/api/expenses/register-check?from={self.today - timedelta(days=3)}&to={self.today}").json()
        self.assertEqual(rng["no_receipt_count"], 1)
        self.assertEqual(self.staff.get(f"/api/expenses/register-check?date={self.today}").status_code, 403)

    def test_ai_settings_permissions(self):
        self.assertEqual(self.staff.get("/api/expenses/settings/ai").status_code, 403)
        self.assertEqual(self.manager.put("/api/expenses/settings/ai", {"ai_enabled": True}, format="json").status_code, 403)
        r = self.owner.put("/api/expenses/settings/ai", {"ai_enabled": True, "ai_monthly_budget_jpy": "500"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["available"])
        self.assertEqual(r.json()["provider"], "mock")
        self.assertEqual(r.json()["budget_jpy"], "500")


@override_settings(MEDIA_ROOT=MEDIA, RECEIPT_AI_PROVIDER="none")
class ProviderSelectionTests(TestCase):
    def test_no_server_provider_means_manual(self):
        fx = standard_fixture()
        make_taxpayer(fx, withholding=False)
        CompanySettings.objects.create(company=fx.company, ai_enabled=True)
        self.assertIsNone(providers.get_provider(fx.company))
        from apps.expenses import services

        self.assertFalse(services.ai_availability(fx.company)["available"])

    def test_anthropic_requires_key(self):
        fx = standard_fixture()
        CompanySettings.objects.create(company=fx.company, ai_enabled=True, ai_provider="anthropic")
        with override_settings(RECEIPT_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY=""):
            self.assertIsNone(providers.get_provider(fx.company))
        with override_settings(RECEIPT_AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-test"):
            p = providers.get_provider(fx.company)
            self.assertIsInstance(p, providers.AnthropicProvider)


@override_settings(MEDIA_ROOT=MEDIA)
class PayableTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor", withholding=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.today = business_date_now(self.fx.store)  # 暦日ではなく営業日（深夜 0〜6 時のずれを避ける）

    def test_payable_and_payments_journals(self):
        r = self.owner.post("/api/expenses/payables", {"counterparty": "酒販ABC", "invoice_date": str(self.today), "due_date": str(self.today + timedelta(days=30)),
                                                     "amount": "33000", "account": "purchases", "description": "11月分 酒類",
                                                     "tax_breakdown": [{"rate": "10", "taxable_incl": "33000", "tax": "3000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.json()["id"]
        self.assertEqual(r.json()["status"], "open")
        self.assertEqual(Decimal(r.json()["balance"]), Decimal("33000"))
        # 発生の仕訳: 仕入高／買掛金
        journals.build(self.tp, self.today - timedelta(days=1), self.today)
        jr = Journal.objects.get(source_type="payable", source_id=str(pid))
        lines = {(l.side, l.account.key): l.amount for l in JournalLine.objects.filter(journal=jr)}
        self.assertEqual(lines[("debit", "purchases")], Decimal("33000"))
        self.assertEqual(lines[("credit", "accounts_payable")], Decimal("33000"))
        # 口座から一部支払い → 買掛金／普通預金
        r = self.owner.post(f"/api/expenses/payables/{pid}/payments", {"date": str(self.today), "amount": "10000", "method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "partially_paid")
        pay_id = r.json()["payments"][0]["id"]
        journals.build(self.tp, self.today - timedelta(days=1), self.today)
        jp = Journal.objects.get(source_type="payable_payment", source_id=str(pay_id))
        lines = {(l.side, l.account.key): l.amount for l in JournalLine.objects.filter(journal=jp)}
        self.assertEqual(lines[("debit", "accounts_payable")], Decimal("10000"))
        self.assertEqual(lines[("credit", "bank")], Decimal("10000"))
        # レジ現金で残りを支払い → レジ出金（科目＝買掛金）が作られ、別の仕訳は作らない（日次仕訳が支払いの仕訳になる）
        r = self.owner.post(f"/api/expenses/payables/{pid}/payments", {"date": str(self.today), "amount": "23000", "method": "cash_register"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["status"], "paid")
        mv = CashMovement.objects.get(kind="expense")
        self.assertEqual(mv.account.key, "accounts_payable")
        self.assertEqual(mv.amount, Decimal("23000"))
        s = self.manager.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(s["cash_out_expense"]), Decimal("23000"))
        journals.build(self.tp, self.today - timedelta(days=1), self.today)
        self.assertEqual(Journal.objects.filter(source_type="payable_payment").count(), 1)
        self.manager.post("/api/cash/close", {"counted_cash": s["theoretical_cash"], "next_day_float": "0"}, format="json", **key())
        journals.build(self.tp, self.today - timedelta(days=1), self.today)
        daily = Journal.objects.get(source_type="daily")
        self.assertEqual(JournalLine.objects.get(journal=daily, account__key="accounts_payable").amount, Decimal("23000"))
        # 残高超過の支払いは拒否、支払い済みの買掛金は取り消せない
        r = self.owner.post(f"/api/expenses/payables/{pid}/payments", {"date": str(self.today), "amount": "1", "method": "bank"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.owner.post(f"/api/expenses/payables/{pid}/void", {"reason": "x"}, format="json").status_code, 409)
        # 買掛帳に出る
        lb = self.owner.get(f"/api/accounting/ledgers/payables?from={self.today - timedelta(days=1)}&to={self.today}").json()
        self.assertEqual(lb["rows"][0]["counterparty"], "酒販ABC")
        self.assertEqual(Decimal(lb["rows"][0]["paid"]), Decimal("33000"))

    def test_payment_void_restores_balance(self):
        r = self.owner.post("/api/expenses/payables", {"counterparty": "X", "invoice_date": str(self.today), "amount": "5000", "account": "supplies"}, format="json", **key())
        pid = r.json()["id"]
        r = self.owner.post(f"/api/expenses/payables/{pid}/payments", {"date": str(self.today), "amount": "5000", "method": "owner_personal"}, format="json", **key())
        pay_id = r.json()["payments"][0]["id"]
        self.assertEqual(r.json()["status"], "paid")
        r = self.owner.post(f"/api/expenses/payables/{pid}/payments/{pay_id}/void", {"reason": "誤登録"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "open")
        self.assertEqual(Decimal(r.json()["balance"]), Decimal("5000"))
        journals.build(self.tp, self.today, self.today)
        self.assertTrue(Journal.objects.get(source_type="payable_payment", source_id=str(pay_id)).is_void)
        # 未払金（経費科目）の発生は 未払金 勘定
        jr = Journal.objects.get(source_type="payable", source_id=str(pid))
        self.assertTrue(JournalLine.objects.filter(journal=jr, account__key="accrued_card", side="credit").exists())
        self.assertEqual(self.manager.get("/api/expenses/payables").status_code, 403)
