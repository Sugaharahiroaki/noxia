"""店舗タブレット用アカウント（メニューの出し分け）の設定。"""
from __future__ import annotations

from rest_framework.test import APIClient, APITestCase

from apps.core import tablet
from apps.core.models import AuditLog, CompanyMembership, StoreMembership, User
from apps.core.testing import client_for, standard_fixture


class TabletMenuUnitTests(APITestCase):
    def test_clean_and_resolve(self):
        self.assertEqual(tablet.clean_menu(["/sales", "/floor", "/floor"]), ["/floor", "/sales"])  # 並びは一覧の順、重複は除く
        with self.assertRaises(Exception):
            tablet.clean_menu(["/nowhere"])
        fx = standard_fixture()
        self.assertEqual(tablet.resolve_menu(fx.store), tablet.DEFAULT_TABLET_MENU)   # 未設定は既定
        fx.store.settings.tablet_menu = ["/sales", "/x", 3]
        fx.store.settings.save()
        self.assertEqual(tablet.resolve_menu(fx.store), ["/sales"])                    # 形の合わないものは捨てる


class TabletSettingsTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store, reauth=True)
        self.manager_sm = StoreMembership.objects.get(user=self.fx.users["manager"], store=self.store)

    def me(self, user):
        return client_for(user, self.store).get("/api/auth/me").json()["stores"][0]

    def test_me_shows_tablet_flag_and_menu(self):
        self.assertFalse(self.me(self.fx.users["manager"])["is_tablet"])
        StoreMembership.objects.filter(id=self.manager_sm.id).update(is_tablet=True)
        s = self.me(self.fx.users["manager"])
        self.assertEqual((s["is_tablet"], s["tablet_menu"]), (True, tablet.DEFAULT_TABLET_MENU))
        # オーナー（会社権限でのアクセス）はタブレット用にならない
        self.assertFalse(self.me(self.fx.users["owner"])["is_tablet"])

    def test_get_and_permissions(self):
        d = self.owner.get("/api/store/tablet").json()
        self.assertEqual(sorted(m["username"] for m in d["members"]), sorted(self.fx.users[k].username for k in ("manager", "staff", "cast")))
        self.assertEqual((d["menu"], d["is_default"], d["default_menu"]), (tablet.DEFAULT_TABLET_MENU, True, tablet.DEFAULT_TABLET_MENU))
        self.assertTrue(any(i["key"] == "/settings/pos" and i["group"] == "設定" for i in d["items"]))
        self.assertEqual(client_for(self.fx.users["cast"], self.store).get("/api/store/tablet").status_code, 403)
        # 変更は再認証が必要
        self.assertEqual(client_for(self.fx.users["owner"], self.store, reauth=False).put("/api/store/tablet", {"menu": ["/floor"]}, format="json").status_code, 403)

    def test_put_members_and_menu(self):
        r = self.owner.put("/api/store/tablet", {"tablet_membership_ids": [self.manager_sm.id], "menu": ["/sales", "/floor"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual([m["is_tablet"] for m in d["members"] if m["id"] == self.manager_sm.id], [True])
        self.assertEqual((d["menu"], d["is_default"]), (["/floor", "/sales"], False))
        s = self.me(self.fx.users["manager"])
        self.assertEqual((s["is_tablet"], s["tablet_menu"]), (True, ["/floor", "/sales"]))
        log = AuditLog.objects.filter(store=self.store, action="settings.tablet").latest("id")
        self.assertEqual(log.changes["tablet_accounts"]["after"], [self.fx.users["manager"].display_name])
        # 印を外す／既定に戻す
        r = self.owner.put("/api/store/tablet", {"tablet_membership_ids": [], "menu": None}, format="json")
        d = r.json()
        self.assertFalse(any(m["is_tablet"] for m in d["members"]))
        self.assertTrue(d["is_default"])
        self.assertFalse(self.me(self.fx.users["manager"])["is_tablet"])

    def test_validation_and_store_scope(self):
        self.assertEqual(self.owner.put("/api/store/tablet", {"menu": ["/nowhere"]}, format="json").status_code, 400)
        self.assertEqual(self.owner.put("/api/store/tablet", {"menu": "x"}, format="json").status_code, 400)
        other = standard_fixture("s2", "c2")
        other_sm = StoreMembership.objects.get(user=other.users["manager"], store=other.store)
        self.assertEqual(self.owner.put("/api/store/tablet", {"tablet_membership_ids": [other_sm.id]}, format="json").status_code, 400)
        self.assertFalse(StoreMembership.objects.get(id=other_sm.id).is_tablet)
        # 別店舗のオーナーからは、この店舗の配属は見えない
        oc = client_for(other.users["owner"], other.store, reauth=True)
        self.assertNotIn("manager", [m["username"] for m in oc.get("/api/store/tablet").json()["members"] if m["user_id"] == self.fx.users["manager"].id])


class TabletAccountCreateTests(APITestCase):
    """PC でログイン ID とパスワードを決めて、タブレット用アカウントをその場で作る（招待リンクを使わない）。"""

    PASSWORD = "Tablet-pass-2026"

    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store, reauth=True)

    def create(self, client=None, **body):
        data = {"username": "rivon-tablet", "password": self.PASSWORD, "display_name": "店のタブレット", **body}
        return (client or self.owner).post("/api/store/tablet/accounts", data, format="json")

    def test_create_then_login_with_that_id_and_password(self):
        r = self.create()
        self.assertEqual(r.status_code, 201, r.content)
        m = [x for x in r.json()["members"] if x["username"] == "rivon-tablet"]
        self.assertEqual([(x["role"], x["is_tablet"], x["display_name"]) for x in m], [("manager", True, "店のタブレット")])
        # そのままログインでき、タブレット用（営業の画面だけ）になっている
        c = APIClient()
        self.assertEqual(c.post("/api/auth/login", {"username": "rivon-tablet", "password": self.PASSWORD}, format="json").status_code, 200)
        s = c.get("/api/auth/me").json()["stores"]
        self.assertEqual([(x["id"], x["is_tablet"], x["role"]) for x in s], [(self.store.id, True, "manager")])
        u = User.objects.get(username="rivon-tablet")
        self.assertEqual(CompanyMembership.objects.get(user=u).role, "member")   # 会社の権限は一般（全店の管理はできない）
        self.assertTrue(AuditLog.objects.filter(store=self.store, action="settings.tablet.account_create").exists())

    def test_default_display_name_and_validation(self):
        r = self.create(display_name="")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(User.objects.get(username="rivon-tablet").display_name, f"{self.store.name} タブレット")
        self.assertEqual(self.create().status_code, 400)                                   # 同じ ID は作れない
        self.assertEqual(self.create(username="t2", password="123").status_code, 400)       # 弱いパスワードは不可
        self.assertEqual(self.create(username="", password=self.PASSWORD).status_code, 400)
        self.assertFalse(User.objects.filter(username="t2").exists())

    def test_only_company_owner_with_reauth(self):
        # 店舗管理者は店舗設定を変えられても、アカウントは作れない
        mgr = client_for(self.fx.users["manager"], self.store, reauth=True)
        self.assertEqual(self.create(client=mgr).status_code, 403)
        self.assertEqual(self.create(client=client_for(self.fx.users["cast"], self.store, reauth=True)).status_code, 403)
        # オーナーでも再認証が必要
        self.assertEqual(self.create(client=client_for(self.fx.users["owner"], self.store, reauth=False)).status_code, 403)
        self.assertFalse(User.objects.filter(username="rivon-tablet").exists())

    def test_other_store_owner_cannot_create_here(self):
        other = standard_fixture("s2", "c2")
        # 別会社のオーナーが X-Store-Id をこの店舗にしても拒否される
        oc = client_for(other.users["owner"], self.store, reauth=True)
        r = oc.post("/api/store/tablet/accounts", {"username": "x-tablet", "password": self.PASSWORD}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(username="x-tablet").exists())
        # 自分の店舗で作れば、自分の店舗にだけ配属される
        r = client_for(other.users["owner"], other.store, reauth=True).post(
            "/api/store/tablet/accounts", {"username": "x-tablet", "password": self.PASSWORD}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(list(StoreMembership.objects.filter(user__username="x-tablet").values_list("store_id", flat=True)), [other.store.id])

    def test_idempotent_double_tap(self):
        key = {"HTTP_IDEMPOTENCY_KEY": "tablet-create-1"}
        body = {"username": "rivon-tablet", "password": self.PASSWORD}
        r1 = self.owner.post("/api/store/tablet/accounts", body, format="json", **key)
        r2 = self.owner.post("/api/store/tablet/accounts", body, format="json", **key)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertEqual(User.objects.filter(username="rivon-tablet").count(), 1)

    def test_change_password(self):
        sm_id = [x["id"] for x in self.create().json()["members"] if x["username"] == "rivon-tablet"][0]
        url = f"/api/store/tablet/accounts/{sm_id}/password"
        self.assertEqual(client_for(self.fx.users["manager"], self.store, reauth=True).post(url, {"password": "New-tablet-pass-9"}, format="json").status_code, 403)
        self.assertEqual(self.owner.post(url, {"password": "1"}, format="json").status_code, 400)
        self.assertEqual(self.owner.post(url, {"password": "New-tablet-pass-9"}, format="json").status_code, 200)
        self.assertTrue(User.objects.get(username="rivon-tablet").check_password("New-tablet-pass-9"))
        # タブレット用でない配属・別店舗の配属は変えられない
        mgr_sm = StoreMembership.objects.get(user=self.fx.users["manager"], store=self.store)
        self.assertEqual(self.owner.post(f"/api/store/tablet/accounts/{mgr_sm.id}/password", {"password": "New-tablet-pass-9"}, format="json").status_code, 404)
        other = standard_fixture("s2", "c2")
        other_sm = StoreMembership.objects.get(user=other.users["manager"], store=other.store)
        StoreMembership.objects.filter(id=other_sm.id).update(is_tablet=True)
        self.assertEqual(self.owner.post(f"/api/store/tablet/accounts/{other_sm.id}/password", {"password": "New-tablet-pass-9"}, format="json").status_code, 404)
        self.assertFalse(other.users["manager"].__class__.objects.get(id=other.users["manager"].id).check_password("New-tablet-pass-9"))
