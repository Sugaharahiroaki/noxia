"""M4: タグ集計（複数タグの重複計上）・商品集計（折半の配分、取消済みを数えない）。"""
from __future__ import annotations

from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.analytics import reports
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.sales.models import Tag

D = Decimal


class SplitLineTests(SimpleTestCase):
    def test_no_target_goes_to_no_cast(self):
        self.assertEqual(reports.split_line_for_casts(D(3000), D(2), []), {reports.NO_CAST: (D(3000), D(2))})

    def test_percent_follows_share(self):
        got = reports.split_line_for_casts(D(10000), D(1), [(1, "percent", D(70)), (2, "percent", D(30))])
        self.assertEqual(got, {1: (D(7000), D("0.70")), 2: (D(3000), D("0.30"))})

    def test_fixed_split_is_equal_floor(self):
        # 参考設計 6.8: 10,000 円を 3 人 → 3,333 円 × 3
        got = reports.split_line_for_casts(D(10000), D(1), [(1, "fixed", D(200)), (2, "fixed", D(3800)), (3, "fixed", D(0))])
        self.assertEqual([got[i][0] for i in (1, 2, 3)], [D(3333)] * 3)
        self.assertEqual(got[1][1], D("0.33"))


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        self.yobi = Tag.objects.create(store=self.store, name="呼び", color="#d9534f", sort_order=1)
        self.new = Tag.objects.create(store=self.store, name="新規", sort_order=2)

    def visit(self, table=0, guests=1, tags=()):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": guests, "course_id": self.fx.hourly.id}]})
        if tags:
            services.set_visit_tags(self.store, self.user, v.id, [t.id for t in tags])
        return v

    def pay(self, v, method="cash"):
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return services.confirm_checkout(self.store, self.user, v.id, {"payments": [p]})

    def line(self, v, product, qty=1, targets=None):
        return services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products[product].id, "quantity": qty, "back_targets": targets or []})


class TagReportTests(Base):
    def test_multiple_tags_are_counted_for_each_tag(self):
        a = self.visit(0, guests=2, tags=[self.yobi, self.new])   # 5,000 現金（両方のタグに数える）
        b = self.visit(1, guests=1, tags=[self.yobi])             # 2,500 カード
        c = self.visit(2, guests=3)                               # タグなし
        self.pay(a, "cash")
        self.pay(b, "card")
        self.pay(c, "cash")
        d = self.c.get("/api/reports/tags").json()
        rows = {r["tag_name"]: r for r in d["rows"]}
        self.assertEqual((rows["呼び"]["count"], rows["呼び"]["guests"], D(rows["呼び"]["subtotal"]), D(rows["呼び"]["cash"]), D(rows["呼び"]["card"]), D(rows["呼び"]["total"])),
                         (2, 3, D(7500), D(5000), D(2500), D(7500)))
        self.assertEqual((rows["新規"]["count"], rows["新規"]["guests"], D(rows["新規"]["total"])), (1, 2, D(5000)))
        # タグの行の合計（12,500）は、タグの付いた伝票の実際の合計（7,500）と一致しない → 注記で説明
        self.assertEqual(D(d["totals"]["total"]), D(12500))
        self.assertEqual(d["tagged_slips"], 2)
        self.assertIn("重複して数えます", d["note"])
        self.assertEqual([r["tag_name"] for r in d["rows"]], ["呼び", "新規"])  # 並び順はタグの並び順

    def test_group_by_day_and_month_and_validation(self):
        a = self.visit(0, tags=[self.yobi])
        self.pay(a)
        day = self.c.get("/api/reports/tags", {"group_by": "day"}).json()["rows"]
        self.assertEqual([(r["period"], r["tag_name"]) for r in day], [(a.business_date.isoformat(), "呼び")])
        month = self.c.get("/api/reports/tags", {"group_by": "month"}).json()["rows"]
        self.assertEqual(month[0]["period"], a.business_date.strftime("%Y-%m"))
        self.assertEqual(self.c.get("/api/reports/tags", {"group_by": "x"}).status_code, 400)

    def test_cancelled_visit_and_unpaid_amounts(self):
        a = self.visit(0, tags=[self.yobi])
        b = self.visit(1, tags=[self.yobi])
        services.cancel_visit(self.store, self.user, b.id, "入力ミス")
        rows = self.c.get("/api/reports/tags").json()["rows"]
        # 取消の伝票は数えない。営業中の伝票は件数に入るが、会計前なので金額は 0
        self.assertEqual((rows[0]["count"], D(rows[0]["total"])), (1, D(0)))
        self.pay(a)
        self.assertEqual(D(self.c.get("/api/reports/tags").json()["rows"][0]["total"]), D(2500))

    def test_other_store_and_permission(self):
        other = make_store("s2", company_code="c2")
        ot = Tag.objects.create(store=other.store, name="他店")
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        services.set_visit_tags(other.store, self.user, ov.id, [ot.id])
        self.assertEqual(self.c.get("/api/reports/tags").json()["rows"], [])
        cast = client_for(self.fx.users["cast"], self.store)
        for path in ("/api/reports/tags", "/api/reports/items", "/api/reports/export/tags.csv"):
            self.assertEqual(cast.get(path).status_code, 403, path)


