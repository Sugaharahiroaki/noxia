"""T4: キャスト折半（バックの手配分）。販売額とバックは別。検証は staff/backs.py の validate_targets（既存）をテストで固める。"""
from __future__ import annotations

import uuid
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import Product
from apps.core.testing import client_for, standard_fixture
from apps.sales import services
from apps.sales.models import OrderLine, OrderLineBackTarget
from apps.staff.models import BackEntry, BackRule

PAST = timezone.localdate().replace(year=2020)


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        # テスト用: 40,000 円のボトル、ボトルのバックは 10%（切り捨て）。本番の値ではない
        self.bottle = Product.objects.create(store=self.store, category=self.fx.categories["bottle"], name="テスト用シャンパン", price=Decimal(40000))
        self.rule = BackRule.objects.create(store=self.store, name="ボトル 10%", target_type="category_kind", category_kind="bottle", calc_type="percent", value=Decimal(10), effective_from=PAST)
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]}, format="json", **key())
        self.v = r.json()
        self.base_total = Decimal(self.v["quote"]["total"])

    def batch(self, items, **kw):
        return self.c.post(f"/api/visits/{self.v['id']}/lines/batch", {"items": items}, format="json", **(kw or key()))

    def item(self, targets, qty=1, product=None):
        return {"kind": "product", "product_id": (product or self.bottle).id, "quantity": qty, "back_targets": targets}

    def fixed(self, emp, value):
        return {"employee_id": emp.id, "share_type": "fixed", "share_value": value}

    def backs(self, line_id):
        return {b.employee_id: b.amount for b in BackEntry.objects.filter(order_line_id=line_id, status__in=["pending", "confirmed"], reversal_of__isnull=True)}


