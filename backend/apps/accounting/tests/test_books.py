"""Phase 1 会計基盤: 勘定科目・仕訳行・帳簿・年度ロック・手入力仕訳・現金取消の二重減算修正・暗号化。"""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounting import books, journals, ledgers
from apps.accounting.models import Account, FiscalYear, FreeeConnection, Journal, JournalLine
from apps.cash.models import CashMovement
from apps.cash.services import day_summary
from apps.core import crypto, jobs
from apps.core.models import AuditLog, BackgroundJob
from apps.core.testing import add_user, client_for, make_user, standard_fixture

from .helpers import contract, key, make_taxpayer, pay, prev_month_first, work

MEDIA = tempfile.mkdtemp(prefix="noxia-test-books-")


def tearDownModule():
    shutil.rmtree(MEDIA, ignore_errors=True)


def close_day(client):
    s = client.get("/api/cash/current").json()["day"]["summary"]
    r = client.post("/api/cash/close", {"counted_cash": s["theoretical_cash"], "next_day_float": "0"}, format="json", **key())
    assert r.status_code == 200, r.content
    return r.json()


def checkout_cash(client, fx, amount="2500"):
    """1時間コースで入店→現金会計。会計 ID を返す。"""
    r = client.post("/api/visits", {"table_id": fx.tables[0].id, "entries": [{"course_id": fx.hourly.id}]}, format="json", **key())
    assert r.status_code == 201, r.content
    vid = r.json()["id"]
    q = client.post(f"/api/visits/{vid}/quote", {}, format="json").json()
    total = q["total"]
    r = client.post(f"/api/visits/{vid}/checkouts", {"payments": [{"method": "cash", "amount": total, "tendered": total}]}, format="json", **key())
    assert r.status_code == 201, r.content
    return r.json()["id"], Decimal(total), vid


