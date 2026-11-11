"""Phase 3 銀行: CSV 解析・取込・照合・権限。"""
import shutil
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting import ledgers
from apps.accounting.models import AccountingAccessGrant, CardSettlement, Journal, JournalLine
from apps.accounting.tests.helpers import key, make_taxpayer
from apps.banking import importer
from apps.banking.models import BankAccount, BankReconciliation, BankStatementImport, BankTransaction
from apps.cash.models import BankDeposit
from apps.core.testing import add_user, client_for, make_store, make_user, standard_fixture

MEDIA = tempfile.mkdtemp(prefix="noxia-test-bank-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


def csv_bytes(rows, enc="cp932", preamble=()):
    """銀行の CSV らしく、金額のカンマを含むセルは引用符で囲む。"""
    import csv
    import io

    buf = io.StringIO()
    for p in preamble:
        buf.write(p + "\r\n")
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode(enc)


KYOSHIN_HEADER = ["取引日", "摘要", "お引出し", "お預入れ", "残高", "備考"]


def upload(data: bytes, name="meisai.csv"):
    return SimpleUploadedFile(name, data, content_type="text/csv")


class ImporterUnitTests(TestCase):
    def test_parse_amount_variants(self):
        self.assertEqual(importer.parse_amount("¥12,345"), Decimal("12345"))
        self.assertEqual(importer.parse_amount("１２，３４５円"), Decimal("12345"))
        self.assertEqual(importer.parse_amount("△1,000"), Decimal("-1000"))
        self.assertEqual(importer.parse_amount("▲500"), Decimal("-500"))
        self.assertEqual(importer.parse_amount("(500)"), Decimal("-500"))
        self.assertEqual(importer.parse_amount("－2,000"), Decimal("-2000"))
        self.assertIsNone(importer.parse_amount(""))
        self.assertIsNone(importer.parse_amount("摘要"))

    def test_parse_date_variants(self):
        self.assertEqual(importer.parse_date("2026/06/01"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("2026年6月1日"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("２０２６年０６月０１日"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("20260601"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("令和8年6月1日"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("R8.6.1"), date(2026, 6, 1))
        self.assertEqual(importer.parse_date("令和元年5月1日"), date(2019, 5, 1))
        # 年なし: 参照日 2026-01-10 で 12/28 は前年
        self.assertEqual(importer.parse_date("12/28", reference=date(2026, 1, 10)), date(2025, 12, 28))
        self.assertEqual(importer.parse_date("1/5", reference=date(2026, 1, 10)), date(2026, 1, 5))
        self.assertEqual(importer.parse_date("1/5", reference=date(2026, 1, 10), carry_year=2025), date(2025, 1, 5))

    def test_cp932_with_preamble_and_header_detection(self):
        rows = [KYOSHIN_HEADER, ["2026年06月01日", "振込 カ）ノクシアシヨウジ", "", "100,000", "1,100,000", ""],
                ["2026年06月02日", "ATM出金", "30,000", "", "1,070,000", ""]]
        data = csv_bytes(rows, "cp932", preamble=["京都信用金庫 入出金明細", "口座番号,普通 1234567", ""])
        res = importer.parse_statement(data, bank_account_id=1)
        self.assertEqual(res.encoding, "cp932")
        self.assertEqual(res.header_row, 2)
        self.assertEqual(res.column_map["date"], 0)
        self.assertEqual(res.column_map["withdrawal"], 2)
        self.assertEqual(res.column_map["deposit"], 3)
        self.assertEqual(res.column_map["balance"], 4)
        self.assertEqual(len(res.rows), 2)
        self.assertEqual(res.rows[0].amount_in, Decimal("100000"))
        self.assertEqual(res.rows[0].counterparty, "カ)ノクシアシヨウジ")  # NFKC で全角括弧は半角に
        self.assertEqual(res.rows[1].amount_out, Decimal("30000"))
        self.assertTrue(res.guessed)

    def test_utf8_sig_signed_amount_and_descending(self):
        rows = [["日付", "内容", "金額", "残高"], ["2026-06-03", "PayPay銀行 振込", "-5,000", "95,000"], ["2026-06-01", "入金", "+100,000", "100,000"]]
        data = csv_bytes(rows, "utf-8-sig")
        res = importer.parse_statement(data, bank_account_id=1)
        self.assertEqual(res.encoding, "utf-8-sig")
        self.assertEqual(res.column_map["amount_signed"], 2)
        self.assertTrue(res.descending)
        self.assertEqual([r.date for r in res.rows], [date(2026, 6, 1), date(2026, 6, 3)])
        self.assertEqual(res.rows[1].amount_out, Decimal("5000"))
        check, _note, opening, closing = importer.balance_continuity(res.rows, None)
        self.assertEqual((check, opening, closing), ("ok", Decimal("0"), Decimal("95000")))

    def test_balance_continuity_mismatch(self):
        rows = [KYOSHIN_HEADER, ["2026/06/01", "入金", "", "1,000", "11,000", ""], ["2026/06/02", "出金", "500", "", "10,000", ""]]
        res = importer.parse_statement(csv_bytes(rows), bank_account_id=1)
        check, note, *_ = importer.balance_continuity(res.rows, Decimal("10000"))
        self.assertEqual(check, "mismatch")  # 06/02 の残高が前の行とつながらない
        rows_ok = [KYOSHIN_HEADER, ["2026/06/01", "入金", "", "1,000", "11,000", ""]]
        res = importer.parse_statement(csv_bytes(rows_ok), bank_account_id=1)
        self.assertEqual(importer.balance_continuity(res.rows, Decimal("9000"))[0], "mismatch")
        self.assertEqual(importer.balance_continuity(res.rows, Decimal("10000"))[0], "ok")

    def test_row_limit(self):
        rows = [KYOSHIN_HEADER] + [["2026/06/01", f"行{i}", "1", "", "", ""] for i in range(importer.MAX_ROWS + 30)]
        from apps.core.exceptions import ApiError

        with self.assertRaises(ApiError):
            importer.parse_statement(csv_bytes(rows), bank_account_id=1)

    def test_name_normalization_and_similarity(self):
        from apps.banking.matching import normalize_name, similarity

        self.assertEqual(normalize_name("振込 カ）ノクシアシヨウジ"), normalize_name("株式会社ノクシア商事")[:0] + normalize_name("のくしあしようじ"))
        self.assertGreaterEqual(similarity("カ）ノクシアシヨウジ", "ノクシア商事"), 0.6)
        self.assertEqual(similarity("ｶ)ﾉｸｼｱ", "ノクシア"), 1.0)


@override_settings(MEDIA_ROOT=MEDIA)
class BankingApiTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor")
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.staff = client_for(self.fx.users["staff"], self.fx.store, reauth=False)
        self.today = timezone.localdate()
        r = self.owner.post("/api/banking/accounts", {"name": "京信 事業用", "bank_name": "京都信用金庫", "bank_code": "1610", "account_number_last4": "4567"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.acc = BankAccount.objects.get(id=r.json()["id"])

    def _import(self, rows, enc="cp932", name="m.csv", commit=True, **extra):
        r = self.owner.post("/api/banking/imports", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows, enc), name), **extra}, **key())
        self.assertEqual(r.status_code, 201, r.content)
        imp = r.json()
        if commit:
            c = self.owner.post(f"/api/banking/imports/{imp['id']}/commit", {}, format="json", **key())
            self.assertEqual(c.status_code, 200, c.content)
            return c.json()
        return imp

    def _rows(self, *items):
        """items: (date, 摘要, 出金, 入金, 残高)"""
        return [KYOSHIN_HEADER] + [[d.strftime("%Y/%m/%d") if isinstance(d, date) else d, desc, w, p, bal, ""] for d, desc, w, p, bal in items]

    def test_account_validation_and_permissions(self):
        self.assertEqual(self.acc.account_number_last4, "4567")
        self.assertIsNotNone(self.acc.account_id)
        self.assertEqual(self.acc.account.key, "bank")
        r = self.owner.post("/api/banking/accounts", {"name": "x", "account_number_last4": "1234567"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.manager.get("/api/banking/accounts").status_code, 403)
        self.assertEqual(self.staff.get("/api/banking/accounts").status_code, 403)
        # 記帳代行は取込・照合ができる。税理士は閲覧のみ
        bk = make_user("bk_bank")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=bk, role="bookkeeper")
        self.assertEqual(client_for(bk, self.fx.store).get("/api/banking/accounts").status_code, 200)
        adv = make_user("adv_bank")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=adv, role="tax_advisor")
        c = client_for(adv, self.fx.store)
        self.assertEqual(c.get("/api/banking/accounts").status_code, 200)
        self.assertEqual(c.post("/api/banking/accounts", {"name": "y"}, format="json", **key()).status_code, 403)

    def test_other_company_cannot_see(self):
        b = standard_fixture("sb", company_code="cb")
        make_taxpayer(b, name="B", entity_type="sole_proprietor")
        ob = client_for(b.users["owner"], b.store)
        self.assertEqual(ob.get("/api/banking/accounts").json(), [])
        self.assertEqual(ob.patch(f"/api/banking/accounts/{self.acc.id}", {"name": "乗っ取り"}, format="json").status_code, 404)
        imp = self._import(self._rows((self.today, "入金", "", "1,000", "1,000")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        self.assertEqual(ob.get(f"/api/banking/transactions/{tx.id}/candidates").status_code, 404)
        self.assertEqual(ob.post(f"/api/banking/imports/{imp['id']}/rollback", {}, format="json", **key()).status_code, 404)

    def test_preview_then_import_commit_and_duplicate_file(self):
        rows = self._rows((self.today - timedelta(days=2), "振込 カ）サケヤ", "33,000", "", "967,000"), (self.today - timedelta(days=1), "入金", "", "50,000", "1,017,000"))
        r = self.owner.post("/api/banking/imports/preview", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows))})
        self.assertEqual(r.status_code, 200, r.content)
        p = r.json()
        self.assertEqual(p["encoding"], "cp932")
        self.assertTrue(p["guessed"])
        self.assertEqual(p["rows_parsed"], 2)
        self.assertEqual(p["balance_check"], "ok")
        self.assertEqual(BankTransaction.objects.count(), 0)  # プレビューは保存しない
        imp = self._import(rows, commit=False)
        self.assertEqual(imp["status"], "parsed")
        self.assertEqual(imp["rows_imported"], 2)
        self.assertEqual(BankTransaction.objects.filter(statement_import_id=imp["id"]).count(), 2)
        # 確定前の明細は一覧に出ない
        self.assertEqual(self.owner.get("/api/banking/transactions").json()["total"], 0)
        c = self.owner.post(f"/api/banking/imports/{imp['id']}/commit", {}, format="json", **key())
        self.assertEqual(c.json()["status"], "committed")
        self.assertEqual(self.owner.get("/api/banking/transactions").json()["total"], 2)
        # 同じファイルは 409
        r = self.owner.post("/api/banking/imports", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows))}, **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "duplicate_file")
        # プロファイルが保存され、2 回目は推測ではなく適用される
        from apps.banking.models import BankImportProfile

        self.assertEqual(BankImportProfile.objects.filter(bank_account=self.acc).count(), 1)
        rows2 = self._rows((self.today, "入金", "", "1,000", "1,018,000"))
        p2 = self.owner.post("/api/banking/imports/preview", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows2))}).json()
        self.assertFalse(p2["guessed"])
        self.assertIsNotNone(p2["profile_id"])

    def test_overlapping_rows_skipped_and_balance_mismatch(self):
        d1, d2, d3 = self.today - timedelta(days=3), self.today - timedelta(days=2), self.today - timedelta(days=1)
        self._import(self._rows((d1, "A", "", "1,000", "11,000"), (d2, "B", "500", "", "10,500")))
        # 2 回目: d2 の行が重複、d3 が新規。前回の最終残高 10,500 とつながる
        imp = self._import(self._rows((d2, "B", "500", "", "10,500"), (d3, "C", "", "2,000", "12,500")), name="m2.csv")
        self.assertEqual(imp["rows_duplicate"], 1)
        self.assertEqual(imp["rows_imported"], 1)
        self.assertEqual(imp["balance_check"], "ok")
        self.assertEqual(BankTransaction.objects.filter(bank_account=self.acc).count(), 3)
        # 3 回目: 開始残高が合わない
        imp = self._import(self._rows((self.today, "D", "", "100", "99,999")), name="m3.csv")
        self.assertEqual(imp["balance_check"], "mismatch")
        # 同じ日・同じ金額・同じ摘要が 2 行あるファイルは両方取り込む（連番でハッシュが違う）
        imp = self._import(self._rows((self.today, "同額", "300", "", ""), (self.today, "同額", "300", "", "")), name="m4.csv")
        self.assertEqual(imp["rows_imported"], 2)

    def test_row_limit_rejected_on_import(self):
        rows = [KYOSHIN_HEADER] + [["2026/06/01", f"行{i}", "1", "", "", ""] for i in range(5100)]
        r = self.owner.post("/api/banking/imports/preview", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows))})
        self.assertEqual(r.status_code, 400)

    def test_rollback_deletes_transactions_but_not_after_confirm(self):
        imp = self._import(self._rows((self.today, "入金", "", "1,000", "1,000")))
        r = self.owner.post(f"/api/banking/imports/{imp['id']}/rollback", {"reason": "やり直し"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(BankTransaction.objects.count(), 0)
        # 取り消したファイルは再取込できる
        imp2 = self._import(self._rows((self.today, "入金", "", "1,000", "1,000")), name="again.csv")
        self.assertEqual(imp2["status"], "committed")

    def _card_sales(self, amount="30000"):
        """カードで会計（手入力）。"""
        r = self.manager.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"course_id": self.fx.hourly.id}]}, format="json", **key())
        vid = r.json()["id"]
        # ボトル（30,000円）を注文して合計を増やす
        b = self.manager.post(f"/api/visits/{vid}/lines/batch", {"items": [{"kind": "product", "product_id": self.fx.products["champagne"].id, "quantity": 1}]}, format="json", **key())
        self.assertEqual(b.status_code, 201, b.content)
        q = self.manager.post(f"/api/visits/{vid}/quote", {}, format="json").json()
        r = self.manager.post(f"/api/visits/{vid}/checkouts", {"payments": [{"method": "card", "amount": q["total"]}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return Decimal(q["total"])

    def _close_day_and_build(self):
        s = self.manager.get("/api/cash/current").json()["day"]["summary"]
        r = self.manager.post("/api/cash/close", {"counted_cash": s["theoretical_cash"], "next_day_float": "0"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        r = self.owner.post("/api/accounting/journals/build", {"from": str(self.today - timedelta(days=1)), "to": str(self.today + timedelta(days=5))}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_card_settlement_candidate_confirm_journal_and_bank_book(self):
        total = self._card_sales()
        self._close_day_and_build()  # 日次仕訳（売掛金（カード）の発生）を作る
        fee = (total * Decimal("0.0325")).quantize(Decimal("1"))
        deposit = total - fee
        dep_date = self.today + timedelta(days=3)
        imp = self._import(self._rows((dep_date, "振込 カ）ペイメントサービス", "", f"{deposit:,.0f}", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        self.assertEqual(tx.status, "suggested")
        cands = self.owner.get(f"/api/banking/transactions/{tx.id}/candidates").json()["candidates"]
        card = [c for c in cands if c["match_type"] == "card_settlement"]
        self.assertTrue(card, cands)
        c = card[0]
        self.assertEqual(Decimal(c["amount"]), total)
        self.assertEqual(Decimal(c["fee_amount"]), fee)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/reconcile", {"allocations": [c]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["transaction"]["status"], "matched")
        cs = CardSettlement.objects.get(taxpayer=self.tp)
        self.assertEqual((cs.sales_amount, cs.fee_amount, cs.deposit_amount, cs.bank_account_ref), (total, fee, deposit, self.acc.id))
        self.assertEqual(cs.deposit_date, dep_date)
        j = Journal.objects.get(sync_key=f"{self.tp.id}:card_settlement:{cs.id}")
        lines = {(l.side, l.account.key): l for l in JournalLine.objects.filter(journal=j).select_related("account")}
        self.assertEqual(lines[("debit", "bank")].amount, deposit)
        self.assertEqual(lines[("debit", "bank")].bank_account_ref, self.acc.id)
        self.assertEqual(lines[("debit", "card_fee")].amount, fee)
        self.assertEqual(lines[("debit", "card_fee")].tax_category, "non_taxable")
        self.assertEqual(lines[("credit", "card_receivable")].amount, total)
        book = ledgers.bank_book(self.tp, dep_date, dep_date, bank_account_ref=self.acc.id)
        self.assertEqual(Decimal(book["in_total"]), deposit)
        other_book = ledgers.bank_book(self.tp, dep_date, dep_date, bank_account_ref=self.acc.id + 999)
        self.assertEqual(Decimal(other_book["in_total"]), Decimal(0))
        # 売掛帳で入金が消し込まれている
        rec = ledgers.receivables(self.tp, self.today, dep_date)
        self.assertEqual(Decimal(rec["card"]["closing"]), Decimal(0))
        # 解除 → CardSettlement 取消・仕訳 void・明細は未照合に戻る
        rid = r.json()["reconciliations"][0]["id"]
        self.assertEqual(self.owner.post(f"/api/banking/reconciliations/{rid}/void", {}, format="json", **key()).status_code, 400)
        v = self.owner.post(f"/api/banking/reconciliations/{rid}/void", {"reason": "別の入金だった"}, format="json", **key())
        self.assertEqual(v.status_code, 200, v.content)
        cs.refresh_from_db()
        j.refresh_from_db()
        tx.refresh_from_db()
        self.assertTrue(cs.is_voided)
        self.assertTrue(j.is_void)
        self.assertIn(tx.status, ("unmatched", "suggested"))

    def test_expense_match_and_double_reconcile_rejected(self):
        r = self.owner.post("/api/accounting/expenses", {"date": str(self.today), "amount": "33000", "account_key": "rent", "paid_from": "bank", "counterparty": "ノクシア不動産"},
                            format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        eid = r.json()["id"]
        imp = self._import(self._rows((self.today, "振込 ノクシアフドウサン", "33,000", "", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        cands = self.owner.get(f"/api/banking/transactions/{tx.id}/candidates").json()["candidates"]
        c = next(c for c in cands if c["match_type"] == "expense")
        self.assertEqual(c["target_id"], eid)
        self.assertGreater(c["score"], 0.85)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/reconcile", {"allocations": [c]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["transaction"]["status"], "matched")
        # 同じ経費を別の明細に照合できない
        imp2 = self._import(self._rows((self.today, "振込 ノクシアフドウサン 2回目", "33,000", "", "")), name="dup.csv")
        tx2 = BankTransaction.objects.get(statement_import_id=imp2["id"])
        r = self.owner.post(f"/api/banking/transactions/{tx2.id}/reconcile", {"allocations": [{"match_type": "expense", "target_id": eid, "amount": "33000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        # 配分が明細の金額を超える
        r = self.owner.post(f"/api/banking/transactions/{tx2.id}/reconcile", {"allocations": [{"match_type": "expense", "target_id": eid + 999, "amount": "1"}]}, format="json", **key())
        self.assertEqual(r.status_code, 404)
        # 店長・スタッフは照合できない
        self.assertEqual(self.manager.post(f"/api/banking/transactions/{tx2.id}/ignore", {"reason": "x"}, format="json", **key()).status_code, 403)

    def test_bank_deposit_match(self):
        day = self.fx.store.business_days.get()
        dep = BankDeposit.objects.create(store=self.fx.store, business_day=day, amount=Decimal("80000"), deposited_at=timezone.now())
        imp = self._import(self._rows((self.today + timedelta(days=1), "ATM入金", "", "80,000", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        cands = self.owner.get(f"/api/banking/transactions/{tx.id}/candidates").json()["candidates"]
        c = next(c for c in cands if c["match_type"] == "bank_deposit")
        self.assertEqual(c["target_id"], dep.id)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/reconcile", {"allocations": [c]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(BankReconciliation.objects.get(bank_deposit=dep).status, "confirmed")
        # 預入は仕訳を増やさない（既存の bank_deposit 仕訳だけ）
        self.assertFalse(Journal.objects.filter(source_type="bank_transfer").exists())

    def test_aggregate_match(self):
        for amt in ("12000", "8000"):
            self.owner.post("/api/accounting/expenses", {"date": str(self.today), "amount": amt, "account_key": "supplies", "paid_from": "bank", "counterparty": "通販"}, format="json", **key())
        imp = self._import(self._rows((self.today + timedelta(days=1), "カード引落 ツウハン", "20,000", "", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        cands = self.owner.get(f"/api/banking/transactions/{tx.id}/candidates").json()["candidates"]
        agg = next(c for c in cands if c["rule"].startswith("aggregate"))
        self.assertEqual(len(agg["items"]), 2)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/reconcile", {"allocations": [agg]}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["transaction"]["status"], "matched")
        self.assertEqual(len(r.json()["reconciliations"]), 2)

    def test_partial_allocation(self):
        self.owner.post("/api/accounting/expenses", {"date": str(self.today), "amount": "1000", "account_key": "supplies", "paid_from": "bank"}, format="json", **key())
        from apps.accounting.models import Expense

        e = Expense.objects.get()
        imp = self._import(self._rows((self.today, "出金", "3,000", "", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/reconcile", {"allocations": [{"match_type": "expense", "target_id": e.id, "amount": "1000"}]}, format="json", **key())
        self.assertEqual(r.json()["transaction"]["status"], "partially_matched")

    def test_ignore_with_reason_and_undo(self):
        imp = self._import(self._rows((self.today, "スーパー（私用）", "2,500", "", "")))
        tx = BankTransaction.objects.get(statement_import_id=imp["id"])
        self.assertEqual(self.owner.post(f"/api/banking/transactions/{tx.id}/ignore", {}, format="json", **key()).status_code, 400)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/ignore", {"reason": "家事費"}, format="json", **key())
        self.assertEqual(r.json()["status"], "ignored")
        self.assertEqual(self.owner.get("/api/banking/transactions?status=open").json()["total"], 0)
        r = self.owner.post(f"/api/banking/transactions/{tx.id}/ignore", {"undo": True}, format="json", **key())
        self.assertEqual(r.json()["status"], "unmatched")

    def test_transfer_between_own_accounts_and_void(self):
        r = self.owner.post("/api/banking/accounts", {"name": "PayPay銀行"}, format="json", **key())
        acc2 = BankAccount.objects.get(id=r.json()["id"])
        imp1 = self._import(self._rows((self.today, "振込 ジブンノコウザ", "50,000", "", "")))
        r = self.owner.post("/api/banking/imports", {"bank_account": acc2.id, "file": upload(csv_bytes(self._rows((self.today, "振込 入金", "", "50,000", ""))), "pp.csv")}, **key())
        self.owner.post(f"/api/banking/imports/{r.json()['id']}/commit", {}, format="json", **key())
        tx1 = BankTransaction.objects.get(statement_import_id=imp1["id"])
        tx2 = BankTransaction.objects.get(bank_account=acc2)
        cands = self.owner.get(f"/api/banking/transactions/{tx1.id}/candidates").json()["candidates"]
        self.assertTrue(any(c["match_type"] == "transfer" and c["target_id"] == tx2.id for c in cands), cands)
        r = self.owner.post(f"/api/banking/transactions/{tx1.id}/transfer", {"counter_transaction_id": tx2.id}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        tx1.refresh_from_db()
        tx2.refresh_from_db()
        self.assertEqual((tx1.status, tx2.status), ("transfer", "transfer"))
        j = Journal.objects.get(source_type="bank_transfer")
        refs = {(l.side, l.bank_account_ref) for l in JournalLine.objects.filter(journal=j)}
        self.assertEqual(refs, {("debit", acc2.id), ("credit", self.acc.id)})
        # 預金出納帳: 口座別に入金・出金が分かれ、合計では相殺
        self.assertEqual(Decimal(ledgers.bank_book(self.tp, self.today, self.today, bank_account_ref=acc2.id)["in_total"]), Decimal("50000"))
        self.assertEqual(Decimal(ledgers.bank_book(self.tp, self.today, self.today, bank_account_ref=self.acc.id)["out_total"]), Decimal("50000"))
        both = ledgers.bank_book(self.tp, self.today, self.today)
        self.assertEqual(Decimal(both["closing"]), Decimal(0))
        # 損益に影響しない
        pl = self.owner.get(f"/api/accounting/pl?from={self.today}&to={self.today}").json()
        self.assertEqual(Decimal(pl["total_expense"]), Decimal(0))
        # 解除で両方戻る
        rid = r.json()["reconciliations"][0]["id"]
        v = self.owner.post(f"/api/banking/reconciliations/{rid}/void", {"reason": "間違い"}, format="json", **key())
        self.assertEqual(v.status_code, 200, v.content)
        tx1.refresh_from_db()
        tx2.refresh_from_db()
        j.refresh_from_db()
        self.assertTrue(j.is_void)
        self.assertIn(tx1.status, ("unmatched", "suggested"))  # 候補が残っていれば「候補あり」
        self.assertIn(tx2.status, ("unmatched", "suggested"))
        self.assertFalse(BankReconciliation.objects.filter(status="confirmed").exists())

    def test_transactions_filters_pagination_and_summary(self):
        rows = [(self.today - timedelta(days=i), f"明細{i}", "100", "", "") for i in range(7)] + [(self.today, "入金", "", "9,000", "")]
        self._import(self._rows(*rows))
        r = self.owner.get("/api/banking/transactions?page=1&page_size=5").json()
        self.assertEqual((r["total"], len(r["items"]), r["page_size"]), (8, 5, 5))
        self.assertEqual(self.owner.get("/api/banking/transactions?direction=in").json()["total"], 1)
        self.assertEqual(self.owner.get("/api/banking/transactions?q=明細3").json()["total"], 1)
        s = self.owner.get(f"/api/banking/summary?from={self.today - timedelta(days=10)}&to={self.today}").json()
        self.assertEqual(s["unmatched_count"], 8)
        self.assertEqual(Decimal(s["unmatched_in"]), Decimal("9000"))
        self.assertEqual(s["accounts"][0]["id"], self.acc.id)
        r = self.owner.post(f"/api/banking/scan?from={self.today - timedelta(days=10)}&to={self.today}", {}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["scanned"], 8)

    def test_profile_crud_and_manual_column_map(self):
        rows = [["日", "メモ", "出", "入"], ["2026/06/01", "A", "1,000", ""]]
        # 見出し語が特殊で推測できない → 列を指定して取込
        r = self.owner.post("/api/banking/imports/preview", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows))})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.owner.post("/api/banking/imports", {"bank_account": self.acc.id, "file": upload(csv_bytes(rows), "odd.csv"),
                                                     "column_map": '{"date": 0, "description": 1, "withdrawal": 2, "deposit": 3}', "header_rows": "1"}, **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["rows_imported"], 1)
        p = self.owner.get("/api/banking/profiles").json()
        self.assertTrue(p)
        pid = p[0]["id"]
        r = self.owner.patch(f"/api/banking/profiles/{pid}", {"column_map": {"date": 0, "description": 1, "withdrawal": 2, "deposit": 3, "balance": None}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = self.owner.patch(f"/api/banking/profiles/{pid}", {"column_map": {"description": 1}}, format="json")
        self.assertEqual(r.status_code, 400)
