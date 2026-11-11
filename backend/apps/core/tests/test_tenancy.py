"""複数会社・複数店舗の境界テスト（必須テスト）。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from apps.core.models import Company, CompanyMembership, IdempotencyRecord, OperatorAccessLog, StoreMembership, User
from apps.core.testing import PASSWORD, add_employee, add_user, client_for, make_store, standard_fixture
from apps.customers.models import Customer
from apps.sales import services
from apps.sales.models import Checkout, Visit

STORE_SCOPED_GET = [
    "/api/floor/state", "/api/visits", "/api/sales/history", "/api/exports/sales.csv", "/api/staff/employees", "/api/customers",
    "/api/inventory/stock", "/api/line/integration", "/api/cash/current", "/api/store/settings", "/api/store/products", "/api/staff/backs",
    "/api/analytics/daily", "/api/reservations", "/api/audit/logs", "/api/receivables",
]


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class TenancyBase(APITestCase):
    def setUp(self):
        self.a1 = standard_fixture("a1", "ca")
        self.a2 = make_store("a2", company=self.a1.company)
        add_user(self.a2, "staff", store_role="staff", username="staff_a2")
        add_employee(self.a2, "cast_a2")
        self.b1 = standard_fixture("b1", "cb")
        self.owner_a = self.a1.users["owner"]
        self.manager_a1 = self.a1.users["manager"]
        self.staff_a1 = self.a1.users["staff"]
        self.staff_a2 = self.a2.users["staff"]
        self.staff_b1 = self.b1.users["staff"]
        self.owner_b = self.b1.users["owner"]

    def visit(self, fx, user=None):
        return services.open_visit(fx.store, user or fx.users.get("manager") or fx.users["staff"], {"table_id": fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": fx.hourly.id}]})

    def checkout(self, fx, visit):
        total = services.quote(visit)[0].total
        return services.confirm_checkout(fx.store, fx.users.get("manager") or fx.users["staff"], visit.id, {"payments": [{"method": "cash", "amount": str(total)}]})


class CrossCompanyTests(TenancyBase):
    def test_b_user_cannot_access_a_store_endpoints(self):
        c = client_for(self.staff_b1, self.a1.store)
        for path in STORE_SCOPED_GET:
            r = c.get(path)
            self.assertEqual(r.status_code, 403, f"{path}: {r.status_code} {r.content[:100]}")
            self.assertEqual(r.json()["error"]["code"], "store_forbidden", path)
        r = c.post("/api/visits", {"table_id": self.a1.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Visit.objects.filter(store=self.a1.store).count(), 0)

    def test_b_owner_cannot_access_a_store_or_company(self):
        c = client_for(self.owner_b, self.a1.store)
        self.assertEqual(c.get("/api/floor/state").status_code, 403)
        self.assertEqual(c.get("/api/store/settings").status_code, 403)
        self.assertEqual(c.put("/api/store/settings", {"nomination_fee": "1"}, format="json").status_code, 403)
        self.assertEqual(c.get(f"/api/companies/{self.a1.company.id}").status_code, 403)
        self.assertEqual(c.get(f"/api/companies/{self.a1.company.id}/stores").status_code, 403)
        self.assertEqual(c.get(f"/api/companies/{self.a1.company.id}/members").status_code, 403)
        self.assertEqual(c.get(f"/api/companies/{self.a1.company.id}/summary").status_code, 403)
        self.assertEqual(c.post(f"/api/companies/{self.a1.company.id}/stores", {"name": "x", "code": "x"}, format="json").status_code, 403)
        self.assertEqual(c.patch(f"/api/companies/{self.a1.company.id}", {"name": "乗っ取り"}, format="json").status_code, 403)
        self.assertEqual(Company.objects.get(id=self.a1.company.id).name, self.a1.company.name)

    def test_unknown_and_missing_store_header(self):
        c = client_for(self.staff_a1, None)
        r = c.get("/api/floor/state")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "store_required")
        c.credentials(HTTP_X_STORE_ID="999999")
        self.assertEqual(c.get("/api/floor/state").status_code, 403)
        c.credentials(HTTP_X_STORE_ID="abc")
        self.assertEqual(c.get("/api/floor/state").status_code, 400)
        anon = APIClient()
        anon.credentials(HTTP_X_STORE_ID=str(self.a1.store.id))
        self.assertEqual(anon.get("/api/floor/state").status_code, 401)

    def test_me_lists_only_own_stores(self):
        stores = {s["id"] for s in client_for(self.staff_b1).get("/api/auth/me").json()["stores"]}
        self.assertEqual(stores, {self.b1.store.id})
        stores = {s["id"] for s in client_for(self.owner_a).get("/api/auth/me").json()["stores"]}
        self.assertEqual(stores, {self.a1.store.id, self.a2.store.id})
        stores = {s["id"] for s in client_for(self.staff_a1).get("/api/auth/me").json()["stores"]}
        self.assertEqual(stores, {self.a1.store.id})
        r = client_for(self.staff_a1).post("/api/auth/select-store", {"store_id": self.b1.store.id}, format="json")
        self.assertEqual(r.status_code, 403)
        r = client_for(self.staff_a1).post("/api/auth/select-store", {"store_id": self.a1.store.id}, format="json")
        self.assertEqual(r.status_code, 200)


class SameCompanyTests(TenancyBase):
    def test_staff_without_membership_cannot_access_other_store(self):
        c = client_for(self.staff_a1, self.a2.store)
        for path in ("/api/floor/state", "/api/visits", "/api/sales/history", "/api/customers", "/api/staff/employees"):
            self.assertEqual(c.get(path).status_code, 403, path)
        c2 = client_for(self.staff_a2, self.a1.store)
        self.assertEqual(c2.get("/api/floor/state").status_code, 403)

    def test_owner_reads_both_own_stores(self):
        for store in (self.a1.store, self.a2.store):
            c = client_for(self.owner_a, store)
            self.assertEqual(c.get("/api/floor/state").status_code, 200)
            self.assertEqual(c.get("/api/store/settings").status_code, 200)
            self.assertEqual(c.get("/api/staff/employees?company=1").status_code, 200)
        self.assertEqual(client_for(self.owner_a, self.b1.store).get("/api/floor/state").status_code, 403)

    def test_store_settings_do_not_mix(self):
        self.a1.settings.service_charge_enabled = True
        self.a1.settings.service_charge_rate = Decimal(10)
        self.a1.settings.save()
        v1 = self.visit(self.a1)
        v2 = self.visit(self.a2, user=self.staff_a2)
        self.assertEqual(services.quote(v1)[0].total, Decimal("2750"))
        self.assertEqual(services.quote(v2)[0].total, Decimal("2500"))
        c1 = client_for(self.owner_a, self.a1.store)
        c2 = client_for(self.owner_a, self.a2.store)
        self.assertTrue(c1.get("/api/store/settings").json()["service_charge_enabled"])
        self.assertFalse(c2.get("/api/store/settings").json()["service_charge_enabled"])
        # 店舗2の設定更新が店舗1に影響しない
        r = c2.put("/api/store/settings", {"nomination_fee": "777"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(c1.get("/api/store/settings").json()["nomination_fee"]), Decimal("1000"))

    def test_slip_numbers_unique_per_store(self):
        v1 = self.visit(self.a1)
        v2 = self.visit(self.a1)
        v3 = self.visit(self.a2, user=self.staff_a2)
        v4 = self.visit(self.b1)
        self.assertEqual((v1.slip_number, v2.slip_number, v3.slip_number, v4.slip_number), (1, 2, 1, 1))
        c1 = self.checkout(self.a1, v1)
        c2 = self.checkout(self.b1, v4)
        self.assertEqual((c1.number, c2.number), (1, 1))

    def test_back_rules_and_closing_do_not_mix(self):
        from apps.staff.models import BackRule
        from datetime import date
        BackRule.objects.create(store=self.a1.store, name="a1", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(50), effective_from=date(2020, 1, 1))
        cast_a2 = self.a2.employees["cast_a2"]
        v2 = self.visit(self.a2, user=self.staff_a2)
        line = services.add_product_line(self.a2.store, self.staff_a2, v2.id, {"product_id": self.a2.products["cast_drink"].id, "quantity": 1, "back_targets": [{"employee_id": cast_a2.id}]})
        self.assertEqual(line.back_entries.count(), 0)  # 店舗1のルールは店舗2に適用されない
        # 日締めは店舗ごと
        c1 = client_for(self.owner_a, self.a1.store)
        c2 = client_for(self.owner_a, self.a2.store)
        c1.post("/api/cash/open", {"opening_float": "1000"}, format="json", **key())
        c2.post("/api/cash/open", {"opening_float": "2000"}, format="json", **key())
        self.checkout(self.a2, v2)
        s1 = c1.get("/api/cash/current").json()["day"]["summary"]
        s2 = c2.get("/api/cash/current").json()["day"]["summary"]
        self.assertEqual(Decimal(s1["cash_sales"]), Decimal("0"))
        self.assertEqual(Decimal(s2["cash_sales"]), Decimal("4000"))
        self.assertEqual(Decimal(s1["theoretical_cash"]), Decimal("1000"))
        self.assertEqual(Decimal(s2["theoretical_cash"]), Decimal("6000"))
        r = c1.post("/api/cash/close", {"counted_cash": "1000", "next_day_float": "1000"}, format="json", **key())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(c2.get("/api/cash/current").json()["day"]["status"], "open")


class IdSwappingTests(TenancyBase):
    def test_other_company_ids_are_rejected(self):
        vb = self.visit(self.b1)
        cb = self.checkout(self.b1, vb)
        vb2 = self.visit(self.b1)
        c = client_for(self.manager_a1, self.a1.store)
        self.assertEqual(c.get(f"/api/visits/{vb.id}").status_code, 404)
        self.assertEqual(c.post(f"/api/visits/{vb2.id}/lines", {"kind": "product", "product_id": self.b1.products["beer"].id}, format="json", **key()).status_code, 404)
        self.assertEqual(c.post(f"/api/visits/{vb2.id}/checkouts", {"payments": [{"method": "cash", "amount": "2500"}]}, format="json", **key()).status_code, 404)
        self.assertEqual(c.get(f"/api/checkouts/{cb.id}").status_code, 404)
        self.assertEqual(c.get(f"/api/checkouts/{cb.id}/handwrite").status_code, 404)
        self.assertEqual(c.post(f"/api/checkouts/{cb.id}/void", {"reason": "x"}, format="json", **key()).status_code, 404)
        self.assertEqual(c.get(f"/api/staff/employees/{self.b1.employees['cast1'].id}").status_code, 404)
        self.assertEqual(c.get(f"/api/store/products/{self.b1.products['beer'].id}").status_code, 404)
        self.assertEqual(c.patch(f"/api/store/products/{self.b1.products['beer'].id}", {"price": "1"}, format="json").status_code, 404)
        self.assertEqual(self.b1.products["beer"].__class__.objects.get(id=self.b1.products["beer"].id).price, Decimal("800"))
        self.assertEqual(Checkout.objects.get(id=cb.id).status, "confirmed")
        self.assertEqual(Visit.objects.get(id=vb2.id).lines.count(), 1)

    def test_related_ids_from_other_company_rejected_on_create(self):
        c = client_for(self.manager_a1, self.a1.store)
        va = self.visit(self.a1)
        # 商品
        r = c.post(f"/api/visits/{va.id}/lines", {"kind": "product", "product_id": self.b1.products["beer"].id}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"]["code"], "validation_error")
        # 卓
        r = c.post("/api/visits", {"table_id": self.b1.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        # コース
        r = c.post("/api/visits", {"table_id": self.a1.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.b1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        # バック対象・担当の従業員（別会社）
        r = c.post(f"/api/visits/{va.id}/lines", {"kind": "product", "product_id": self.a1.products["cast_drink"].id, "back_targets": [{"employee_id": self.b1.employees["cast1"].id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = c.post(f"/api/visits/{va.id}/lines", {"kind": "nomination", "employee_id": self.b1.employees["cast1"].id}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = c.post("/api/visits", {"table_id": self.a1.tables[0].id, "primary_employee_id": self.b1.employees["cast1"].id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        # 席移動先が別会社の卓
        r = c.post(f"/api/visits/{va.id}/move-table", {"table_id": self.b1.tables[1].id}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Visit.objects.get(id=va.id).lines.count(), 1)
        # 商品作成時に別店舗のカテゴリ
        r = c.post("/api/store/products", {"category": self.b1.categories["drink"].id, "name": "x", "price": "100"}, format="json")
        self.assertEqual(r.status_code, 400)
        # 顧客作成時に別会社の担当従業員
        r = c.post("/api/customers", {"display_name": "x", "assigned_employee": self.b1.employees["cast1"].id}, format="json")
        self.assertEqual(r.status_code, 400)
        # 予約で別会社の卓
        r = c.post("/api/reservations", {"guest_name": "x", "reserved_at": "2030-01-01T20:00:00+09:00", "table": self.b1.tables[0].id}, format="json")
        self.assertEqual(r.status_code, 400)
        # 予約→入店で別会社の卓を指定
        r = c.post("/api/reservations", {"guest_name": "y", "reserved_at": "2030-01-01T20:00:00+09:00"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        r = c.post(f"/api/reservations/{r.json()['id']}/seat", {"table_id": self.b1.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        # 別会社の顧客 ID
        cust_b = Customer.objects.create(company=self.b1.company, store=self.b1.store, display_name="B客", shared_in_company=True)
        r = c.post("/api/visits", {"table_id": self.a1.tables[0].id, "customer_id": cust_b.id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        self.assertEqual(c.get(f"/api/customers/{cust_b.id}").status_code, 404)
        self.assertEqual(c.get(f"/api/customers/{cust_b.id}/history").status_code, 404)
        # 同一会社の他店舗の従業員（会社所属なので担当にはできる＝仕様: 従業員は会社所属）
        r = c.post(f"/api/visits/{va.id}/lines", {"kind": "nomination", "employee_id": self.a2.employees["cast_a2"].id}, format="json", **key())
        self.assertEqual(r.status_code, 201)

    def test_customer_sharing_rules(self):
        Company.objects.filter(id__in=[self.a1.company.id, self.b1.company.id]).update(customer_sharing_enabled=True)
        own = Customer.objects.create(company=self.a1.company, store=self.a1.store, display_name="A1客")
        other_shared = Customer.objects.create(company=self.a1.company, store=self.a2.store, display_name="A2共有客", shared_in_company=True)
        other_private = Customer.objects.create(company=self.a1.company, store=self.a2.store, display_name="A2非共有客", shared_in_company=False)
        b_shared = Customer.objects.create(company=self.b1.company, store=self.b1.store, display_name="B共有客", shared_in_company=True)
        c = client_for(self.manager_a1, self.a1.store)
        names = {x["display_name"] for x in c.get("/api/customers").json()}
        self.assertEqual(names, {"A1客", "A2共有客"})
        self.assertNotIn("B共有客", names)
        # 検索でも漏れない
        names = {x["display_name"] for x in c.get("/api/customers?q=客").json()}
        self.assertEqual(names, {"A1客", "A2共有客"})
        # 他店舗の共有顧客は編集不可、参照のみ
        self.assertEqual(c.patch(f"/api/customers/{other_shared.id}", {"memo": "x"}, format="json").status_code, 403)
        self.assertEqual(c.get(f"/api/customers/{other_shared.id}").status_code, 200)
        self.assertEqual(c.get(f"/api/customers/{other_private.id}").status_code, 404)
        self.assertEqual(c.get(f"/api/customers/{b_shared.id}").status_code, 404)
        # 会社が共有を許可していなければ他店舗の顧客は見えない
        Company.objects.filter(id=self.a1.company.id).update(customer_sharing_enabled=False)
        names = {x["display_name"] for x in c.get("/api/customers").json()}
        self.assertEqual(names, {"A1客"})
        # 来店履歴は自店舗分だけ
        self.visit(self.a1, user=self.manager_a1)
        self.assertEqual(c.get(f"/api/customers/{own.id}/history").status_code, 200)


class AggregationAndExportTests(TenancyBase):
    def test_company_summary_only_permitted_stores(self):
        v1 = self.visit(self.a1)
        self.checkout(self.a1, v1)
        v2 = self.visit(self.a2, user=self.staff_a2)
        self.checkout(self.a2, v2)
        vb = self.visit(self.b1)
        self.checkout(self.b1, vb)
        cid = self.a1.company.id
        r = client_for(self.manager_a1).get(f"/api/companies/{cid}/summary")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([s["store_id"] for s in r.json()["stores"]], [self.a1.store.id])
        self.assertEqual(Decimal(r.json()["total"]["total"]), Decimal("2500"))
        r = client_for(self.owner_a).get(f"/api/companies/{cid}/summary")
        self.assertEqual({s["store_id"] for s in r.json()["stores"]}, {self.a1.store.id, self.a2.store.id})
        self.assertEqual(Decimal(r.json()["total"]["total"]), Decimal("5000"))
        self.assertEqual(r.json()["store_count"], 2)
        self.assertEqual(client_for(self.staff_a1).get(f"/api/companies/{cid}/summary").status_code, 403)  # staff にダッシュボード権限なし
        self.assertEqual(client_for(self.owner_b).get(f"/api/companies/{cid}/summary").status_code, 403)
        self.assertEqual(client_for(self.staff_b1).get(f"/api/companies/{cid}/summary").status_code, 403)
        # 停止中の店舗は全店集計から外れない（過去データ保持）が、権限がない店舗は含まれない
        self.a2.store.is_active = False
        self.a2.store.save()
        r = client_for(self.owner_a).get(f"/api/companies/{cid}/summary")
        self.assertEqual({s["store_id"] for s in r.json()["stores"]}, {self.a1.store.id, self.a2.store.id})

    def test_csv_export_permissions_and_scope(self):
        va = self.visit(self.a1)
        self.checkout(self.a1, va)
        vb = self.visit(self.b1)
        self.checkout(self.b1, vb)
        owner = client_for(self.owner_a, self.a1.store)
        r = owner.get("/api/exports/sales.csv")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        body = r.content.decode("utf-8-sig")
        self.assertEqual(body.count("\n"), 2)  # ヘッダ + 自店舗の1件（B社の会計は含まれない）
        for kind in ("payments", "cash", "backs", "attendance", "payroll", "inventory", "purchases"):
            self.assertEqual(owner.get(f"/api/exports/{kind}.csv").status_code, 200, kind)
        staff = client_for(self.staff_a1, self.a1.store)
        self.assertEqual(staff.get("/api/exports/payroll.csv").status_code, 403)
        self.assertEqual(staff.get("/api/exports/sales.csv").status_code, 403)
        self.assertEqual(staff.get("/api/exports/backs.csv").status_code, 403)
        manager = client_for(self.manager_a1, self.a1.store)
        self.assertEqual(manager.get("/api/exports/sales.csv").status_code, 200)
        self.assertEqual(manager.get("/api/exports/payroll.csv").status_code, 403)
        owner_noreauth = client_for(self.owner_a, self.a1.store, reauth=False)
        self.assertEqual(owner_noreauth.get("/api/exports/payroll.csv").status_code, 403)
        self.assertEqual(client_for(self.owner_b, self.a1.store).get("/api/exports/sales.csv").status_code, 403)
        self.assertEqual(owner.get("/api/exports/unknown.csv").status_code, 404)

    def test_analytics_scoped(self):
        self.checkout(self.a1, self.visit(self.a1))
        self.checkout(self.b1, self.visit(self.b1))
        c = client_for(self.owner_a, self.a1.store)
        self.assertEqual(Decimal(c.get("/api/analytics/daily").json()["summary"]["total"]), Decimal("2500"))
        bd = c.get("/api/analytics/breakdown").json()
        self.assertEqual(Decimal(bd["summary"]["total"]), Decimal("2500"))
        self.assertEqual(client_for(self.staff_a1, self.a1.store).get("/api/analytics/daily").status_code, 403)


class RoleTests(TenancyBase):
    def test_cast_cannot_operate_floor(self):
        c = client_for(self.a1.users["cast"], self.a1.store)
        self.assertEqual(c.get("/api/floor/state").status_code, 403)
        self.assertEqual(c.get("/api/visits").status_code, 403)
        self.assertEqual(c.get("/api/sales/history").status_code, 403)
        self.assertEqual(c.get("/api/store/settings").status_code, 403)
        self.assertEqual(c.get("/api/customers").status_code, 403)
        self.assertEqual(c.get("/api/staff/me").status_code, 200)
        caps = next(s for s in c.get("/api/auth/me").json()["stores"] if s["id"] == self.a1.store.id)["caps"]
        self.assertNotIn("floor.operate", caps)

    def test_staff_cannot_change_settings_or_wages(self):
        c = client_for(self.staff_a1, self.a1.store)
        self.assertEqual(c.get("/api/store/settings").status_code, 200)
        self.assertEqual(c.put("/api/store/settings", {"nomination_fee": "1"}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/store/products", {"category": self.a1.categories["drink"].id, "name": "x", "price": "100"}, format="json").status_code, 403)
        self.assertEqual(c.post("/api/staff/employees", {"full_name": "x", "display_name": "x"}, format="json").status_code, 403)
        self.assertEqual(c.get("/api/staff/back-rules").status_code, 403)
        self.assertEqual(c.get("/api/audit/logs").status_code, 403)
        # 店長: 設定変更は再認証が必要
        m = client_for(self.manager_a1, self.a1.store, reauth=False)
        r = m.put("/api/store/settings", {"nomination_fee": "1"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")
        m = client_for(self.manager_a1, self.a1.store)
        r = m.put("/api/store/settings", {"nomination_fee": "1200"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["nomination_fee"]), Decimal("1200"))
        self.assertEqual(m.get("/api/audit/logs?action=settings.update").json()[0]["action"], "settings.update")

    def test_platform_operator_access_is_logged(self):
        op = User.objects.create_user(username="op", password=PASSWORD, is_platform_operator=True)
        c = client_for(op, self.a1.store)
        self.assertEqual(OperatorAccessLog.objects.count(), 0)
        self.assertEqual(c.get("/api/floor/state").status_code, 200)
        self.assertEqual(OperatorAccessLog.objects.filter(user=op, company=self.a1.company, store=self.a1.store).count(), 1)
        self.assertEqual(client_for(op, self.b1.store).get("/api/floor/state").status_code, 200)
        self.assertEqual(OperatorAccessLog.objects.filter(user=op, company=self.b1.company).count(), 1)
        self.assertEqual(c.get(f"/api/companies/{self.b1.company.id}/stores").status_code, 200)
        self.assertEqual(OperatorAccessLog.objects.count(), 3)
        # 運営者は会社オーナーとは別権限: platform API は運営者のみ
        self.assertEqual(c.get("/api/platform/companies").status_code, 200)
        self.assertEqual(client_for(self.owner_a).get("/api/platform/companies").status_code, 403)
        self.assertEqual(client_for(self.owner_a).get("/api/platform/users").status_code, 403)
        self.assertEqual(client_for(self.owner_a).get("/api/platform/access-logs").status_code, 403)

    def test_inactive_store(self):
        self.a1.store.is_active = False
        self.a1.store.save()
        staff = client_for(self.staff_a1, self.a1.store)
        r = staff.get("/api/floor/state")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "store_inactive")
        owner = client_for(self.owner_a, self.a1.store)
        self.assertEqual(owner.get("/api/store/settings").status_code, 200)
        self.assertEqual(owner.get("/api/sales/history").status_code, 200)
        self.assertEqual(owner.get("/api/exports/sales.csv").status_code, 200)
        self.assertEqual(owner.post("/api/visits", {"table_id": self.a1.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.a1.hourly.id}]}, format="json", **key()).status_code, 403)
        self.assertEqual(owner.put("/api/store/settings", {"nomination_fee": "1"}, format="json").status_code, 403)
        me = client_for(self.owner_a).get("/api/auth/me").json()
        s = next(x for x in me["stores"] if x["id"] == self.a1.store.id)
        self.assertTrue(s["read_only"])
        self.assertFalse(s["is_active"])
        # 停止しても過去データは残る
        self.assertEqual(self.a1.store.products.count(), 5)

    def test_inactive_company(self):
        self.a1.company.is_active = False
        self.a1.company.save()
        self.assertEqual(client_for(self.staff_a1, self.a1.store).get("/api/floor/state").status_code, 403)
        owner = client_for(self.owner_a, self.a1.store)
        self.assertEqual(owner.get("/api/sales/history").status_code, 200)
        self.assertEqual(owner.post("/api/cash/open", {"opening_float": "1"}, format="json", **key()).status_code, 403)

    def test_inactive_membership(self):
        StoreMembership.objects.filter(user=self.staff_a1).update(is_active=False)
        self.assertEqual(client_for(self.staff_a1, self.a1.store).get("/api/floor/state").status_code, 403)
        CompanyMembership.objects.filter(user=self.owner_a).update(is_active=False)
        self.assertEqual(client_for(self.owner_a, self.a1.store).get("/api/floor/state").status_code, 403)

    def test_company_management_by_owner(self):
        owner = client_for(self.owner_a)
        cid = self.a1.company.id
        r = owner.post(f"/api/companies/{cid}/stores", {"name": "三号店", "code": "a3"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        sid = r.json()["id"]
        self.assertTrue(client_for(self.owner_a, None).get("/api/auth/me").json()["stores"][0]["id"] in {self.a1.store.id, self.a2.store.id, sid})
        # 招待: 店舗配属は自社の店舗だけ
        r = owner.post(f"/api/companies/{cid}/invitations", {"display_name": "新人", "company_role": "member", "store_roles": [{"store_id": self.b1.store.id, "role": "staff"}]}, format="json")
        self.assertEqual(r.status_code, 400)
        r = owner.post(f"/api/companies/{cid}/invitations", {"display_name": "新人", "company_role": "member", "store_roles": [{"store_id": sid, "role": "staff"}]}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        token = r.json()["token"]
        anon = APIClient()
        r = anon.post("/api/auth/accept-invite", {"token": token, "username": "newbie", "password": "Strong-pass-9876", "display_name": "新人"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual({s["id"] for s in r.json()["stores"]}, {sid})
        self.assertEqual(anon.post("/api/auth/accept-invite", {"token": token, "username": "again", "password": "Strong-pass-9876"}, format="json").status_code, 400)
        # 配属変更: 自社ユーザーのみ
        newbie = User.objects.get(username="newbie")
        r = owner.post(f"/api/companies/{cid}/members", {"user_id": newbie.id, "store_id": self.a1.store.id, "role": "cast"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        r = owner.post(f"/api/companies/{cid}/members", {"user_id": self.staff_b1.id, "store_id": self.a1.store.id, "role": "staff"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = owner.post(f"/api/companies/{cid}/members", {"user_id": newbie.id, "store_id": self.b1.store.id, "role": "staff"}, format="json")
        self.assertEqual(r.status_code, 400)
        # 店舗停止（データは残る）
        r = owner.patch(f"/api/companies/{cid}/stores/{sid}", {"is_active": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["is_active"])
        # 店舗管理者は会社管理できない
        self.assertEqual(client_for(self.manager_a1).post(f"/api/companies/{cid}/stores", {"name": "x", "code": "x9"}, format="json").status_code, 403)


class IdempotencyScopeTests(TenancyBase):
    def test_same_key_different_users_do_not_collide(self):
        va = self.visit(self.a1)
        vb = self.visit(self.b1)
        k = str(uuid.uuid4())
        body = {"payments": [{"method": "cash", "amount": "2500"}]}
        ra = client_for(self.manager_a1, self.a1.store).post(f"/api/visits/{va.id}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=k)
        rb = client_for(self.b1.users["manager"], self.b1.store).post(f"/api/visits/{vb.id}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=k)
        self.assertEqual((ra.status_code, rb.status_code), (201, 201))
        self.assertEqual(IdempotencyRecord.objects.filter(key=k).count(), 2)
        self.assertEqual(Checkout.objects.count(), 2)