@override_settings(MEDIA_ROOT=MEDIA)
class BooksTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor")
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.d = prev_month_first()
        self.today = timezone.localdate()

    def build(self):
        r = self.owner.post("/api/accounting/journals/build", {"from": str(self.d), "to": str(self.today)}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def test_standard_accounts_seeded_and_names_follow_entity_type(self):
        accounts = books.ensure_accounts(self.tp)
        self.assertIn("cash", accounts)
        self.assertEqual(accounts["owner_in"].name, "事業主借")
        self.assertEqual(accounts["sales"].statement_line, "sales")
        self.assertEqual(accounts["card_fee"].default_tax, "non_taxable")
        for k in journals.ACCOUNTS:
            self.assertIn(k, accounts, k)
        r = self.owner.get("/api/accounting/accounts")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.json()["accounts"]), 40)

    def test_journal_lines_are_normalized_and_balanced(self):
        cast = self.fx.employees["cast1"]
        contract(self.fx, cast, period="daily")
        work(self.fx.store, cast, self.d, hours=8, back=10000)
        pay(self.manager, cast, self.d)
        self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "1100", "reason": "氷", "account_key": "supplies"}, format="json", **key())
        close_day(self.manager)
        self.build()
        j = Journal.objects.get(source_type="daily")
        lines = list(JournalLine.objects.filter(journal=j))
        self.assertEqual(len(lines), len(j.lines))
        dr = sum(l.amount for l in lines if l.side == "debit")
        cr = sum(l.amount for l in lines if l.side == "credit")
        self.assertEqual(dr, cr)
        # 現金経費は指定した科目（消耗品費）になり、雑費の警告は出ない
        self.assertTrue(any(l.account.key == "supplies" and l.amount == Decimal("1100") for l in lines))
        self.assertFalse(any("勘定科目が未設定" in w for w in j.warnings))
        self.assertIsNotNone(j.fiscal_year)
        self.assertEqual(j.fiscal_year.year, self.d.year)
        # 行の店舗は仕訳の店舗
        self.assertTrue(all(l.store_id == self.fx.store.id for l in lines))
        # スタッフ支払行に従業員が付く
        self.assertTrue(any(l.employee_id == cast.id for l in lines if l.account.key == "cast_fee"))

    def test_ledgers_cash_book_and_expense_book(self):
        self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "3000", "reason": "レモン", "account_key": "purchases"}, format="json", **key())
        self.owner.post("/api/accounting/owner-transfers", {"date": str(self.today), "direction": "in", "amount": "50000", "method": "cash_register"}, format="json", **key())
        close_day(self.manager)
        self.build()
        cb = self.owner.get(f"/api/accounting/ledgers/cash?from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(cb["in_total"]), Decimal("50000"))
        self.assertEqual(Decimal(cb["out_total"]), Decimal("3000"))
        self.assertEqual(Decimal(cb["closing"]), Decimal("47000"))
        eb = self.owner.get(f"/api/accounting/ledgers/purchases?from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(eb["total"]), Decimal("3000"))
        gl = self.owner.get(f"/api/accounting/ledgers/general?account=cash&from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(gl["closing"]), Decimal("47000"))
        tb = self.owner.get(f"/api/accounting/ledgers/trial_balance?from={self.d}&to={self.today}").json()
        total_dr = sum(Decimal(r["debit"]) for r in tb["rows"])
        total_cr = sum(Decimal(r["credit"]) for r in tb["rows"])
        self.assertEqual(total_dr, total_cr)
        # 事業主借は損益に入らない
        pl = self.owner.get(f"/api/accounting/pl?from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(pl["total_income"]), Decimal(0))
        self.assertEqual(Decimal(pl["total_expense"]), Decimal("3000"))
        self.assertEqual(pl["by_statement_line"][0]["line"], "purchases")
        # CSV / XLSX
        self.assertEqual(self.owner.get(f"/api/accounting/ledgers/cash.csv?from={self.d}&to={self.today}").status_code, 200)
        x = self.owner.get(f"/api/accounting/ledgers/cash.xlsx?from={self.d}&to={self.today}")
        self.assertEqual(x.status_code, 200)
        self.assertTrue(x.content[:2] == b"PK")
        self.assertEqual(self.owner.get(f"/api/accounting/ledgers.xlsx?from={self.d}&to={self.today}").status_code, 200)

    def test_sales_book_matches_pos(self):
        cid, total, _vid = checkout_cash(self.manager, self.fx)
        close_day(self.manager)
        self.build()
        sb = self.owner.get(f"/api/accounting/ledgers/sales?from={self.d}&to={self.today}").json()
        row = sb["rows"][0]
        self.assertEqual(Decimal(row["sales_total"]), total)
        self.assertEqual(Decimal(row["cash"]), total)
        self.assertEqual(Decimal(row["journal_sales"]), total)

    def test_manual_journal_requires_balance_and_year_lock(self):
        r = self.owner.post("/api/accounting/journals/manual", {"issue_date": str(self.today), "description": "テスト", "lines": [
            {"side": "debit", "key": "supplies", "amount": "1000"}, {"side": "credit", "key": "cash", "amount": "900"}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "unbalanced")
        r = self.owner.post("/api/accounting/journals/manual", {"issue_date": str(self.today), "description": "備品", "lines": [
            {"side": "debit", "key": "supplies", "amount": "1000", "tax": "purchase_10"}, {"side": "credit", "key": "owner_in", "amount": "1000"}]},
            format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        jid = r.json()["id"]
        j = Journal.objects.get(id=jid)
        self.assertEqual(j.entry_kind, "manual")
        self.assertEqual(JournalLine.objects.filter(journal=j).count(), 2)
        # 更新 → revision
        r = self.owner.patch(f"/api/accounting/journals/{jid}", {"description": "備品（訂正）"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["revision"], 2)
        # 年度を締める（オーナー・明示の再認証）→ 手入力・取消・自動の作り直しができない
        year = self.today.year
        r = self.owner.post(f"/api/accounting/fiscal-years/{year}/close", {"reason": "テスト", "force": True}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["status"], "closed")
        r = self.owner.post("/api/accounting/journals/manual", {"issue_date": str(self.today), "description": "x", "lines": [
            {"side": "debit", "key": "supplies", "amount": "10"}, {"side": "credit", "key": "cash", "amount": "10"}]}, format="json", **key())
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "year_locked")
        r = self.owner.post(f"/api/accounting/journals/{jid}/void", {"reason": "x"}, format="json")
        self.assertEqual(r.status_code, 409)
        # 締め済み年度で元データが増えても仕訳は作られず、drift に記録される
        self.owner.post("/api/accounting/expenses", {"date": str(self.today), "amount": "5500", "account_key": "supplies", "paid_from": "bank"}, format="json", **key())
        res = self.build()
        self.assertFalse(Journal.objects.filter(source_type="expense").exists())
        fy = FiscalYear.objects.get(taxpayer=self.tp, year=year)
        self.assertEqual(len(fy.drift_warnings), 1)
        self.assertGreaterEqual(res["drift_warnings"], 1)
        # 再開には理由が必要。再開後は作れる
        self.assertEqual(self.owner.post(f"/api/accounting/fiscal-years/{year}/reopen", {}, format="json", **key()).status_code, 400)
        r = self.owner.post(f"/api/accounting/fiscal-years/{year}/reopen", {"reason": "経費の登録漏れ"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.build()
        self.assertTrue(Journal.objects.filter(source_type="expense", is_void=False).exists())
        fy.refresh_from_db()
        self.assertEqual(fy.reopen_count, 1)

    def test_year_close_requires_owner_and_explicit_reauth(self):
        year = self.today.year
        # 会社管理者は年度を締められない
        admin = make_user("admin_y")
        from apps.core.models import CompanyMembership

        CompanyMembership.objects.create(user=admin, company=self.fx.company, role="admin")
        r = client_for(admin, self.fx.store).post(f"/api/accounting/fiscal-years/{year}/close", {"force": True}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        # ログイン直後の自動再認証（reauth_source=login）では足りない
        c = client_for(self.fx.users["owner"], self.fx.store, reauth=False)
        s = c.session
        s["reauth_until"] = timezone.now().timestamp() + 3600
        s["reauth_source"] = "login"
        s.save()
        r = c.post(f"/api/accounting/fiscal-years/{year}/close", {"force": True}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")
        # 明示の再認証なら可
        r = self.owner.post(f"/api/accounting/fiscal-years/{year}/close", {"force": True}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)

    def test_cash_void_same_day_does_not_double_count(self):
        """既存バグの修正: 同じ営業日に現金の会計を取り消すと理論現金が二重に減っていた → 0 に戻る"""
        day = self.manager.get("/api/cash/current").json()["day"]
        base = Decimal(day["summary"]["theoretical_cash"])
        cid, total, vid = checkout_cash(self.manager, self.fx)
        s = self.manager.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(s["theoretical_cash"]), base + total)
        r = self.manager.post(f"/api/checkouts/{cid}/void", {"reason": "テスト取消"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        s = self.manager.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(s["theoretical_cash"]), base)
        self.assertEqual(Decimal(s["cash_sales"]), Decimal(0))
        self.assertEqual(Decimal(s["cash_refunds"]), total)
        # 取消で伝票が営業中に戻るので、伝票を取り消してから締める。仕訳でも売上と返金が相殺して現金は変わらない
        r = self.manager.post(f"/api/visits/{vid}/cancel", {"reason": "テスト"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        close_day(self.manager)
        self.build()
        gl = self.owner.get(f"/api/accounting/ledgers/general?account=cash&from={self.d}&to={self.today}").json()
        self.assertEqual(Decimal(gl["closing"]), base)

    def test_manager_cannot_use_books_apis_staff_cannot_see_ledgers(self):
        self.assertEqual(self.manager.get(f"/api/accounting/ledgers/cash?from={self.d}&to={self.today}").status_code, 403)
        staff = client_for(self.fx.users["staff"], self.fx.store)
        self.assertEqual(staff.get("/api/accounting/accounts").status_code, 403)
        # 記帳代行は帳簿を見られ、手入力仕訳もできるが年度は締められない
        bk = make_user("bk_books")
        from apps.accounting.models import AccountingAccessGrant

        AccountingAccessGrant.objects.create(company=self.fx.company, user=bk, role="bookkeeper")
        c = client_for(bk, self.fx.store)
        self.assertEqual(c.get(f"/api/accounting/ledgers/cash?from={self.d}&to={self.today}").status_code, 200)
        r = c.post("/api/accounting/journals/manual", {"issue_date": str(self.today), "description": "y", "lines": [
            {"side": "debit", "key": "supplies", "amount": "10"}, {"side": "credit", "key": "cash", "amount": "10"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(c.post(f"/api/accounting/fiscal-years/{self.today.year}/close", {"force": True}, format="json", **key()).status_code, 403)


class CryptoAndInfraTests(TestCase):
    def test_encrypt_decrypt_roundtrip_and_legacy_plaintext(self):
        token = crypto.encrypt("secret-token")
        self.assertTrue(token.startswith("v1:"))
        self.assertNotIn("secret", token)
        self.assertEqual(crypto.decrypt(token), "secret-token")
        self.assertEqual(crypto.decrypt("plain-old-value"), "plain-old-value")  # 移行前の平文はそのまま読む
        data = crypto.encrypt_bytes(b"\x00\x01binary")
        self.assertTrue(crypto.is_encrypted_bytes(data))
        self.assertEqual(crypto.decrypt_bytes(data), b"\x00\x01binary")

    def test_freee_tokens_stored_encrypted(self):
        fx = standard_fixture()
        tp = make_taxpayer(fx)
        conn = FreeeConnection.objects.create(taxpayer=tp, access_token="abc123", refresh_token="r-456")
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT access_token, refresh_token FROM accounting_freeeconnection WHERE id = %s", [conn.id])
            raw_a, raw_r = cur.fetchone()
        self.assertTrue(raw_a.startswith("v1:") and "abc123" not in raw_a)
        self.assertTrue(raw_r.startswith("v1:"))
        conn.refresh_from_db()
        self.assertEqual(conn.access_token, "abc123")

    def test_audit_log_is_append_only(self):
        log = AuditLog.objects.create(action="test.x")
        log.action = "changed"
        with self.assertRaises(ValueError):
            log.save()
        with self.assertRaises(ValueError):
            log.delete()

    def test_background_jobs_run_and_retry(self):
        calls = []

        @jobs.register("test.ok")
        def ok(job):
            calls.append(job.payload)
            return {"done": True}

        @jobs.register("test.retry")
        def retry(job):
            raise jobs.RetryableError("一時的")

        jobs.enqueue("test.ok", {"n": 1})
        j2 = jobs.enqueue("test.retry", {}, max_attempts=2)
        n = jobs.run_pending()
        self.assertEqual(n, 2)
        self.assertEqual(calls, [{"n": 1}])
        j2.refresh_from_db()
        self.assertEqual(j2.status, BackgroundJob.QUEUED)
        self.assertEqual(j2.attempts, 1)
        j2.run_after = timezone.now() - timedelta(seconds=1)
        j2.save()
        jobs.run_pending()
        j2.refresh_from_db()
        self.assertEqual(j2.status, BackgroundJob.FAILED)

    def test_new_caps_by_role(self):
        from apps.core import permissions as p

        self.assertIn(p.CAP_EXPENSES_SUBMIT, p.STAFF_CAPS)
        self.assertNotIn(p.CAP_EXPENSES_APPROVE, p.MANAGER_CAPS)
        self.assertIn(p.CAP_EXPENSES_APPROVE, p.BOOKKEEPER_CAPS)
        self.assertNotIn(p.CAP_TAX_RETURN_EDIT, p.ADMIN_CAPS)
        self.assertNotIn(p.CAP_TAX_RETURN_EDIT, p.OPERATOR_CAPS)
        self.assertNotIn(p.CAP_YEAREND_CONFIRM, p.BOOKKEEPER_CAPS)
        self.assertIn(p.CAP_TAX_RETURN_EDIT, p.OWNER_CAPS)
        self.assertNotIn(p.CAP_TAX_CONFIG_MANAGE, p.OWNER_CAPS)
        # 店長の承認権限は店舗設定で付く
        fx = standard_fixture()
        from apps.core.permissions import resolve_store_access

        self.assertNotIn(p.CAP_EXPENSES_APPROVE, resolve_store_access(fx.users["manager"], fx.store.id).caps)
        fx.settings.manager_can_approve_expenses = True
        fx.settings.save()
        fx.store.refresh_from_db()
        self.assertIn(p.CAP_EXPENSES_APPROVE, resolve_store_access(fx.users["manager"], fx.store.id).caps)
