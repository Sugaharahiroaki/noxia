"""M1: 集計の土台（キャスト売上の配分）・伝票一覧・伝票詳細。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.analytics import reports
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services
from apps.sales.models import Tag


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class PureFunctionTests(SimpleTestCase):
    def test_split_amount(self):
        self.assertEqual(reports.split_amount(Decimal(26000), 1), [Decimal(26000)])
        self.assertEqual(reports.split_amount(Decimal(26000), 2), [Decimal(13000), Decimal(13000)])
        # 割り切れないときは円未満切り捨て・余りは先頭へ（合計は元の金額と一致）
        parts = reports.split_amount(Decimal(10000), 3)
        self.assertEqual(parts, [Decimal(3334), Decimal(3333), Decimal(3333)])
        self.assertEqual(sum(parts), Decimal(10000))
        self.assertEqual(reports.split_amount(Decimal(10000), 3, "full_each"), [Decimal(10000)] * 3)
        self.assertEqual(reports.split_amount(Decimal(10000), 0), [])

    def test_single_nomination_gets_whole_subtotal(self):
        # 参考設計 6.3 の例: 小計 26,000 の伝票 → 指名キャストに 26,000
        r = reports.cast_sales_for_slip(Decimal(26000), Decimal(29900), [(1, "nomination")], set_count=1)
        self.assertEqual(r, {1: {"yobi_subtotal": Decimal(26000), "yobi_total": Decimal(29900), "douhan_subtotal": Decimal(0), "douhan_total": Decimal(0),
                                 "nomination_count": Decimal(1), "jounai_count": Decimal(0), "yobi_count": Decimal(0), "douhan_count": Decimal(0)}})

    def test_multiple_casts_equal_split_and_full_each(self):
        d = [(1, "nomination"), (2, "jounai")]
        r = reports.cast_sales_for_slip(Decimal(26000), Decimal(29900), d, set_count=1)
        self.assertEqual((r[1]["yobi_subtotal"], r[2]["yobi_subtotal"]), (Decimal(13000), Decimal(13000)))
        self.assertEqual((r[1]["yobi_total"], r[2]["yobi_total"]), (Decimal(14950), Decimal(14950)))
        self.assertEqual((r[1]["nomination_count"], r[1]["jounai_count"], r[2]["jounai_count"]), (Decimal(1), Decimal(0), Decimal(1)))
        r = reports.cast_sales_for_slip(Decimal(26000), Decimal(29900), d, set_count=1, split_mode="full_each")
        self.assertEqual((r[1]["yobi_subtotal"], r[2]["yobi_subtotal"]), (Decimal(26000), Decimal(26000)))

    def test_same_cast_twice_counts_once_and_douhan_is_separate(self):
        d = [(1, "nomination"), (1, "nomination"), (1, "douhan"), (2, "douhan")]
        r = reports.cast_sales_for_slip(Decimal(20000), Decimal(22000), d, set_count=1)
        self.assertEqual(r[1]["yobi_subtotal"], Decimal(20000))  # 呼びは 1 人だけ
        self.assertEqual((r[1]["douhan_subtotal"], r[2]["douhan_subtotal"]), (Decimal(10000), Decimal(10000)))
        self.assertEqual((r[1]["nomination_count"], r[1]["douhan_count"]), (Decimal(1), Decimal(1)))
        self.assertEqual(r[2]["yobi_subtotal"], Decimal(0))

    def test_no_designation_and_count_per_set(self):
        self.assertEqual(reports.cast_sales_for_slip(Decimal(5000), Decimal(5500), [], set_count=2), {})
        # 「セット数ぶん数える」設定: 2 セット指名 = 2 回
        r = reports.cast_sales_for_slip(Decimal(5000), Decimal(5500), [(1, "nomination")], set_count=2, count_mode="per_set")
        self.assertEqual(r[1]["nomination_count"], Decimal(2))
        r = reports.cast_sales_for_slip(Decimal(5000), Decimal(5500), [(1, "nomination")], set_count=2, count_mode="per_slip")
        self.assertEqual(r[1]["nomination_count"], Decimal(1))

    def test_ratio_and_per_unit(self):
        # 参考設計 6.6 の検算例
        self.assertEqual(reports.ratio_percent(Decimal(28075), Decimal(97800)), Decimal("28.71"))
        self.assertEqual(reports.per_unit(Decimal(488300), 41), Decimal(11910))
        self.assertEqual(reports.per_unit(Decimal(488300), 20), Decimal(24415))
        self.assertIsNone(reports.ratio_percent(Decimal(1), Decimal(0)))
        self.assertIsNone(reports.per_unit(Decimal(1), 0))


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]

    def visit(self, table=0, guests=2, fees=(), beer=0):
        """入店して、指名などと注文を足す。fees: [("nomination", emp), ...]"""
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": guests, "course_id": self.fx.hourly.id}]})
        for kind, emp in fees:
            services.add_fee_line(self.store, self.user, v.id, kind, {"employee_id": emp.id})
        if beer:
            services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products["beer"].id, "quantity": beer})
        return v

    def pay(self, v, method="cash", **extra):
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return services.confirm_checkout(self.store, self.user, v.id, {"payments": [p], **extra})

    def slips(self, **params):
        r = self.c.get("/api/reports/slips", params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def detail(self, v):
        r = self.c.get(f"/api/reports/slips/{v.id}")
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()


class SlipDetailTests(Base):
    def test_single_nomination_whole_subtotal(self):
        v = self.visit(fees=[("nomination", self.cast1)], beer=2)  # 5,000 + 1,000 + 1,600 = 7,600
        co = self.pay(v)
        d = self.detail(v)
        self.assertEqual((Decimal(d["subtotal"]), Decimal(d["total"])), (co.subtotal, co.total))
        self.assertEqual(len(d["cast_sales"]), 1)
        row = d["cast_sales"][0]
        self.assertEqual((row["employee_name"], Decimal(row["yobi_subtotal"]), Decimal(row["yobi_total"])), ("cast1", Decimal(7600), Decimal(7600)))
        self.assertEqual((Decimal(row["nomination_count"]), Decimal(row["douhan_subtotal"])), (Decimal(1), Decimal(0)))
        self.assertEqual(d["nomination"], ["cast1"])
        # 明細: 種別・キャスト・単価・税・数量・計
        nom = [l for l in d["lines"] if l["line_type"] == "nomination"][0]
        self.assertEqual((nom["line_type_label"], nom["cast"], Decimal(nom["amount"]), nom["paid"]), ("本指名", ["cast1"], Decimal(1000), True))
        self.assertEqual(Decimal(d["cash"]), Decimal(7600))

    def test_multiple_casts_split_and_setting_switch(self):
        self.fx.settings.jounai_fee = Decimal(800)
        self.fx.settings.save()
        v = self.visit(table=1, fees=[("nomination", self.cast1), ("jounai", self.cast2)])  # 5,000 + 1,000 + 800 = 6,800
        self.pay(v)
        rows = {r["employee_name"]: r for r in self.detail(v)["cast_sales"]}
        self.assertEqual((Decimal(rows["cast1"]["yobi_subtotal"]), Decimal(rows["cast2"]["yobi_subtotal"])), (Decimal(3400), Decimal(3400)))
        self.assertEqual(self.detail(v)["settings"]["cast_sales_split_mode"], "equal")
        self.fx.settings.cast_sales_split_mode = "full_each"
        self.fx.settings.save()
        rows = {r["employee_name"]: r for r in self.detail(v)["cast_sales"]}
        self.assertEqual((Decimal(rows["cast1"]["yobi_subtotal"]), Decimal(rows["cast2"]["yobi_subtotal"])), (Decimal(6800), Decimal(6800)))

    def test_no_nomination_and_unpaid_visit(self):
        v = self.visit(beer=1)
        self.assertEqual(self.detail(v)["cast_sales"], [])
        self.assertEqual(Decimal(self.detail(v)["total"]), Decimal(0))  # 未会計の伝票は確定額なし
        self.pay(v)
        self.assertEqual(self.detail(v)["cast_sales"], [])
        self.assertEqual(Decimal(self.detail(v)["total"]), Decimal(5800))

    def test_payment_method_does_not_change_subtotal_allocation(self):
        a = self.visit(table=0, fees=[("nomination", self.cast1)])
        b = self.visit(table=1, fees=[("nomination", self.cast1)])
        self.pay(a, "cash")
        self.pay(b, "card")
        ra, rb = self.detail(a)["cast_sales"][0], self.detail(b)["cast_sales"][0]
        self.assertEqual(ra["yobi_subtotal"], rb["yobi_subtotal"])
        self.assertEqual((Decimal(self.detail(a)["cash"]), Decimal(self.detail(b)["card"])), (Decimal(6000), Decimal(6000)))

    def test_voided_and_refunded(self):
        v = self.visit(fees=[("nomination", self.cast1)])
        co = self.pay(v)
        # 一部返金: 会計は有効のまま。配分は変わらず、返金額が別に出る
        services.refund_checkout(self.store, self.user, co.id, {"amount": "1000", "method": "cash", "reason": "サービス"})
        d = self.detail(v)
        self.assertEqual((Decimal(d["refunds"]), Decimal(d["cast_sales"][0]["yobi_subtotal"])), (Decimal(1000), Decimal(6000)))
        # 会計の取消: 確定額が無くなり、配分も無くなる
        v2 = self.visit(table=1, fees=[("nomination", self.cast1)])
        co2 = self.pay(v2)
        services.void_checkout(self.store, self.user, co2.id, "打ち間違い")
        d2 = self.detail(v2)
        self.assertEqual((Decimal(d2["total"]), d2["cast_sales"], d2["voided_checkouts"]), (Decimal(0), [], 1))

    def test_split_checkout_sums_confirmed_checkouts(self):
        v = self.visit(fees=[("nomination", self.cast1)], beer=1)
        lines = {l.line_type: l for l in v.lines.all()}
        services.confirm_checkout(self.store, self.user, v.id, {"line_ids": [lines["product"].id], "payments": [{"method": "card", "amount": "800"}]})
        d = self.detail(v)
        self.assertEqual((Decimal(d["subtotal"]), d["cast_sales"]), (Decimal(800), []))  # 指名の明細はまだ未会計
        self.pay(v)
        d = self.detail(v)
        self.assertEqual((Decimal(d["subtotal"]), d["checkout_count"], Decimal(d["cast_sales"][0]["yobi_subtotal"])), (Decimal(6800), 2, Decimal(6800)))

    def test_other_store_slip_is_rejected_and_permissions(self):
        other = make_store("s2", company_code="c2")
        ov = services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        self.assertEqual(self.c.get(f"/api/reports/slips/{ov.id}").status_code, 404)
        # キャストの役割（sales.view なし）は見られない
        cast = client_for(self.fx.users["cast"], self.store)
        v = self.visit()
        self.assertEqual(cast.get(f"/api/reports/slips/{v.id}").status_code, 403)
        self.assertEqual(cast.get("/api/reports/slips").status_code, 403)


class SlipListTests(Base):
    def test_list_totals_and_filters(self):
        a = self.visit(table=0, fees=[("nomination", self.cast1)])       # 6,000 現金
        b = self.visit(table=1, beer=1)                                   # 5,800 カード
        c = self.visit(table=2)                                           # 5,000 − 500 値引き → ツケ 4,500
        services.add_discount(self.store, self.user, c.id, {"amount": "500", "reason": "サービス"})
        tag = Tag.objects.create(store=self.store, name="呼び")
        services.set_visit_tags(self.store, self.user, a.id, [tag.id])
        self.pay(a, "cash")
        self.pay(b, "card")
        self.pay(c, "receivable")
        d = self.slips()
        self.assertEqual([r["slip_number"] for r in d["rows"]], [a.slip_number, b.slip_number, c.slip_number])
        t = d["totals"]
        self.assertEqual((t["count"], Decimal(t["total"]), Decimal(t["cash"]), Decimal(t["card"]), Decimal(t["receivable"]), Decimal(t["discount"])),
                         (3, Decimal(16300), Decimal(6000), Decimal(5800), Decimal(4500), Decimal(500)))
        self.assertEqual(Decimal(t["cash"]) + Decimal(t["card"]) + Decimal(t["receivable"]), Decimal(t["total"]))
        row_a = d["rows"][0]
        self.assertEqual((row_a["nomination"], [x["name"] for x in row_a["tags"]], row_a["status"]), (["cast1"], ["呼び"], "closed"))
        self.assertEqual([r["slip_number"] for r in self.slips(payment="card")["rows"]], [b.slip_number])
        self.assertEqual([r["slip_number"] for r in self.slips(payment="receivable")["rows"]], [c.slip_number])
        self.assertEqual([r["slip_number"] for r in self.slips(discount="1")["rows"]], [c.slip_number])
        self.assertEqual(Decimal(self.slips(payment="card")["totals"]["total"]), Decimal(5800))

    def test_cancelled_filter_and_date_range(self):
        a = self.visit(table=0)
        b = self.visit(table=1)
        services.cancel_visit(self.store, self.user, b.id, "入力ミス")
        self.assertEqual([r["id"] for r in self.slips()["rows"]], [a.id])
        cancelled = self.slips(cancelled="1")["rows"]
        self.assertEqual([(r["id"], r["status"], r["cancel_reason"]) for r in cancelled], [(b.id, "cancelled", "入力ミス")])
        # 期間の外
        self.assertEqual(self.slips(**{"from": "2020-01-01", "to": "2020-01-31"})["rows"], [])
        self.assertEqual(self.c.get("/api/reports/slips", {"from": "2020-01-01", "to": "2026-01-01"}).status_code, 400)

    def test_other_store_slips_are_not_mixed(self):
        other = make_store("s2", company_code="c2")
        services.open_visit(other.store, self.user, {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]})
        a = self.visit()
        self.assertEqual([r["id"] for r in self.slips()["rows"]], [a.id])

    def test_report_settings_are_saved(self):
        owner = client_for(self.fx.users["owner"], self.store)
        r = owner.put("/api/store/settings", {"cast_sales_split_mode": "full_each", "nomination_count_mode": "per_set"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.slips()["settings"]["nomination_count_mode"], "per_set")
        self.assertEqual(owner.put("/api/store/settings", {"cast_sales_split_mode": "x"}, format="json").status_code, 400)
