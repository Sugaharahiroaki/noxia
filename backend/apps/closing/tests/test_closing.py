"""Phase 4 年間決算: 減価償却（定額法・一括・少額・30万特例・任意償却）、棚卸、決算整理、年間損益、年度ロック、権限。"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounting import books, ledgers
from apps.accounting.models import AccountingAccessGrant, FiscalYear, Journal, JournalLine
from apps.accounting.tests.helpers import key, make_taxpayer
from apps.catalog.models import Product
from apps.closing import depreciation
from apps.closing.models import ClosingAdjustment, DepreciationEntry, FixedAsset, InventoryClosing
from apps.core.models import CompanyMembership
from apps.core.testing import client_for, make_user, standard_fixture
from apps.inventory.models import StockTake, StockTakeLine, Supplier, SupplierProduct

YEAR = 2026


def lines_of(j: Journal) -> dict:
    return {(ln.side, ln.account.key): ln.amount for ln in JournalLine.objects.filter(journal=j).select_related("account")}


class ClosingBase(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.tp = make_taxpayer(self.fx, name="個人 夜子", entity_type="sole_proprietor", withholding=False)
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.fy = books.get_fiscal_year(self.tp, YEAR, create=True)

    def asset(self, **kw):
        body = {"year": YEAR, "name": "冷蔵庫", "kind": "tangible", "category": "器具備品", "acquired_on": f"{YEAR}-01-10", "acquisition_cost": "600000",
                "business_use_percent": "100", "depreciation_method": "straight_line", "useful_life_years": 6, "paid_from": "none"}
        body.update(kw)
        r = self.owner.post("/api/closing/fixed-assets", body, format="json", **key())
        return r

    def compute(self, year=YEAR, voluntary=None, client=None):
        c = client or self.owner
        return c.post("/api/closing/depreciation/compute", {"year": year, "voluntary": voluntary or {}}, format="json", **key())


class DepreciationTests(ClosingBase):
    def test_rates_table_matches_official_rule(self):
        t = depreciation.rates_table()
        self.assertEqual(t["rates"]["6"], "0.167")
        self.assertEqual(t["rates"]["3"], "0.334")
        self.assertEqual(t["rates"]["10"], "0.100")
        self.assertIn("nta.go.jp", t["source_url"])
        self.assertEqual(depreciation.rate_for(27), Decimal("0.038"))

    def test_straight_line_full_year(self):
        r = self.asset()
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["rate"]), Decimal("0.167"))
        r = self.compute()
        self.assertEqual(r.status_code, 200, r.content)
        e = r.json()["entries"][0]
        self.assertEqual(e["months_in_service"], 12)
        self.assertEqual(Decimal(e["amount_total"]), Decimal("100200"))  # 600,000 × 0.167
        self.assertEqual(Decimal(e["amount_business"]), Decimal("100200"))
        j = Journal.objects.get(id=e["journal_id"])
        self.assertEqual(j.entry_kind, "adjustment")
        self.assertEqual(j.issue_date, date(YEAR, 12, 31))
        self.assertEqual(lines_of(j), {("debit", "depreciation"): Decimal("100200"), ("credit", "equipment"): Decimal("100200")})

    def test_straight_line_mid_year_proration_and_household_split(self):
        self.asset(acquired_on=f"{YEAR}-09-20", business_use_percent="70")
        e = self.compute().json()["entries"][0]
        self.assertEqual(e["months_in_service"], 4)  # 9,10,11,12 月（取得月を含む）
        self.assertEqual(Decimal(e["amount_total"]), Decimal("33400"))  # 600,000 × 0.167 × 4/12 = 33,400
        self.assertEqual(Decimal(e["amount_business"]), Decimal("23380"))  # × 70%
        self.assertEqual(Decimal(e["amount_household"]), Decimal("10020"))
        j = Journal.objects.get(id=e["journal_id"])
        self.assertEqual(lines_of(j), {("debit", "depreciation"): Decimal("23380"), ("debit", "owner_out"): Decimal("10020"), ("credit", "equipment"): Decimal("33400")})

    def test_memorandum_value_cap(self):
        a = FixedAsset.objects.create(taxpayer=self.tp, name="古い備品", acquired_on=date(2019, 1, 1), acquisition_cost=Decimal("120000"),
                                      depreciation_method="straight_line", useful_life_years=2, rate=Decimal("0.500"))
        # 2019 と 2020 で全額償却済みの状態を作る（2020 は備忘価額 1 円を残す）
        for y, amt in ((2019, Decimal("60000")), (2020, Decimal("59999"))):
            fy = books.get_fiscal_year(self.tp, y, create=True)
            DepreciationEntry.objects.create(asset=a, fiscal_year=fy, amount_total=amt, amount_business=amt, book_value_begin=0, book_value_end=0)
        e = self.compute().json()["entries"]
        row = next(x for x in e if x["asset_id"] == a.id)
        self.assertEqual(Decimal(row["amount_total"]), Decimal("0"))
        self.assertEqual(Decimal(row["book_value_begin"]), Decimal("1"))
        self.assertIsNone(row["journal_id"])

    def test_lump_sum_three_years(self):
        self.asset(name="プリンタ", acquisition_cost="150000", depreciation_method="lump_sum_3yr", useful_life_years=None, acquired_on=f"{YEAR}-11-30")
        e = self.compute().json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("50000"))  # 月割なし
        for y in (YEAR + 1, YEAR + 2):
            e = self.compute(year=y).json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("50000"))
        self.assertEqual(Decimal(e["book_value_end"]), Decimal("0"))
        self.assertEqual(Decimal(self.compute(year=YEAR + 3).json()["entries"][0]["amount_total"]), Decimal("0"))
        self.assertEqual(self.asset(name="x", acquisition_cost="200000", depreciation_method="lump_sum_3yr").status_code, 400)

    def test_immediate_expensing_to_supplies(self):
        self.asset(name="電卓", acquisition_cost="9800", depreciation_method="immediate", useful_life_years=None)
        e = self.compute().json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("9800"))
        self.assertEqual(lines_of(Journal.objects.get(id=e["journal_id"])), {("debit", "supplies"): Decimal("9800"), ("credit", "equipment"): Decimal("9800")})
        self.assertEqual(self.asset(name="y", acquisition_cost="100000", depreciation_method="immediate").status_code, 400)

    def test_blue_small_asset_requires_blue_filing(self):
        r = self.asset(name="PC", acquisition_cost="250000", depreciation_method="blue_small_asset", useful_life_years=None)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "unsupported")
        self.fy.filing_type = "blue_10"
        self.fy.save()
        r = self.asset(name="PC", acquisition_cost="250000", depreciation_method="blue_small_asset", useful_life_years=None)
        self.assertEqual(r.status_code, 201, r.content)
        e = self.compute().json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("250000"))
        # 年 300 万円の上限
        for i in range(12):
            self.asset(name=f"PC{i}", acquisition_cost="250000", depreciation_method="blue_small_asset", useful_life_years=None)
        r = self.compute()
        self.assertEqual(r.status_code, 400)
        self.assertIn("300万円", r.json()["error"]["message"])

    def test_declining_balance_rejected(self):
        r = self.asset(depreciation_method="declining_balance")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "unsupported")

    def test_voluntary_opening_cost(self):
        r = self.asset(name="開業費", kind="deferred_opening_cost", acquisition_cost="400000", depreciation_method="voluntary", useful_life_years=None, acquired_on=f"{YEAR}-03-01")
        self.assertEqual(r.status_code, 201, r.content)
        aid = r.json()["id"]
        r = self.compute(voluntary={str(aid): "500000"})
        self.assertEqual(r.status_code, 400)  # 残額超え
        e = self.compute(voluntary={str(aid): "150000"}).json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("150000"))
        self.assertEqual(lines_of(Journal.objects.get(id=e["journal_id"])), {("debit", "opening_cost_amort"): Decimal("150000"), ("credit", "deferred_opening_cost"): Decimal("150000")})
        # 指定を省いて再計算しても前回の額を保つ
        e = self.compute().json()["entries"][0]
        self.assertEqual(Decimal(e["amount_total"]), Decimal("150000"))
        # 翌年は残額 250,000 まで
        self.assertEqual(self.compute(year=YEAR + 1, voluntary={str(aid): "250000"}).status_code, 200)
        self.assertEqual(self.compute(year=YEAR + 2, voluntary={str(aid): "1"}).status_code, 400)

    def test_recompute_is_idempotent_and_revisions_on_change(self):
        r = self.asset()
        aid = r.json()["id"]
        self.compute()
        self.assertEqual(Journal.objects.filter(source_type="depreciation").count(), 1)
        self.compute()
        self.assertEqual(Journal.objects.filter(source_type="depreciation").count(), 1)
        j = Journal.objects.get(source_type="depreciation")
        self.assertEqual(j.revision, 1)
        self.assertEqual(self.owner.patch(f"/api/closing/fixed-assets/{aid}", {"business_use_percent": "50"}, format="json").status_code, 200)
        self.compute()
        j.refresh_from_db()
        self.assertEqual(j.revision, 2)
        self.assertEqual(lines_of(j)[("debit", "owner_out")], Decimal("50100"))
        self.assertEqual(DepreciationEntry.objects.count(), 1)

    def test_acquisition_journal_when_paid_from_given_and_delete_voids(self):
        r = self.asset(paid_from="bank")
        a = FixedAsset.objects.get(id=r.json()["id"])
        self.assertIsNotNone(a.acquisition_journal)
        self.assertEqual(lines_of(a.acquisition_journal), {("debit", "equipment"): Decimal("600000"), ("credit", "bank"): Decimal("600000")})
        self.assertEqual(self.owner.delete(f"/api/closing/fixed-assets/{a.id}").status_code, 204)
        a.acquisition_journal.refresh_from_db()
        self.assertTrue(a.acquisition_journal.is_void)

    def test_disposed_asset_prorates_to_disposal_month(self):
        r = self.asset()
        aid = r.json()["id"]
        r = self.owner.post(f"/api/closing/fixed-assets/{aid}/dispose", {"disposed_on": f"{YEAR}-06-15", "kind": "disposed"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        e = self.compute().json()["entries"][0]
        self.assertEqual(e["months_in_service"], 6)
        self.assertEqual(Decimal(e["amount_total"]), Decimal("50100"))
        self.assertEqual(self.compute(year=YEAR + 1).json()["entries"][0]["months_in_service"], 0)

    def test_locked_year_blocks_compute_and_assets(self):
        self.asset()
        books.close_year(self.fy, self.fx.users["owner"])
        r = self.compute()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["error"]["code"], "year_locked")
        self.assertEqual(self.asset(name="z").status_code, 409)


class InventoryTests(ClosingBase):
    def _stock_take(self):
        p_bottle = self.fx.products["champagne"]  # cost_price 9,000
        p_tequila = self.fx.products["tequila"]  # cost_price 3,000
        p_nocost = Product.objects.create(store=self.fx.store, category=self.fx.categories["drink"], name="原価なし", price=Decimal(500), stock_mode="bottle")
        sup = Supplier.objects.create(store=self.fx.store, name="酒屋")
        SupplierProduct.objects.create(store=self.fx.store, supplier=sup, product=p_bottle, purchase_price=Decimal("48000"), units_per_order_unit=Decimal("6"), is_preferred=True)
        st = StockTake.objects.create(store=self.fx.store, taken_at=timezone.now(), status="applied")
        StockTakeLine.objects.create(stock_take=st, product=p_bottle, theoretical_qty=3, counted_qty=Decimal("3"), difference=0)
        StockTakeLine.objects.create(stock_take=st, product=p_tequila, theoretical_qty=2, counted_qty=Decimal("2.5"), difference=0)
        StockTakeLine.objects.create(stock_take=st, product=p_nocost, theoretical_qty=1, counted_qty=Decimal("4"), difference=0)
        return st

    def test_closing_from_stock_take_cost_sources_and_journal(self):
        st = self._stock_take()
        r = self.owner.post("/api/closing/inventory", {"year": YEAR, "store": self.fx.store.id, "kind": "closing", "stock_take": st.id}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        by = {ln["name"]: ln for ln in d["lines"]}
        self.assertEqual(by["シャンパン"]["cost_source"], "supplier_product")
        self.assertEqual(Decimal(by["シャンパン"]["unit_cost"]), Decimal("8000.00"))  # 48,000 ÷ 6
        self.assertEqual(Decimal(by["シャンパン"]["amount"]), Decimal("24000"))
        self.assertEqual(by["テキーラショット"]["cost_source"], "product_cost")
        self.assertEqual(Decimal(by["テキーラショット"]["amount"]), Decimal("7500"))  # 2.5 × 3,000
        self.assertEqual(by["原価なし"]["cost_source"], "none")
        self.assertEqual(d["unpriced_count"], 1)
        self.assertEqual(Decimal(d["total_amount"]), Decimal("31500"))
        self.assertEqual(d["as_of"], f"{YEAR}-12-31")
        r = self.owner.post(f"/api/closing/inventory/{d['id']}/confirm", {}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        c = InventoryClosing.objects.get(id=d["id"])
        self.assertEqual(c.status, "confirmed")
        self.assertEqual(lines_of(c.journal), {("debit", "inventory"): Decimal("31500"), ("credit", "closing_inventory"): Decimal("31500")})
        # 確定を取り消すと仕訳も取消
        r = self.owner.post(f"/api/closing/inventory/{d['id']}/unconfirm", {"reason": "数え直し"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        c.journal.refresh_from_db()
        self.assertTrue(c.journal.is_void)

    def test_opening_from_previous_year_closing(self):
        st = self._stock_take()
        r = self.owner.post("/api/closing/inventory", {"year": YEAR - 1, "store": self.fx.store.id, "kind": "closing", "stock_take": st.id}, format="json", **key())
        self.owner.post(f"/api/closing/inventory/{r.json()['id']}/confirm", {}, format="json", **key())
        r = self.owner.post("/api/closing/inventory", {"year": YEAR, "store": self.fx.store.id, "from_previous": True}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()
        self.assertEqual(d["kind"], "opening")
        self.assertEqual(Decimal(d["total_amount"]), Decimal("31500"))
        self.assertEqual(d["as_of"], f"{YEAR}-01-01")
        self.owner.post(f"/api/closing/inventory/{d['id']}/confirm", {}, format="json", **key())
        c = InventoryClosing.objects.get(id=d["id"])
        self.assertEqual(lines_of(c.journal), {("debit", "opening_inventory"): Decimal("31500"), ("credit", "inventory"): Decimal("31500")})
        # 前年の期末が無い店舗は 404
        self.assertEqual(self.owner.post("/api/closing/inventory", {"year": YEAR + 5, "store": self.fx.store.id, "from_previous": True}, format="json", **key()).status_code, 404)

    def test_manual_lines_and_unique_per_store_kind(self):
        r = self.owner.post("/api/closing/inventory", {"year": YEAR, "store": self.fx.store.id, "kind": "closing", "lines": [{"name": "ビール", "quantity": "10", "unit_cost": "250.5"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Decimal(r.json()["total_amount"]), Decimal("2505"))
        self.assertEqual(r.json()["method"], "manual")
        # 同じ年度・店舗・区分は作り直し（下書きのうち）
        r2 = self.owner.post("/api/closing/inventory", {"year": YEAR, "store": self.fx.store.id, "kind": "closing", "lines": [{"name": "ビール", "quantity": "1", "unit_cost": "100"}]}, format="json", **key())
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2.json()["id"], r.json()["id"])
        self.assertEqual(InventoryClosing.objects.count(), 1)


class AdjustmentAndPlTests(ClosingBase):
    def test_adjustment_journal_and_void(self):
        r = self.owner.get(f"/api/closing/adjustments?year={YEAR}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(k["key"] == "accrued_expense" for k in r.json()["kinds"]))
        r = self.owner.post("/api/closing/adjustments", {"year": YEAR, "kind": "accrued_expense", "description": "12月分の電気代", "amount": "23000",
                                                         "debit_key": "utilities", "credit_key": "accrued_expenses"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        adj = ClosingAdjustment.objects.get()
        self.assertEqual(adj.issue_date, date(YEAR, 12, 31))
        self.assertEqual(lines_of(adj.journal), {("debit", "utilities"): Decimal("23000"), ("credit", "accrued_expenses"): Decimal("23000")})
        pl = self.owner.get(f"/api/closing/years/{YEAR}/pl").json()
        self.assertEqual(Decimal(pl["total_expense"]), Decimal("23000"))
        self.assertEqual(next(x for x in pl["by_statement_line"] if x["line"] == "utilities")["amount"], "23000.00")
        self.assertEqual(len(pl["adjustments"]), 1)
        # 取消
        self.assertEqual(self.owner.post(f"/api/closing/adjustments/{adj.id}/void", {}, format="json").status_code, 400)
        r = self.owner.post(f"/api/closing/adjustments/{adj.id}/void", {"reason": "誤り"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        adj.journal.refresh_from_db()
        self.assertTrue(adj.journal.is_void)
        pl = self.owner.get(f"/api/closing/years/{YEAR}/pl").json()
        self.assertEqual(Decimal(pl["total_expense"]), Decimal("0"))
        # 不正: 同じ科目・知らないキー・貸借不一致はない（単一金額）
        self.assertEqual(self.owner.post("/api/closing/adjustments", {"year": YEAR, "kind": "other", "description": "x", "amount": "1", "debit_key": "cash", "credit_key": "cash"}, format="json", **key()).status_code, 400)
        self.assertEqual(self.owner.post("/api/closing/adjustments", {"year": YEAR, "kind": "other", "description": "x", "amount": "1", "debit_key": "nope", "credit_key": "cash"}, format="json", **key()).status_code, 400)

    def test_year_pl_equals_trial_balance_and_exports(self):
        self.asset()
        self.compute()
        self.owner.post("/api/closing/adjustments", {"year": YEAR, "kind": "accrued_expense", "description": "家賃", "amount": "100000", "debit_key": "rent", "credit_key": "accrued_expenses"}, format="json", **key())
        self.owner.post("/api/accounting/journals/manual", {"issue_date": f"{YEAR}-05-01", "description": "売上", "lines": [
            {"side": "debit", "key": "cash", "amount": "300000"}, {"side": "credit", "key": "sales", "amount": "300000"}]}, format="json", **key())
        pl = self.owner.get(f"/api/closing/years/{YEAR}/pl?by=store").json()
        self.assertEqual(Decimal(pl["total_income"]), Decimal("300000"))
        self.assertEqual(Decimal(pl["total_expense"]), Decimal("200200"))  # 償却 100,200 + 家賃 100,000
        self.assertEqual(Decimal(pl["profit"]), Decimal("99800"))
        tb = ledgers.trial_balance(self.tp, self.fy.start_date, self.fy.end_date)
        rev = sum(Decimal(r["balance"]) for r in tb["rows"] if r["category"] == "revenue")
        exp = sum(Decimal(r["balance"]) for r in tb["rows"] if r["category"] in ("expense", "cogs"))
        self.assertEqual(rev - exp, Decimal(pl["profit"]))
        self.assertEqual(pl["month_summary"][4]["income"], "300000.00")
        self.assertEqual(len(pl["months"]), 12)
        self.assertEqual(Decimal(pl["depreciation_total"]), Decimal("100200"))
        x = self.owner.get(f"/api/closing/years/{YEAR}/pl.xlsx")
        self.assertEqual(x.status_code, 200)
        self.assertEqual(x.content[:2], b"PK")
        p = self.owner.get(f"/api/closing/years/{YEAR}/pl.pdf")
        self.assertEqual(p.status_code, 200)
        self.assertEqual(p.content[:4], b"%PDF")

    def test_checklist_reports_missing_depreciation(self):
        self.asset()
        c = self.owner.get(f"/api/closing/years/{YEAR}/checklist").json()
        self.assertEqual(next(i for i in c["items"] if i["code"] == "depreciation")["count"], 1)
        self.compute()
        c = self.owner.get(f"/api/closing/years/{YEAR}/checklist").json()
        self.assertEqual(next(i for i in c["items"] if i["code"] == "depreciation")["count"], 0)
        self.assertTrue(any(m["key"] == "straight_line" for m in c["methods"]))


class PermissionTests(ClosingBase):
    def test_roles(self):
        # 店長: 閲覧も作成も不可（accounting.view を持たない）
        self.assertEqual(self.manager.get(f"/api/closing/years/{YEAR}/pl").status_code, 403)
        self.assertEqual(self.manager.post("/api/closing/fixed-assets", {"name": "x"}, format="json", **key()).status_code, 403)
        # 記帳代行: 準備（登録・計算）はできる
        bk = make_user("bk_closing")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=bk, role="bookkeeper")
        c = client_for(bk, self.fx.store)
        self.assertEqual(self.asset().status_code, 201)
        self.assertEqual(self.compute(client=c).status_code, 200)
        # 会社管理者: 準備できる
        admin = make_user("admin_closing")
        CompanyMembership.objects.create(user=admin, company=self.fx.company, role="admin")
        self.assertEqual(self.compute(client=client_for(admin, self.fx.store)).status_code, 200)
        # 税理士等: 閲覧のみ
        adv = make_user("adv_closing")
        AccountingAccessGrant.objects.create(company=self.fx.company, user=adv, role="tax_advisor")
        ca = client_for(adv, self.fx.store)
        self.assertEqual(ca.get(f"/api/closing/years/{YEAR}/pl").status_code, 200)
        self.assertEqual(self.compute(client=ca).status_code, 403)
        # 他社の事業者の資産は見えない
        other = standard_fixture("s9", company_code="c9")
        make_taxpayer(other, name="他社", withholding=False)
        a = FixedAsset.objects.get()
        self.assertEqual(client_for(other.users["owner"], other.store).get(f"/api/closing/fixed-assets/{a.id}").status_code, 404)
