"""バック計算（固定額・割合・個人別・具体性・複数人分配・確定時点・値引き按分・打消し・設定変更の不変）。"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.catalog.models import Product
from apps.core.testing import client_for, standard_fixture
from apps.sales import services
from apps.sales.models import OrderLine, Receivable
from apps.staff.models import BackEntry, BackRule

PAST = date(2020, 1, 1)


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class BackBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]
        self.p = self.fx.products

    def rule(self, **kw):
        base = dict(store=self.store, name="r", calc_type="percent", value=Decimal(50), effective_from=PAST)
        base.update(kw)
        return BackRule.objects.create(**base)

    def visit(self):
        return services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})

    def product_line(self, visit, product, qty=1, targets=None):
        targets = targets if targets is not None else [{"employee_id": self.cast1.id}]
        return services.add_product_line(self.store, self.user, visit.id, {"product_id": product.id, "quantity": qty, "back_targets": targets})

    def entries(self, line):
        return list(BackEntry.objects.filter(order_line=line).order_by("id"))


class RuleResolutionTests(BackBase):
    def test_fixed_rule_for_nomination(self):
        self.rule(target_type="nomination", calc_type="fixed", value=Decimal(500))
        v = self.visit()
        line = services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.cast1.id, "quantity": 2})
        e = self.entries(line)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0].amount, Decimal("1000"))  # 固定額 × 数量
        self.assertEqual(e[0].kind, "nomination")
        self.assertEqual(e[0].status, "pending")

    def test_percent_rule_on_category_kind(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"], qty=2)
        e = self.entries(line)
        self.assertEqual(e[0].amount, Decimal("1500"))
        self.assertEqual(e[0].basis_amount, Decimal("3000"))

    def test_no_matching_rule_creates_nothing(self):
        self.rule(target_type="category_kind", category_kind="bottle", value=Decimal(10))
        v = self.visit()
        line = self.product_line(v, self.p["beer"])
        self.assertEqual(self.entries(line), [])

    def test_individual_rule_overrides_common(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(60), employee=self.cast1)
        v = self.visit()
        l1 = self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast1.id}])
        l2 = self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast2.id}])
        self.assertEqual(self.entries(l1)[0].amount, Decimal("900"))
        self.assertEqual(self.entries(l2)[0].amount, Decimal("750"))
        self.assertTrue(self.entries(l1)[0].rule_snapshot["employee_specific"])
        self.assertFalse(self.entries(l2)[0].rule_snapshot["employee_specific"])

    def test_individual_rule_not_applied_to_other_employee(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(60), employee=self.cast1)
        v = self.visit()
        l2 = self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast2.id}])
        self.assertEqual(self.entries(l2), [])

    def test_specificity_product_over_category_over_kind_over_all(self):
        self.rule(target_type="all_products", value=Decimal(5))
        self.rule(target_type="category_kind", category_kind="drink", value=Decimal(10))
        self.rule(target_type="category", category=self.fx.categories["drink"], value=Decimal(20))
        self.rule(target_type="product", product=self.p["beer"], value=Decimal(30))
        highball = Product.objects.create(store=self.store, category=self.fx.categories["drink"], name="ハイボール", price=Decimal(1000))
        v = self.visit()
        self.assertEqual(self.entries(self.product_line(v, self.p["beer"]))[0].amount, Decimal("240"))       # product 30% of 800
        self.assertEqual(self.entries(self.product_line(v, highball))[0].amount, Decimal("200"))             # category 20% of 1000
        self.assertEqual(self.entries(self.product_line(v, self.p["food"]))[0].amount, Decimal("25"))        # all_products 5% of 500
        # カテゴリ種別のみ一致する商品
        BackRule.objects.filter(target_type="category").update(is_active=False)
        self.assertEqual(self.entries(self.product_line(v, highball))[0].amount, Decimal("100"))             # kind 10%

    def test_effective_dates(self):
        today = timezone.localdate()
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), effective_from=today + timedelta(days=1))
        v = self.visit()
        self.assertEqual(self.entries(self.product_line(v, self.p["cast_drink"])), [])
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(40), effective_from=PAST, effective_to=today - timedelta(days=1))
        self.assertEqual(self.entries(self.product_line(v, self.p["cast_drink"])), [])
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(30), effective_from=today)
        self.assertEqual(self.entries(self.product_line(v, self.p["cast_drink"]))[0].amount, Decimal("450"))

    def test_basis_tax_excluded(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), basis="tax_excluded")
        v = self.visit()
        e = self.entries(self.product_line(v, self.p["cast_drink"]))[0]
        self.assertEqual(e.basis_amount, Decimal("1364"))  # 1500 − floor(1500×10/110)=136
        self.assertEqual(e.amount, Decimal("682"))

    def test_rounding_mode(self):
        self.rule(target_type="category_kind", category_kind="food", value=Decimal("33.3"), rounding="ceil")
        v = self.visit()
        e = self.entries(self.product_line(v, self.p["food"]))[0]
        self.assertEqual(e.amount, Decimal("167"))  # 500×0.333=166.5 → ceil


class SplitTests(BackBase):
    def test_percent_split_two_employees(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"], qty=2, targets=[{"employee_id": self.cast1.id, "share_value": 70}, {"employee_id": self.cast2.id, "share_value": 30}])
        amounts = {e.employee_id: e.amount for e in self.entries(line)}
        self.assertEqual(amounts[self.cast1.id], Decimal("1050"))  # 3000×50%×70%
        self.assertEqual(amounts[self.cast2.id], Decimal("450"))
        self.assertEqual(sum(amounts.values()), Decimal("1500"))  # 二重付与なし

    def test_fixed_share_overrides_rule(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast1.id, "share_type": "fixed", "share_value": 300}])
        self.assertEqual(self.entries(line)[0].amount, Decimal("300"))

    def test_invalid_splits_rejected(self):
        from apps.core.exceptions import ApiError
        v = self.visit()
        with self.assertRaises(ApiError):
            self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast1.id, "share_value": 60}, {"employee_id": self.cast2.id, "share_value": 60}])
        with self.assertRaises(ApiError):
            self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast1.id}, {"employee_id": self.cast1.id}])
        with self.assertRaises(ApiError):
            self.product_line(v, self.p["cast_drink"], targets=[{"employee_id": self.cast1.id, "share_type": "fixed", "share_value": 99999}])
        self.assertEqual(BackEntry.objects.count(), 0)

    def test_change_targets_reverses_and_recreates(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        first = self.entries(line)[0]
        services.set_back_targets(self.store, self.user, v.id, line.id, [{"employee_id": self.cast2.id}])
        first.refresh_from_db()
        self.assertEqual(first.status, "reversed")
        live = [e for e in self.entries(line) if e.status == "pending"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0].employee_id, self.cast2.id)


class TimingTests(BackBase):
    def _checkout(self, v, method="cash"):
        totals, _ = services.quote(v)
        return services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": method, "amount": str(totals.total), "debtor_name": "x"}]})

    def test_timing_order_confirms_immediately(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="order")
        v = self.visit()
        e = self.entries(self.product_line(v, self.p["cast_drink"]))[0]
        self.assertEqual(e.status, "confirmed")
        self.assertIsNotNone(e.confirmed_at)

    def test_timing_checkout(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="checkout")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        self.assertEqual(self.entries(line)[0].status, "pending")
        co = self._checkout(v)
        e = self.entries(line)[0]
        self.assertEqual(e.status, "confirmed")
        self.assertEqual(e.checkout_id, co.id)
        self.assertEqual(e.business_date, co.business_date)

    def test_timing_payment_waits_for_receivable(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="payment")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        co = self._checkout(v, method="receivable")
        e = self.entries(line)[0]
        self.assertEqual(e.status, "pending")
        self.assertEqual(e.checkout_id, co.id)
        rec = Receivable.objects.get(checkout=co)
        services.collect_receivable(self.store, self.user, rec.id, {"amount": "1000", "method": "cash"})
        e.refresh_from_db()
        self.assertEqual(e.status, "pending")
        services.collect_receivable(self.store, self.user, rec.id, {"amount": str(rec.amount - Decimal(1000)), "method": "cash"})
        e.refresh_from_db()
        self.assertEqual(e.status, "confirmed")

    def test_timing_payment_without_receivable_confirms_at_checkout(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="payment")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        self._checkout(v)
        self.assertEqual(self.entries(line)[0].status, "confirmed")


class ProrationAndReversalTests(BackBase):
    def _checkout(self, v):
        totals, _ = services.quote(v)
        return services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": "cash", "amount": str(totals.total)}]})

    def test_include_discount_prorates_at_checkout(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), include_discount=True)
        v = self.visit()  # course 2500
        line = self.product_line(v, self.p["cast_drink"], qty=2)  # 3000
        services.add_discount(self.store, self.user, v.id, {"amount": 1000, "reason": "x"})
        self.assertEqual(self.entries(line)[0].amount, Decimal("1500"))  # 注文時は値引き未反映
        self._checkout(v)  # gross 5500, net 4500 → ratio 0.818181
        e = self.entries(line)[0]
        self.assertEqual(e.status, "confirmed")
        self.assertEqual(e.basis_amount, Decimal("2454"))  # floor(3000 × 0.818181)
        self.assertEqual(e.amount, Decimal("1227"))

    def test_exclude_discount(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), include_discount=False)
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"], qty=2)
        services.add_discount(self.store, self.user, v.id, {"amount": 1000, "reason": "x"})
        self._checkout(v)
        self.assertEqual(self.entries(line)[0].amount, Decimal("1500"))

    def test_include_service_charge(self):
        self.fx.settings.service_charge_enabled = True
        self.fx.settings.service_charge_rate = Decimal(10)
        self.fx.settings.save()
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), include_service_charge=True)
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        self._checkout(v)
        e = self.entries(line)[0]
        self.assertEqual(e.basis_amount, Decimal("1650"))
        self.assertEqual(e.amount, Decimal("825"))

    def test_cancel_confirmed_line_creates_negative_reversal(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="order")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        services.cancel_line(self.store, self.user, v.id, line.id, "誤注文")
        e = self.entries(line)
        self.assertEqual(len(e), 2)
        self.assertEqual(e[0].status, "confirmed")
        self.assertEqual(e[1].amount, Decimal("-750"))
        self.assertEqual(e[1].reversal_of_id, e[0].id)
        self.assertEqual(e[1].status, "confirmed")
        self.assertIn("誤注文", e[1].reason)
        self.assertEqual(sum(x.amount for x in e if x.status == "confirmed"), Decimal("0"))

    def test_cancel_pending_line_marks_reversed_without_negative(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="checkout")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        services.cancel_line(self.store, self.user, v.id, line.id, "x")
        e = self.entries(line)
        self.assertEqual(len(e), 1)
        self.assertEqual(e[0].status, "reversed")

    def test_void_checkout_reverses_and_recheckout_recomputes(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50))
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        co = self._checkout(v)
        services.void_checkout(self.store, self.user, co.id, "取消")
        e = self.entries(line)
        self.assertEqual(len(e), 2)
        self.assertEqual([x.amount for x in e], [Decimal("750"), Decimal("-750")])
        self.assertTrue(all(x.status == "confirmed" for x in e))
        co2 = self._checkout(v)
        e = self.entries(line)
        self.assertEqual(len(e), 3)
        self.assertEqual(e[2].checkout_id, co2.id)
        self.assertEqual(e[2].amount, Decimal("750"))
        # 集計（確定分の合計）は 750 のまま、履歴は 3 件
        total = sum(x.amount for x in BackEntry.objects.filter(employee=self.cast1, status="confirmed"))
        self.assertEqual(total, Decimal("750"))
        # 二重打消しされない
        services.void_checkout(self.store, self.user, co2.id, "再取消")
        self.assertEqual(len(self.entries(line)), 4)
        self.assertEqual(sum(x.amount for x in BackEntry.objects.filter(employee=self.cast1, status="confirmed")), Decimal("0"))

    def test_rule_change_after_confirmation_does_not_alter_entries(self):
        rule = self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="order")
        v = self.visit()
        line = self.product_line(v, self.p["cast_drink"])
        e = self.entries(line)[0]
        self.assertEqual(e.amount, Decimal("750"))
        rule.value = Decimal(90)
        rule.save()
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal("750"))
        self.assertEqual(e.rule_snapshot["value"], "50.00")
        # 新しい注文は新しい率
        line2 = self.product_line(v, self.p["cast_drink"])
        self.assertEqual(self.entries(line2)[0].amount, Decimal("1350"))
        # 会計しても確定済みの金額は再計算されない（timing=order）
        self._checkout(v)
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal("750"))


class BackApiTests(BackBase):
    def test_adjustment_requires_rules_edit_cap(self):
        staff = client_for(self.fx.users["staff"], self.store)
        r = staff.post("/api/staff/backs/adjust", {"employee_id": self.cast1.id, "amount": "1000", "reason": "調整"}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        owner = client_for(self.fx.users["owner"], self.store)
        r = owner.post("/api/staff/backs/adjust", {"employee_id": self.cast1.id, "amount": "1000"}, format="json", **key())
        self.assertEqual(r.status_code, 400)
        r = owner.post("/api/staff/backs/adjust", {"employee_id": self.cast1.id, "amount": "-300", "reason": "過払い調整"}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["kind"], "adjustment")
        self.assertEqual(r.json()["status"], "confirmed")
        self.assertEqual(Decimal(r.json()["amount"]), Decimal("-300"))
        # 再認証なしでは拒否
        owner_noreauth = client_for(self.fx.users["owner"], self.store, reauth=False)
        r = owner_noreauth.post("/api/staff/backs/adjust", {"employee_id": self.cast1.id, "amount": "1", "reason": "x"}, format="json", **key())
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "reauth_required")

    def test_back_entries_summary(self):
        self.rule(target_type="category_kind", category_kind="cast_drink", value=Decimal(50), confirm_timing="order")
        v = self.visit()
        self.product_line(v, self.p["cast_drink"], qty=2, targets=[{"employee_id": self.cast1.id, "share_value": 50}, {"employee_id": self.cast2.id, "share_value": 50}])
        r = self.c.get("/api/staff/backs")
        self.assertEqual(r.status_code, 200)
        summary = {s["employee_id"]: s for s in r.json()["summary"]}
        self.assertEqual(Decimal(summary[self.cast1.id]["confirmed"]), Decimal("750"))
        self.assertEqual(Decimal(summary[self.cast2.id]["confirmed"]), Decimal("750"))
        self.assertEqual(len(r.json()["entries"]), 2)
        # キャストは全員分を見られない
        cast = client_for(self.fx.users["cast"], self.store)
        self.assertEqual(cast.get("/api/staff/backs").status_code, 403)

    def test_back_rule_crud_permissions(self):
        body = {"name": "test", "target_type": "category_kind", "category_kind": "cast_drink", "calc_type": "percent", "value": "50", "effective_from": "2020-01-01"}
        staff = client_for(self.fx.users["staff"], self.store)
        self.assertEqual(staff.get("/api/staff/back-rules").status_code, 403)
        manager = self.c
        self.assertEqual(manager.get("/api/staff/back-rules").status_code, 200)
        self.assertEqual(manager.post("/api/staff/back-rules", body, format="json").status_code, 403)
        owner = client_for(self.fx.users["owner"], self.store)
        r = owner.post("/api/staff/back-rules", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        bad = dict(body, calc_type="percent", value="150")
        self.assertEqual(owner.post("/api/staff/back-rules", bad, format="json").status_code, 400)
        bad = dict(body, target_type="product")
        self.assertEqual(owner.post("/api/staff/back-rules", bad, format="json").status_code, 400)
        rid = r.json()["id"]
        r = owner.delete(f"/api/staff/back-rules/{rid}")
        self.assertEqual(r.status_code, 204)
        self.assertFalse(BackRule.objects.get(id=rid).is_active)