class ItemReportTests(Base):
    def test_products_by_date_and_cancelled_lines_are_not_counted(self):
        v = self.visit(0)
        self.line(v, "beer", 3)
        gone = self.line(v, "beer", 2)
        services.cancel_line(self.store, self.user, v.id, gone.id, "入力ミス")
        self.line(v, "food", 1)
        co = self.pay(v)
        unpaid = self.visit(1)
        self.line(unpaid, "beer", 5)   # 未会計は数えない
        d = self.c.get("/api/reports/items").json()
        rows = {p["name"]: p for p in d["products"]}
        date = co.business_date.isoformat()
        self.assertEqual((D(rows["ビール"]["quantity"]), D(rows["ビール"]["amount"]), D(rows["ビール"]["by_date"][date])), (D(3), D(2400), D(3)))
        self.assertEqual(D(rows["乾き物"]["quantity"]), D(1))
        self.assertEqual((d["dates"], D(d["totals"]["quantity"]), D(d["totals"]["amount"]), D(d["totals"]["by_date"][date])), ([date], D(4), D(2900), D(4)))
        # 会計を取り消すと数えなくなる
        services.void_checkout(self.store, self.user, co.id, "打ち間違い")
        self.assertEqual(self.c.get("/api/reports/items").json()["products"], [])

    def test_cast_allocation_follows_back_targets(self):
        v = self.visit(0)
        self.line(v, "cast_drink", 2, [{"employee_id": self.cast1.id}])                                           # 3,000 → cast1
        self.line(v, "champagne", 1, [{"employee_id": self.cast1.id, "share_type": "percent", "share_value": 70},
                                      {"employee_id": self.cast2.id, "share_type": "percent", "share_value": 30}])     # 30,000 → 21,000 / 9,000
        self.line(v, "tequila", 1, [{"employee_id": self.cast1.id, "share_type": "fixed", "share_value": 100},
                                    {"employee_id": self.cast2.id, "share_type": "fixed", "share_value": 50}])         # 1,000 → 500 / 500
        self.line(v, "beer", 1)                                                                                    # 担当なし
        self.pay(v, "card")
        d = self.c.get("/api/reports/items").json()
        casts = {c["employee_name"]: c for c in d["casts"]}
        self.assertEqual(D(casts["cast1"]["amount"]), D(3000 + 21000 + 500))
        self.assertEqual(D(casts["cast2"]["amount"]), D(9000 + 500))
        self.assertEqual(D(casts["担当なし（お客様の注文など）"]["amount"]), D(800))
        items1 = {i["name"]: i for i in casts["cast1"]["items"]}
        self.assertEqual((D(items1["キャストドリンク"]["quantity"]), D(items1["シャンパン"]["quantity"]), D(items1["シャンパン"]["amount"])), (D(2), D("0.70"), D(21000)))
        # 販売額は 1 回だけ（商品別の合計は明細の合計）
        self.assertEqual(D(d["totals"]["amount"]), D(3000 + 30000 + 1000 + 800))
        self.assertEqual(d["casts"][-1]["employee_id"], None)  # 担当なしは最後

    def test_csv_exports(self):
        v = self.visit(0, tags=[self.yobi])
        self.line(v, "cast_drink", 1, [{"employee_id": self.cast1.id}])
        self.pay(v)
        owner = client_for(self.fx.users["owner"], self.store)
        tags = owner.get("/api/reports/export/tags.csv").content.decode("utf-8-sig").splitlines()
        self.assertEqual(tags[0], "期間,タグ,件数,客数,伝票小計,現金,カード,売掛,販売額")
        self.assertTrue(tags[1].startswith(",呼び,1,1,4000"))
        items = owner.get("/api/reports/export/items.csv").content.decode("utf-8-sig").splitlines()
        self.assertEqual(items[0], "区分,カテゴリー,商品,キャスト,個数,金額")
        self.assertTrue(any(l.startswith("キャスト別,,キャストドリンク,cast1,1") for l in items), items)
        self.assertEqual(owner.get("/api/reports/export/x.csv").status_code, 404)
        # スタッフ（export.sales なし）は出力できない
        self.assertEqual(client_for(self.fx.users["staff"], self.store).get("/api/reports/export/tags.csv").status_code, 403)
