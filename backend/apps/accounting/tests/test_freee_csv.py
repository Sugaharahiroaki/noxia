"""freee 取込用 CSV（他社会計ソフトインポート向け）と、事業者の申告方法（freee）の出し分け。"""
import csv
import io
from datetime import date

from django.test import TestCase

from apps.accounting import books
from apps.accounting.models import Journal, TaxPayer
from apps.core.testing import client_for, standard_fixture

from .helpers import key, make_taxpayer


class FreeeCsvTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor", withholding=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.bookkeeper = client_for(self.fx.users["owner"], self.fx.store)  # 承認権限の確認は caps で行うので同じユーザーで十分
        u = self.fx.users["owner"]
        # 借方 2 行・貸方 1 行の複合仕訳（8%/10% の仕入）と、単純な売上仕訳
        self.j1 = books.create_manual_journal(
            self.tp, u, issue_date=date(2026, 6, 10), description="仕入 氷屋",
            lines=[{"side": "debit", "key": "purchases", "amount": "1080", "tax": "purchase_8"},
                   {"side": "debit", "key": "purchases", "amount": "2200", "tax": "purchase_10"},
                   {"side": "credit", "key": "cash", "amount": "3280"}],
            source_type="test", source_id="p1",
        )
        self.j2 = books.create_manual_journal(
            self.tp, u, issue_date=date(2026, 6, 15), description="売上",
            lines=[{"side": "debit", "key": "cash", "amount": "15000"}, {"side": "credit", "key": "sales", "amount": "15000", "tax": "sales_10"}],
            source_type="test", source_id="s1",
        )

    def _rows(self, res):
        text = res.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_csv_layout_matches_freee_import(self):
        res = self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30")
        self.assertEqual(res.status_code, 200, res.content)
        rows = self._rows(res)
        self.assertEqual(rows[0], ["伝票番号", "取引日", "借方勘定科目", "借方税区分", "借方金額", "貸方勘定科目", "貸方税区分", "貸方金額", "摘要", "NOXIA仕訳ID"])
        body = rows[1:]
        self.assertEqual(len(body), 3)  # 複合仕訳 2 行 ＋ 売上 1 行
        # 複合仕訳: 同じ伝票番号、2 行目は貸方が空
        self.assertEqual(body[0][0], body[1][0])
        self.assertEqual(body[0][1], "2026/06/10")
        self.assertEqual(body[0][2:5], ["仕入高", "課対仕入8%（軽）", "1080"])
        self.assertEqual(body[0][5:8], ["現金", "対象外", "3280"])
        self.assertEqual(body[1][2:5], ["仕入高", "課対仕入10%", "2200"])
        self.assertEqual(body[1][5:8], ["", "", ""])
        self.assertEqual(body[2][5:8], ["売上高", "課税売上10%", "15000"])
        self.assertEqual(body[2][9], self.j2.sync_key)
        self.assertEqual(res["X-Journal-Count"], "2")

    def test_only_new_and_marking(self):
        st = self.owner.get("/api/accounting/freee/csv-status?from=2026-06-01&to=2026-06-30").json()
        self.assertEqual((st["total"], st["new"], st["changed"]), (2, 2, 0))
        self.assertTrue(st["can_mark"])
        # 出力すると印が付き、未出力分は 0 件になる
        res = self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30&only_new=1")
        self.assertEqual(res["X-Journal-Count"], "2")
        st = self.owner.get("/api/accounting/freee/csv-status?from=2026-06-01&to=2026-06-30").json()
        self.assertEqual((st["total"], st["new"]), (2, 0))
        self.assertIsNotNone(st["last_exported_at"])
        res = self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30&only_new=1")
        self.assertEqual(res["X-Journal-Count"], "0")
        self.assertEqual(len(self._rows(res)), 1)  # ヘッダだけ
        # 内容が変わった仕訳（revision が進む）はもう一度「未出力」になる
        books.update_manual_journal(self.j2, self.fx.users["owner"], issue_date=date(2026, 6, 15), description="売上（訂正）",
                                    lines=[{"side": "debit", "key": "cash", "amount": "16000"}, {"side": "credit", "key": "sales", "amount": "16000", "tax": "sales_10"}])
        st = self.owner.get("/api/accounting/freee/csv-status?from=2026-06-01&to=2026-06-30").json()
        self.assertEqual((st["new"], st["changed"]), (1, 1))
        # mark=0 なら印を付けない
        res = self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30&only_new=1&mark=0")
        self.assertEqual(res["X-Journal-Count"], "1")
        st = self.owner.get("/api/accounting/freee/csv-status?from=2026-06-01&to=2026-06-30").json()
        self.assertEqual(st["new"], 1)
        # 全件出力（only_new なし）も印を付ける
        self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30")
        self.assertEqual(Journal.objects.filter(taxpayer=self.tp, freee_csv_exported_at__isnull=True).count(), 0)

    def test_sjis_encoding_option(self):
        res = self.owner.get("/api/accounting/freee.csv?from=2026-06-01&to=2026-06-30&encoding=sjis&mark=0")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Shift_JIS", res["Content-Type"])
        self.assertIn("伝票番号", res.content.decode("cp932"))

    def test_filing_method_setting_and_session_payload(self):
        me = self.owner.get("/api/auth/me").json()
        st = next(s for s in me["stores"] if s["id"] == self.fx.store.id)
        self.assertEqual(st["taxpayer"]["filing_method"], "")
        r = self.owner.patch(f"/api/accounting/taxpayers/{self.tp.id}", {"filing_method": "freee"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(TaxPayer.objects.get(pk=self.tp.pk).filing_method, "freee")
        me = self.owner.get("/api/auth/me").json()
        st = next(s for s in me["stores"] if s["id"] == self.fx.store.id)
        self.assertEqual(st["taxpayer"], {"id": self.tp.id, "name": "個人 夜子", "entity_type": "sole_proprietor", "filing_method": "freee"})
        r = self.owner.patch(f"/api/accounting/taxpayers/{self.tp.id}", {"filing_method": "paper"}, format="json", **key())
        self.assertEqual(r.status_code, 400)

    def test_manager_cannot_mark(self):
        manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        res = manager.get("/api/accounting/freee/csv-status?from=2026-06-01&to=2026-06-30")
        # 店長は経理閲覧の権限がないので 403
        self.assertEqual(res.status_code, 403)
