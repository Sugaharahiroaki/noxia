"""店舗タブレット用アカウント（メニューの出し分け）の設定。"""
from __future__ import annotations

from rest_framework.test import APIClient, APITestCase

from apps.core import permissions, tablet
from apps.core.models import AuditLog, CompanyMembership, StoreMembership, StoreSettings, User
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
        # 経理の画面はパソコンだけ（保存できない・前に保存したものは捨てる）
        with self.assertRaises(Exception):
            tablet.clean_menu(["/accounting/dashboard"])
        fx.store.settings.tablet_menu = ["/sales", "/accounting/contracts"]
        fx.store.settings.save()
        self.assertEqual(tablet.resolve_menu(fx.store), ["/sales"])

    def test_default_ops_are_the_former_manager_caps(self):
        # 既定のままなら、これまでのタブレット用アカウント（店舗管理者）と同じ操作
        default = tablet.TABLET_BASE_CAPS.union(*(c for k, _l, c, _d in tablet.TABLET_OPS if k in tablet.DEFAULT_TABLET_OPS))
        self.assertEqual(default, permissions.MANAGER_CAPS)
        # いつも持つ操作に営業の画面に要るものがあり、付け外しの一覧と重ならない
        self.assertTrue({permissions.CAP_FLOOR, permissions.CAP_CLOSING, permissions.CAP_SALES_VIEW, permissions.CAP_SETTINGS_VIEW} <= tablet.TABLET_BASE_CAPS)
        self.assertFalse(tablet.TABLET_BASE_CAPS & tablet.TABLET_TOGGLE_CAPS)
        # オーナーだけの操作（会社の管理・操作履歴・経営者本人の税務判断）は付けられない
        never = {permissions.CAP_COMPANY_MANAGE, permissions.CAP_AUDIT_VIEW, permissions.CAP_ACCOUNTING_VIEW} | permissions.OWNER_ONLY_CAPS
        self.assertFalse(never & (tablet.TABLET_BASE_CAPS | tablet.TABLET_TOGGLE_CAPS))
        with self.assertRaises(Exception):
            tablet.clean_ops(["company"])
        self.assertEqual(tablet.clean_ops(["wages", "sales_void", "wages"]), ["sales_void", "wages"])


class TabletSettingsTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store)
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
        # 変更は会社オーナーだけ（スタッフ・店舗管理者は不可）。パスワードの聞き直しはない
        self.assertTrue(d["can_edit"])
        self.assertEqual(client_for(self.fx.users["staff"], self.store).put("/api/store/tablet", {"menu": ["/floor"]}, format="json").status_code, 403)
        mgr = client_for(self.fx.users["manager"], self.store)
        self.assertFalse(mgr.get("/api/store/tablet").json()["can_edit"])
        for body in ({"menu": ["/floor"]}, {"ops": []}, {"tablet_membership_ids": [self.manager_sm.id]}):
            self.assertEqual(mgr.put("/api/store/tablet", body, format="json").status_code, 403, body)
        self.assertFalse(StoreMembership.objects.get(id=self.manager_sm.id).is_tablet)
        self.assertIsNone(StoreSettings.objects.get(store=self.store).tablet_ops)

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
        oc = client_for(other.users["owner"], other.store)
        self.assertNotIn("manager", [m["username"] for m in oc.get("/api/store/tablet").json()["members"] if m["user_id"] == self.fx.users["manager"].id])


