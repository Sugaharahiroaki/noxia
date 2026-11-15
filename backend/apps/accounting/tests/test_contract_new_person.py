"""キャスト契約の登録と同時に新しい人を登録する／事業者が未登録のときの案内。"""
from django.test import TestCase

from apps.core.models import StoreSettings
from apps.core.testing import client_for, standard_fixture
from apps.staff.models import Employee, EmployeeStoreAssignment

from .helpers import key


class ContractNewPersonTests(TestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.owner = client_for(self.fx.users["owner"], self.fx.store)
        self.manager = client_for(self.fx.users["manager"], self.fx.store, reauth=False)
        self.staff = client_for(self.fx.users["staff"], self.fx.store, reauth=False)

    def post(self, c, data):
        return c.post("/api/accounting/contracts", data, format="json", **key())

    def test_register_new_person_with_contract(self):
        body = {"contract_type": "outsourcing", "start_date": "2026-11-01", "calc_period": "daily",
                "new_employee": {"display_name": "さくら", "full_name": "", "role": "cast"}}
        r = self.post(self.manager, body)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["employee_name"], "さくら")
        emp = Employee.objects.get(display_name="さくら", company=self.fx.company)
        self.assertEqual(emp.full_name, "さくら")  # 氏名が空なら表示名
        self.assertEqual(str(emp.hired_on), "2026-11-01")
        a = EmployeeStoreAssignment.objects.get(employee=emp, store=self.fx.store)
        self.assertEqual(a.role, "cast")
        # 店舗の従業員一覧にも出る（出勤・日払いで選べる）
        names = [e["display_name"] for e in self.manager.get("/api/staff/employees?active=1").json()]
        self.assertIn("さくら", names)

    def test_duplicate_and_missing_name(self):
        existing = self.fx.employees["cast1"].display_name
        r = self.post(self.owner, {"contract_type": "employment", "start_date": "2026-11-01", "new_employee": {"display_name": existing}})
        self.assertEqual(r.status_code, 409, r.content)
        self.assertEqual(r.json()["error"]["code"], "duplicate_employee")
        r = self.post(self.owner, {"contract_type": "employment", "start_date": "2026-11-01", "new_employee": {"display_name": "  "}})
        self.assertEqual(r.status_code, 400)
        r = self.post(self.owner, {"contract_type": "employment", "start_date": "2026-11-01", "new_employee": {"display_name": "ゆき", "role": "boss"}})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Employee.objects.filter(display_name="ゆき").exists())

    def test_contract_validation_failure_does_not_leave_a_person(self):
        # 終了日が開始日より前 → 契約が保存できないので、人も作られない（同じ取引）
        r = self.post(self.owner, {"contract_type": "outsourcing", "start_date": "2026-11-10", "end_date": "2026-11-01",
                                   "new_employee": {"display_name": "あおい"}})
        self.assertEqual(r.status_code, 400, r.content)
        self.assertFalse(Employee.objects.filter(display_name="あおい").exists())

    def test_staff_cannot_register(self):
        r = self.post(self.staff, {"contract_type": "outsourcing", "start_date": "2026-11-01", "new_employee": {"display_name": "れい"}})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Employee.objects.filter(display_name="れい").exists())

    def test_contracts_payload_tells_withholding_setting(self):
        StoreSettings.objects.filter(store=self.fx.store).update(withholding_enabled=False)
        self.assertIs(self.owner.get("/api/accounting/contracts").json()["withholding_enabled"], False)
        StoreSettings.objects.filter(store=self.fx.store).update(withholding_enabled=True)
        self.assertIs(self.owner.get("/api/accounting/contracts").json()["withholding_enabled"], True)

    def test_missing_taxpayer_message_is_about_the_business_not_withholding(self):
        r = self.owner.get("/api/accounting/pl")
        self.assertEqual(r.status_code, 409)
        err = r.json()["error"]
        self.assertEqual(err["code"], "taxpayer_required")
        self.assertIn("法人または個人事業主", err["message"])
        self.assertNotIn("源泉徴収義務者", err["message"])
