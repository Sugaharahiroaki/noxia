"""指名・同伴のバック（店舗共通）の簡単な設定 API: 1 回ごと（固定額・料金の%）と会計の%、営業日での切り替え、権限・別店舗。"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.core.business_date import current_business_date
from apps.core.models import AuditLog, TermOverride
from apps.core.testing import client_for, standard_fixture
from apps.sales import services
from apps.staff.models import BackEntry, BackRule

D = Decimal
URL = "/api/staff/designation-backs"


class DesignationBacksTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.manager = self.fx.users["manager"]
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.today = current_business_date(self.store)
        self.cast1 = self.fx.employees["cast1"]
        self.cast2 = self.fx.employees["cast2"]

    def rows(self, client=None):
        r = (client or self.owner).get(URL)
        self.assertEqual(r.status_code, 200, r.content)
        return {row["target"]: row for row in r.json()["backs"]}

    def put(self, backs, client=None):
        return (client or self.owner).put(URL, {"backs": backs}, format="json")

    def visit(self, table=0):
        return services.open_visit(self.store, self.manager, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]})

    def pay(self, v):
        total = services.quote(v)[0].total
        return services.confirm_checkout(self.store, self.manager, v.id, {"payments": [{"method": "card", "amount": str(total)}]})

    def test_empty_store_shows_none(self):
        rows = self.rows()
        self.assertEqual(list(rows), ["yobi", "douhan", "nomination", "jounai", "yobi_subtotal", "douhan_subtotal", "nomination_subtotal", "jounai_subtotal"])
        self.assertTrue(all(r["mode"] == "none" and r["rule_id"] is None for r in rows.values()))
        self.assertEqual((rows["nomination"]["kind"], rows["douhan_subtotal"]["kind"]), ("per_unit", "subtotal"))

    def test_fixed_per_time_and_percent_of_bill_are_used_by_backs(self):
        r = self.put([
            {"target": "nomination", "mode": "fixed", "value": "1000"},
            {"target": "douhan", "mode": "fixed", "value": "2000"},
            {"target": "nomination_subtotal", "mode": "percent", "value": "10", "subtotal_basis": "set_extension", "split_mode": "full_each", "include_discount": True},
        ])
        self.assertEqual(r.status_code, 200, r.content)
        rows = {row["target"]: row for row in r.json()["backs"]}
        self.assertEqual((rows["nomination"]["mode"], D(rows["nomination"]["value"])), ("fixed", D(1000)))
        self.assertEqual((rows["nomination_subtotal"]["subtotal_basis"], rows["nomination_subtotal"]["split_mode"]), ("set_extension", "full_each"))
        self.assertEqual(rows["jounai"]["mode"], "none")
        rule = BackRule.objects.get(target_type="nomination")
        self.assertIsNone(rule.employee)
        self.assertEqual(rule.effective_from, self.today)
        self.assertIn("バック", rule.name)

        v = self.visit()                                                                # セット 2,500 × 2
        nom1 = services.add_fee_line(self.store, self.manager, v.id, "nomination", {"employee_id": self.cast1.id})
        services.add_fee_line(self.store, self.manager, v.id, "nomination", {"employee_id": self.cast2.id})
        dh = services.add_fee_line(self.store, self.manager, v.id, "douhan", {"employee_id": self.cast1.id})
        self.assertEqual(BackEntry.objects.get(order_line=nom1).amount, D(1000))
        self.assertEqual(BackEntry.objects.get(order_line=dh).amount, D(2000))
        co = self.pay(v)
        subtotal_backs = BackEntry.objects.filter(checkout=co, kind="yobi_subtotal").order_by("employee_id")
        self.assertEqual([(b.basis_amount, b.amount) for b in subtotal_backs], [(D(5000), D(500)), (D(5000), D(500))])   # セットだけ・それぞれ全額

    def test_name_follows_renamed_term(self):
        TermOverride.objects.create(store=self.store, key="nomination", label="指名")
        self.assertEqual(self.put([{"target": "nomination", "mode": "fixed", "value": "500"}, {"target": "yobi_subtotal", "mode": "percent", "value": "5"}]).status_code, 200)
        self.assertEqual(BackRule.objects.get(target_type="nomination").name, "指名バック（店舗共通）")
        self.assertEqual(BackRule.objects.get(target_type="yobi_subtotal").name, "呼びのある会計の%バック（店舗共通）")

    def test_omitted_detail_keeps_the_current_rule_detail(self):
        self.put([{"target": "douhan_subtotal", "mode": "percent", "value": "5", "subtotal_basis": "total", "split_mode": "full_each", "include_discount": False}])
        self.put([{"target": "douhan_subtotal", "mode": "percent", "value": "8"}])
        r = BackRule.objects.get(target_type="douhan_subtotal")
        self.assertEqual((r.value, r.subtotal_basis, r.split_mode, r.include_discount), (D(8), "total", "full_each", False))

    def test_yobi_back_set_from_the_list_is_used(self):
        self.fx.settings.yobi_fee = D(0)
        self.fx.settings.save()
        self.put([{"target": "yobi", "mode": "fixed", "value": "1000"}])
        v = self.visit()
        line = services.add_fee_line(self.store, self.manager, v.id, "yobi", {"employee_id": self.cast1.id})
        self.assertEqual(BackEntry.objects.get(order_line=line).amount, D(1000))

    def test_change_on_a_later_day_keeps_the_old_rule_for_the_past(self):
        old = BackRule.objects.create(store=self.store, name="昔の同伴", target_type="douhan", calc_type="fixed", value=D(1000),
                                      effective_from=self.today - timedelta(days=30), rounding="ceil")
        self.assertEqual(self.rows()["douhan"]["rule_id"], old.id)
        self.assertEqual(self.put([{"target": "douhan", "mode": "fixed", "value": "1500"}]).status_code, 200)
        old.refresh_from_db()
        self.assertEqual(old.effective_to, self.today - timedelta(days=1))              # 過去の分は前のルールのまま
        self.assertEqual(old.value, D(1000))
        new = BackRule.objects.get(target_type="douhan", effective_from=self.today)
        self.assertEqual((new.value, new.rounding), (D(1500), "ceil"))                  # 細かい条件は引き継ぐ
        self.assertEqual(self.rows()["douhan"]["rule_id"], new.id)

    def test_same_day_change_rewrites_and_same_value_does_nothing(self):
        self.put([{"target": "nomination", "mode": "percent", "value": "50"}])
        self.put([{"target": "nomination", "mode": "percent", "value": "40"}])
        self.assertEqual(BackRule.objects.filter(target_type="nomination").count(), 1)
        self.assertEqual(BackRule.objects.get(target_type="nomination").value, D(40))
        logs = AuditLog.objects.filter(action="back_rule.designation_update").count()
        self.put([{"target": "nomination", "mode": "percent", "value": "40.00"}])
        self.assertEqual(AuditLog.objects.filter(action="back_rule.designation_update").count(), logs)   # 変わらないなら記録しない

    def test_none_stops_the_common_rule_but_not_personal_ones(self):
        common = BackRule.objects.create(store=self.store, name="共通", target_type="nomination", calc_type="fixed", value=D(1000),
                                         effective_from=self.today - timedelta(days=10))
        personal = BackRule.objects.create(store=self.store, employee=self.cast2, name="個人", target_type="nomination", calc_type="fixed",
                                           value=D(3000), effective_from=self.today - timedelta(days=10))
        self.assertEqual(self.rows()["nomination"]["personal_rules"], 1)
        self.assertEqual(self.put([{"target": "nomination", "mode": "none"}]).status_code, 200)
        common.refresh_from_db()
        personal.refresh_from_db()
        self.assertEqual(common.effective_to, self.today - timedelta(days=1))
        self.assertIsNone(personal.effective_to)
        v = self.visit()
        l1 = services.add_fee_line(self.store, self.manager, v.id, "nomination", {"employee_id": self.cast1.id})
        l2 = services.add_fee_line(self.store, self.manager, v.id, "nomination", {"employee_id": self.cast2.id})
        self.assertFalse(BackEntry.objects.filter(order_line=l1).exists())               # 共通はもう付かない
        self.assertEqual(BackEntry.objects.get(order_line=l2).amount, D(3000))            # 個人別はそのまま
        self.assertEqual(self.rows()["nomination"]["mode"], "none")

    def test_unlisted_targets_are_left_alone(self):
        self.put([{"target": "douhan_subtotal", "mode": "percent", "value": "5"}])
        self.put([{"target": "nomination", "mode": "fixed", "value": "1000"}])
        self.assertEqual(self.rows()["douhan_subtotal"]["mode"], "percent")

    def test_validation(self):
        bad = [
            [{"target": "nope", "mode": "fixed", "value": "1"}],
            [{"target": "nomination", "mode": "bonus", "value": "1"}],
            [{"target": "nomination", "mode": "fixed", "value": "-1"}],
            [{"target": "nomination", "mode": "fixed", "value": "10.5"}],
            [{"target": "nomination", "mode": "fixed", "value": ""}],
            [{"target": "nomination", "mode": "percent", "value": "101"}],
            [{"target": "nomination", "mode": "percent", "value": "abc"}],
            [{"target": "nomination_subtotal", "mode": "percent", "value": "5", "subtotal_basis": "x"}],
            [{"target": "nomination_subtotal", "mode": "percent", "value": "5", "split_mode": "x"}],
            [{"target": "nomination", "mode": "fixed", "value": "1"}, {"target": "nomination", "mode": "none"}],
        ]
        for backs in bad:
            self.assertEqual(self.put(backs).status_code, 400, backs)
        self.assertEqual(self.owner.put(URL, {"backs": "x"}, format="json").status_code, 400)
        self.assertFalse(BackRule.objects.exists())

    def test_failed_item_saves_nothing(self):
        r = self.put([{"target": "nomination", "mode": "fixed", "value": "1000"}, {"target": "douhan", "mode": "percent", "value": "120"}])
        self.assertEqual(r.status_code, 400)
        self.assertFalse(BackRule.objects.exists())

    def test_permissions(self):
        manager = client_for(self.manager, self.store)
        self.assertEqual(manager.get(URL).status_code, 200)                              # 店長は見るだけ
        self.assertEqual(self.put([{"target": "nomination", "mode": "fixed", "value": "1"}], client=manager).status_code, 403)
        staff = client_for(self.fx.users["staff"], self.store)
        self.assertEqual(staff.get(URL).status_code, 403)
        no_reauth = client_for(self.fx.users["owner"], self.store, reauth=False)
        r = self.put([{"target": "nomination", "mode": "fixed", "value": "1"}], client=no_reauth)
        self.assertEqual((r.status_code, r.json()["error"]["code"]), (403, "reauth_required"))
        self.assertFalse(BackRule.objects.exists())

    def test_other_store_is_rejected_and_untouched(self):
        other = standard_fixture("s2", "c2")
        BackRule.objects.create(store=other.store, name="他店", target_type="nomination", calc_type="fixed", value=D(700), effective_from=self.today)
        cross = client_for(self.fx.users["owner"], other.store)                           # 別の会社の店舗の ID を送る
        self.assertEqual(cross.get(URL).status_code, 403)
        self.assertEqual(self.put([{"target": "nomination", "mode": "none"}], client=cross).status_code, 403)
        self.put([{"target": "nomination", "mode": "fixed", "value": "1000"}])
        self.assertEqual(BackRule.objects.get(store=other.store).value, D(700))
        self.assertTrue(BackRule.objects.get(store=other.store).is_active)
        other_rows = {row["target"]: row for row in client_for(other.users["owner"], other.store).get(URL).json()["backs"]}
        self.assertEqual(D(other_rows["nomination"]["value"]), D(700))
