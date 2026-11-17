"""店舗タブレット用アカウント（メニューの出し分け）の設定。"""
from __future__ import annotations

from rest_framework.test import APITestCase

from apps.core import tablet
from apps.core.models import AuditLog, StoreMembership
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