class SplitBackTests(Base):
    def test_uneven_fixed_split(self):
        r = self.batch([self.item([self.fixed(self.cast1, 200), self.fixed(self.cast2, 3800)])])
        self.assertEqual(r.status_code, 201, r.content)
        line_id = r.json()["created_line_ids"][0]
        self.assertEqual(self.backs(line_id), {self.cast1.id: Decimal(200), self.cast2.id: Decimal(3800)})
        # 販売額は担当が何人いても 1 回だけ（明細 1 行・伝票の合計は 40,000 だけ増える）
        self.assertEqual(OrderLine.objects.filter(visit_id=self.v["id"], product=self.bottle).count(), 1)
        self.assertEqual(OrderLine.objects.get(id=line_id).amount, Decimal(40000))
        self.assertEqual(Decimal(r.json()["quote"]["total"]), self.base_total + Decimal(40000))
        self.assertEqual(Decimal(r.json()["quote"]["breakdown"]["product"]), Decimal(40000))

    def test_sales_counted_once_after_checkout(self):
        r = self.batch([self.item([self.fixed(self.cast1, 200), self.fixed(self.cast2, 3800)])])
        line_id = r.json()["created_line_ids"][0]
        total = Decimal(r.json()["quote"]["total"])
        co = services.confirm_checkout(self.store, self.user, self.v["id"], {"payments": [{"method": "card", "amount": str(total)}]})
        self.assertEqual(co.total, self.base_total + Decimal(40000))
        self.assertEqual(co.subtotal, self.base_total + Decimal(40000))
        # 会計で確定しても、手で配分した額のまま（割合で計算し直さない）
        confirmed = {b.employee_id: (b.amount, b.status) for b in BackEntry.objects.filter(order_line_id=line_id)}
        self.assertEqual(confirmed, {self.cast1.id: (Decimal(200), "confirmed"), self.cast2.id: (Decimal(3800), "confirmed")})

    def test_fixed_total_over_line_amount_is_rejected(self):
        r = self.batch([self.item([self.fixed(self.cast1, 30000), self.fixed(self.cast2, 10001)])])
        self.assertEqual(r.status_code, 400)
        self.assertIn("超えています", r.json()["error"]["message"])
        self.assertFalse(OrderLine.objects.filter(product=self.bottle).exists())
        # ちょうど明細の金額までは通る。数量 2 なら上限も 2 倍
        self.assertEqual(self.batch([self.item([self.fixed(self.cast1, 40000)])]).status_code, 201)
        self.assertEqual(self.batch([self.item([self.fixed(self.cast1, 50000), self.fixed(self.cast2, 30000)], qty=2)]).status_code, 201)

    def test_duplicate_negative_and_bad_type_are_rejected(self):
        bad = [
            [self.fixed(self.cast1, 100), self.fixed(self.cast1, 200)],  # 同じ人の重複
            [self.fixed(self.cast1, -1)],
            [{"employee_id": self.cast1.id, "share_type": "ratio", "share_value": 1}],
            [{"employee_id": self.cast1.id, "share_type": "percent", "share_value": 60}, {"employee_id": self.cast2.id, "share_type": "percent", "share_value": 30}],  # 合計が 100 でない
        ]
        for targets in bad:
            self.assertEqual(self.batch([self.item(targets)]).status_code, 400, targets)
        self.assertFalse(OrderLine.objects.filter(product=self.bottle).exists())
        self.assertFalse(OrderLineBackTarget.objects.exists())

    def test_other_company_employee_is_rejected_same_company_other_store_is_allowed(self):
        other = standard_fixture("s2", company_code="c2")
        r = self.batch([self.item([self.fixed(other.employees["cast1"], 100)])])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(OrderLine.objects.filter(product=self.bottle).exists())
        # 同じ会社の別店舗に配属された人は選べる（複数店舗勤務・応援。従業員は会社単位で照合する既存の設計）
        from apps.core.models import Store
        from apps.staff.models import Employee, EmployeeStoreAssignment

        store2 = Store.objects.create(company=self.fx.company, name="二号店", code="s1b")
        helper = Employee.objects.create(company=self.fx.company, full_name="応援", display_name="応援")
        EmployeeStoreAssignment.objects.create(employee=helper, store=store2, role="cast")
        self.assertEqual(self.batch([self.item([self.fixed(helper, 100)])]).status_code, 201)

    def test_fixed_fraction_follows_rule_rounding(self):
        # 端数はルールの丸め（既定は切り捨て）に従う
        r = self.batch([self.item([self.fixed(self.cast1, "200.9"), self.fixed(self.cast2, "3799.1")])])
        line_id = r.json()["created_line_ids"][0]
        self.assertEqual(self.backs(line_id), {self.cast1.id: Decimal(200), self.cast2.id: Decimal(3799)})
        self.rule.rounding = "half_up"
        self.rule.save()
        r = self.batch([self.item([self.fixed(self.cast1, "200.5")])])
        self.assertEqual(self.backs(r.json()["created_line_ids"][0]), {self.cast1.id: Decimal(201)})

    def test_percent_split_uses_rule_and_does_not_change_sales(self):
        r = self.batch([self.item([{"employee_id": self.cast1.id, "share_type": "percent", "share_value": 50}, {"employee_id": self.cast2.id, "share_type": "percent", "share_value": 50}])])
        line_id = r.json()["created_line_ids"][0]
        self.assertEqual(self.backs(line_id), {self.cast1.id: Decimal(2000), self.cast2.id: Decimal(2000)})  # 40,000 × 10% を半分ずつ
        self.assertEqual(Decimal(r.json()["quote"]["total"]), self.base_total + Decimal(40000))

    def test_same_product_with_different_targets_makes_separate_lines(self):
        drink = self.fx.products["cast_drink"]
        r = self.batch([
            self.item([{"employee_id": self.cast1.id}], qty=1, product=drink),
            self.item([{"employee_id": self.cast2.id}], qty=1, product=drink),
            self.item([], qty=4, product=drink),
        ])
        self.assertEqual(r.status_code, 201, r.content)
        lines = OrderLine.objects.filter(id__in=r.json()["created_line_ids"]).order_by("id")
        self.assertEqual([(l.quantity, l.back_targets.count()) for l in lines], [(Decimal(1), 1), (Decimal(1), 1), (Decimal(4), 0)])
        self.assertEqual(Decimal(r.json()["quote"]["total"]), self.base_total + drink.price * 6)

    def test_batch_replay_does_not_duplicate_lines_or_backs(self):
        k = key()
        items = [self.item([self.fixed(self.cast1, 200), self.fixed(self.cast2, 3800)])]
        r1 = self.batch(items, **k)
        r2 = self.batch(items, **k)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(OrderLine.objects.filter(product=self.bottle).count(), 1)
        self.assertEqual(BackEntry.objects.filter(order_line__product=self.bottle).count(), 2)

    def test_set_back_targets_validates_and_replaces(self):
        r = self.batch([self.item([self.fixed(self.cast1, 4000)])])
        line_id = r.json()["created_line_ids"][0]
        url = f"/api/visits/{self.v['id']}/lines/{line_id}/back-targets"
        self.assertEqual(self.c.put(url, {"back_targets": [self.fixed(self.cast1, 30000), self.fixed(self.cast2, 20000)]}, format="json").status_code, 400)
        self.assertEqual(self.backs(line_id), {self.cast1.id: Decimal(4000)})  # 拒否されたら元のまま
        r = self.c.put(url, {"back_targets": [self.fixed(self.cast1, 200), self.fixed(self.cast2, 3800)]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.backs(line_id), {self.cast1.id: Decimal(200), self.cast2.id: Decimal(3800)})
        self.assertEqual(OrderLine.objects.get(id=line_id).amount, Decimal(40000))  # 販売額は変わらない
