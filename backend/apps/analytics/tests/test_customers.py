"""M5: お客様分析（来店回数・単価・ランク、会社内共有の設定、別店舗・別会社が混ざらないこと）。"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from apps.analytics import reports
from apps.core.testing import add_user, client_for, make_store, standard_fixture
from apps.customers.models import BottleKeep, Customer
from apps.sales import services
from apps.sales.models import Visit

D = Decimal
RANKS = [{"label": "S", "min_visits": 3}, {"label": "A", "min_visits": 2}, {"label": "C", "min_visits": 1}]


class RankTests(SimpleTestCase):
    def test_rank_by_recent_visits(self):
        self.assertEqual([reports.customer_rank(n, RANKS) for n in (0, 1, 2, 3, 9)], ["", "C", "A", "S", "S"])
        self.assertEqual(reports.customer_rank(5, []), "")  # しきい値が未設定ならランクなし

    def test_thresholds_are_sorted_and_broken_items_dropped(self):
        got = reports.clean_rank_thresholds([{"label": "C", "min_visits": 1}, {"label": "S", "min_visits": "20"}, {"x": 1}, "bad"])
        self.assertEqual(got, [{"label": "S", "min_visits": 20}, {"label": "C", "min_visits": 1}])
        self.assertEqual(reports.clean_rank_thresholds(None), [])

    def test_months_before(self):
        self.assertEqual(reports.months_before(date(2026, 5, 31), 3), date(2026, 2, 28))
        self.assertEqual(reports.months_before(date(2026, 2, 10), 3), date(2025, 11, 10))


class CustomerReportTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture(customer_rank_thresholds=RANKS)
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.tanaka = Customer.objects.create(company=self.store.company, store=self.store, display_name="田中様", assigned_employee=self.cast1)
        self.sato = Customer.objects.create(company=self.store.company, store=self.store, display_name="佐藤様")

    def visit(self, customer, table=0, guests=1, store=None, fx=None):
        fx = fx or self.fx
        return services.open_visit(fx.store, self.user, {"table_id": fx.tables[table].id, "customer_id": customer.id,
                                                         "entries": [{"guest_count": guests, "course_id": fx.hourly.id}]})

    def pay(self, v, method="cash", store=None):
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return services.confirm_checkout(v.store, self.user, v.id, {"payments": [p]})

    def rows(self, client=None):
        d = (client or self.c).get("/api/reports/customers").json()
        return d, {r["name"]: r for r in d["rows"]}

    def test_counts_amounts_and_rank(self):
        a = self.visit(self.tanaka, guests=2)                      # 5,000 + 指名 1,000 = 6,000 現金
        services.add_fee_line(self.store, self.user, a.id, "nomination", {"employee_id": self.cast1.id})
        self.pay(a, "cash")
        b = self.visit(self.tanaka, table=1)                       # 2,500 カード
        self.pay(b, "card")
        gone = self.visit(self.tanaka, table=2)                    # 取消した伝票は数えない
        services.cancel_visit(self.store, self.user, gone.id, "入力ミス")
        old = self.visit(self.tanaka, table=2)                     # 4 か月前の来店（来店回数には入るが、ランクには入らない）
        self.pay(old, "cash")
        past = a.business_date - timedelta(days=125)
        Visit.objects.filter(id=old.id).update(business_date=past)
        BottleKeep.objects.create(store=self.store, customer=self.tanaka, name="モエ", registered_on=a.business_date)
        BottleKeep.objects.create(store=self.store, customer=self.tanaka, name="飲み切り", registered_on=a.business_date, status="consumed")
        d, rows = self.rows()
        r = rows["田中様"]
        self.assertEqual((r["visits"], r["visits_recent"], r["rank"], r["guests"]), (3, 2, "A", 4))
        self.assertEqual((r["first_visit"], r["last_visit"]), (past.isoformat(), a.business_date.isoformat()))
        self.assertEqual((D(r["total"]), D(r["cash"]), D(r["card"]), D(r["receivable"])), (D(11000), D(8500), D(2500), D(0)))
        self.assertEqual(D(r["unit_price"]), D(3667))             # 11,000 ÷ 3 = 3,666.67 → 四捨五入
        self.assertEqual((r["nomination"], r["jounai"], r["douhan"]), (1, 0, 0))
        self.assertEqual((r["keep_count"], r["assigned_employee_name"]), (1, "cast1"))
        self.assertIsNotNone(r["avg_minutes"])
        # 来店の無いお客様も行は出る（ランクなし・金額 0）。並びは最終来店日の新しい順で、来店なしは最後
        s = rows["佐藤様"]
        self.assertEqual((s["visits"], s["rank"], s["last_visit"], s["unit_price"], D(s["total"])), (0, "", None, None, D(0)))
        self.assertEqual([x["name"] for x in d["rows"]], ["田中様", "佐藤様"])
        self.assertEqual((d["totals"]["customers"], d["totals"]["visits"], D(d["totals"]["total"])), (2, 3, D(11000)))
        self.assertEqual(d["rank_thresholds"][0], {"label": "S", "min_visits": 3})

    def test_unpaid_visit_counts_as_visit_without_amount(self):
        self.visit(self.tanaka)
        _, rows = self.rows()
        self.assertEqual((rows["田中様"]["visits"], D(rows["田中様"]["total"]), rows["田中様"]["avg_minutes"]), (1, D(0), None))

    def test_no_thresholds_means_no_rank(self):
        st = self.store.settings
        st.customer_rank_thresholds = []
        st.save()
        self.pay(self.visit(self.tanaka))
        d, rows = self.rows()
        self.assertEqual((rows["田中様"]["rank"], d["rank_thresholds"]), ("", []))

    def test_other_store_and_other_company_never_mix(self):
        company = self.store.company
        s2 = make_store("s2", company=company)                     # 同じ会社の別店舗
        other = make_store("x1", company_code="c9")                # 別会社
        own_other = Customer.objects.create(company=company, store=s2.store, display_name="二号店の山本様")
        shared = Customer.objects.create(company=company, store=s2.store, display_name="共有の鈴木様", shared_in_company=True)
        Customer.objects.create(company=other.store.company, store=other.store, display_name="他社の中村様", shared_in_company=True)
        # 共有のお客様が二号店に来店（この店舗の集計には入れない）
        self.pay(self.visit(shared, fx=s2))
        # 共有がオフ: 自店舗の顧客だけ
        d, rows = self.rows()
        self.assertEqual(sorted(rows), ["佐藤様", "田中様"])
        self.assertFalse(d["sharing_enabled"])
        # 共有をオン: 同じ会社で共有フラグの立った顧客だけが増える。他店舗での来店・会計は数えない
        company.customer_sharing_enabled = True
        company.save()
        d, rows = self.rows()
        self.assertEqual(sorted(rows), ["佐藤様", "共有の鈴木様", "田中様"])
        self.assertEqual((rows["共有の鈴木様"]["visits"], D(rows["共有の鈴木様"]["total"]), rows["共有の鈴木様"]["own_store"]), (0, D(0), False))
        self.assertNotIn(own_other.display_name, rows)
        # 共有のお客様がこの店舗に来たら、その分だけ数える
        self.pay(self.visit(shared))
        _, rows = self.rows()
        self.assertEqual((rows["共有の鈴木様"]["visits"], D(rows["共有の鈴木様"]["total"])), (1, D(2500)))
        # 二号店から見ると、二号店の顧客と、二号店での来店だけ
        m2 = add_user(s2, "manager2", store_role="manager")
        _, rows2 = self.rows(client_for(m2, s2.store))
        self.assertEqual(sorted(rows2), ["二号店の山本様", "共有の鈴木様"])
        self.assertEqual(rows2["共有の鈴木様"]["visits"], 1)

    def test_inactive_customer_is_hidden(self):
        self.sato.is_active = False
        self.sato.save()
        _, rows = self.rows()
        self.assertEqual(sorted(rows), ["田中様"])

    def test_permissions_and_csv(self):
        cast = client_for(self.fx.users["cast"], self.store)
        self.assertEqual(cast.get("/api/reports/customers").status_code, 403)
        self.assertEqual(cast.get("/api/reports/export/customers.csv").status_code, 403)
        self.pay(self.visit(self.tanaka))
        owner = client_for(self.fx.users["owner"], self.store)
        lines = owner.get("/api/reports/export/customers.csv").content.decode("utf-8-sig").splitlines()
        self.assertTrue(lines[0].startswith("お客様,登録店舗,ランク,キープ,来店回数"))
        self.assertTrue(any(l.startswith("田中様,店舗s1,C,0,1,1,") for l in lines), lines)

    def test_rank_thresholds_setting_is_validated(self):
        owner = client_for(self.fx.users["owner"], self.store)
        ok = owner.put("/api/store/settings", {"customer_rank_thresholds": [{"label": "B", "min_visits": 5}, {"label": "S", "min_visits": 20}]}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(ok.json()["customer_rank_thresholds"], [{"label": "S", "min_visits": 20}, {"label": "B", "min_visits": 5}])
        for bad in ([{"label": "", "min_visits": 1}], [{"label": "S", "min_visits": -1}], [{"label": "S", "min_visits": 1}, {"label": "S", "min_visits": 2}], "x"):
            self.assertEqual(owner.put("/api/store/settings", {"customer_rank_thresholds": bad}, format="json").status_code, 400, bad)
