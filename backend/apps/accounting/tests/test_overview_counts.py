"""会計ダッシュボード用: overview の counts / month_pl。"""
from decimal import Decimal

from django.test import TestCase

from apps.core.testing import client_for, standard_fixture

from .helpers import key, make_taxpayer


class OverviewCountsTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor")
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)

    def test_counts_and_month_pl_present(self):
        # 科目の付いていない現金経費 1 件、営業日は開いている
        r = self.manager.post("/api/cash/movements", {"kind": "expense", "amount": "1100", "reason": "氷"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = self.owner.get("/api/accounting/overview").json()
        c = d["counts"]
        self.assertEqual(c["open_days"], 1)
        self.assertEqual(c["unassigned_cash_expenses"], 1)
        self.assertEqual(c["pending_receipts"], 0)
        self.assertEqual(c["unmatched_bank"], 0)
        self.assertEqual(c["suspense_journals"], 0)
        self.assertIsInstance(c["fiscal_years"], list)
        self.assertIsNotNone(d["month_pl"])
        self.assertEqual(Decimal(d["month_pl"]["total_income"]), Decimal(0))

    def test_counts_empty_without_taxpayer(self):
        fx = standard_fixture("s9", company_code="c9")
        owner = client_for(fx.users["owner"], fx.store)
        d = owner.get("/api/accounting/overview").json()
        self.assertEqual(d["counts"], {})
        self.assertIsNone(d["month_pl"])