class TabletOpsTests(APITestCase):
    """店舗設定「権限」のタブレットの列: チェックした操作だけがタブレットの API の権限になる。"""

    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store)
        self.tablet_user = self.fx.users["manager"]
        self.sm = StoreMembership.objects.get(user=self.tablet_user, store=self.store)
        StoreMembership.objects.filter(id=self.sm.id).update(is_tablet=True)

    def caps(self, user=None):
        return set(client_for(user or self.tablet_user, self.store).get("/api/auth/me").json()["stores"][0]["caps"])

    def put_ops(self, ops):
        return self.owner.put("/api/store/tablet", {"ops": ops}, format="json")

    def test_default_is_unchanged(self):
        self.assertEqual(self.caps(), set(permissions.MANAGER_CAPS))
        d = self.owner.get("/api/store/tablet").json()
        self.assertEqual((d["ops"], d["ops_is_default"]), (tablet.DEFAULT_TABLET_OPS, True))
        self.assertEqual([i["key"] for i in d["ops_items"]], tablet.TABLET_OP_KEYS)
        self.assertTrue(d["owner_only"])
        self.assertFalse(any(i["tablet"] for i in d["items"] if i["group"] == "経理"))

    def test_unchecked_ops_are_refused_by_api(self):
        r = self.put_ops([o for o in tablet.DEFAULT_TABLET_OPS if o != "settings_edit"])
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()["ops_is_default"], "settings_edit" in r.json()["ops"]), (False, False))
        caps = self.caps()
        self.assertNotIn(permissions.CAP_SETTINGS_EDIT, caps)
        self.assertIn(permissions.CAP_SETTINGS_VIEW, caps)
        tc = client_for(self.tablet_user, self.store)
        self.assertEqual(tc.get("/api/store/settings").status_code, 200)            # 見るだけはできる
        self.assertEqual(tc.put("/api/store/settings", {"settings_note": "x"}, format="json").status_code, 403)
        self.assertEqual(tc.put("/api/printing/config", {"enabled": True}, format="json").status_code, 403)
        log = AuditLog.objects.filter(store=self.store, action="settings.tablet").latest("id")
        self.assertIsNone(log.changes["tablet_ops"]["before"])
        # 戻すと変えられる
        self.assertEqual(self.put_ops(None).json()["ops_is_default"], True)
        self.assertEqual(tc.put("/api/store/settings", {"settings_note": "x"}, format="json").status_code, 200)

    def test_all_off_keeps_floor_and_owner_only_can_be_granted(self):
        self.put_ops([])
        caps = self.caps()
        self.assertTrue({permissions.CAP_FLOOR, permissions.CAP_CLOSING, permissions.CAP_SALES_VIEW} <= caps)   # 営業はいつもできる
        self.assertFalse(caps & tablet.TABLET_TOGGLE_CAPS)
        # これまでオーナーだけだった操作も、チェックすればタブレットに付く
        self.put_ops(["wages", "back_rules_edit"])
        caps = self.caps()
        self.assertTrue({permissions.CAP_WAGES_EDIT, permissions.CAP_WAGES_VIEW, permissions.CAP_BACK_RULES_EDIT} <= caps)
        self.assertNotIn(permissions.CAP_SALES_VOID, caps)

    def test_only_tablet_accounts_are_affected(self):
        self.put_ops([])
        # タブレット用でない店舗管理者・会社オーナーは変わらない
        self.assertEqual(self.caps(self.fx.users["owner"]), set(permissions.OWNER_CAPS))
        StoreMembership.objects.filter(id=self.sm.id).update(is_tablet=False)
        self.assertEqual(self.caps(), set(permissions.MANAGER_CAPS))

    def test_tablet_sees_but_cannot_change_its_own_permissions(self):
        tc = client_for(self.tablet_user, self.store)
        d = tc.get("/api/store/tablet").json()
        self.assertEqual((d["can_edit"], d["ops"]), (False, tablet.DEFAULT_TABLET_OPS))
        self.assertEqual(tc.put("/api/store/tablet", {"ops": tablet.TABLET_OP_KEYS}, format="json").status_code, 403)
        self.assertEqual(tc.put("/api/store/tablet", {"menu": tablet.TABLET_MENU_KEYS}, format="json").status_code, 403)
        # 店舗設定の保存では、タブレットの画面・操作は変わらない（受け取らない）
        self.put_ops(["sales_void", "settings_edit"])
        r = tc.put("/api/store/settings", {"tablet_ops": tablet.TABLET_OP_KEYS, "tablet_menu": tablet.TABLET_MENU_KEYS}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        st = StoreSettings.objects.get(store=self.store)
        self.assertEqual((st.tablet_ops, st.tablet_menu), (["sales_void", "settings_edit"], []))
        self.assertNotIn("tablet_ops", r.json())

    def test_validation_and_other_store(self):
        self.assertEqual(self.put_ops(["nope"]).status_code, 400)
        self.assertEqual(self.put_ops("sales_void").status_code, 400)
        self.assertEqual(self.owner.put("/api/store/tablet", {"menu": ["/accounting/dashboard"]}, format="json").status_code, 400)
        # 別会社のオーナーが X-Store-Id をこの店舗にしても変えられない
        other = standard_fixture("s2", "c2")
        oc = client_for(other.users["owner"], self.store)
        self.assertEqual(oc.put("/api/store/tablet", {"ops": []}, format="json").status_code, 403)
        self.assertIsNone(StoreSettings.objects.get(store=self.store).tablet_ops)
        # 自分の店舗の設定は、この店舗のタブレットに効かない
        self.assertEqual(client_for(other.users["owner"], other.store).put("/api/store/tablet", {"ops": []}, format="json").status_code, 200)
        self.assertEqual(self.caps(), set(permissions.MANAGER_CAPS))


class TabletAccountCreateTests(APITestCase):
    """PC でログイン ID とパスワードを決めて、タブレット用アカウントをその場で作る（招待リンクを使わない）。"""

    PASSWORD = "Tablet-pass-2026"

    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.owner = client_for(self.fx.users["owner"], self.store)

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

    def test_only_company_owner(self):
        # 店舗管理者は店舗設定を変えられても、アカウントは作れない
        mgr = client_for(self.fx.users["manager"], self.store)
        self.assertEqual(self.create(client=mgr).status_code, 403)
        self.assertEqual(self.create(client=client_for(self.fx.users["cast"], self.store)).status_code, 403)
        self.assertFalse(User.objects.filter(username="rivon-tablet").exists())

    def test_other_store_owner_cannot_create_here(self):
        other = standard_fixture("s2", "c2")
        # 別会社のオーナーが X-Store-Id をこの店舗にしても拒否される
        oc = client_for(other.users["owner"], self.store)
        r = oc.post("/api/store/tablet/accounts", {"username": "x-tablet", "password": self.PASSWORD}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertFalse(User.objects.filter(username="x-tablet").exists())
        # 自分の店舗で作れば、自分の店舗にだけ配属される
        r = client_for(other.users["owner"], other.store).post(
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
        self.assertEqual(client_for(self.fx.users["manager"], self.store).post(url, {"password": "New-tablet-pass-9"}, format="json").status_code, 403)
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
