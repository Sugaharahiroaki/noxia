"""M6: 入出金項目（粗利のフラグ）・名称変更・履歴・XLSX／PDF 出力。"""
from __future__ import annotations

import io
import uuid
from decimal import Decimal

from django.test import SimpleTestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from apps.analytics import reports
from apps.cash.models import CashItem, CashMovement
from apps.cash.services import current_open_day, day_summary
from apps.core import terms
from apps.core.business_date import business_date_now
from apps.core.models import AuditLog, TermOverride
from apps.core.testing import client_for, make_store, standard_fixture
from apps.sales import services

D = Decimal


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


class ProfitSideTests(SimpleTestCase):
    def test_without_item_follows_kind(self):
        side = reports.movement_profit_side
        self.assertEqual([side(k, None) for k in ("other_in", "expense", "other_out", "daily_pay", "mid_collection", "float_adjust_in")],
                         ["in", "out", "out", None, None, None])

    def test_item_flag_wins_for_other_in_out(self):
        side = reports.movement_profit_side
        self.assertEqual((side("other_in", False), side("other_out", False), side("other_in", True), side("other_out", True)), (None, None, "in", "out"))
        # 日払い・途中回収・釣銭補充は、フラグがあっても含めない
        self.assertEqual((side("daily_pay", True), side("mid_collection", True), side("float_adjust_in", True)), (None, None, None))


class RelabelTests(SimpleTestCase):
    def test_relabel_replaces_longest_first(self):
        labels = {k: v[0] for k, v in terms.TERMS.items()}
        labels.update({"nomination": "指名A", "jounai": "場内B", "douhan": "アフター"})
        self.assertEqual(terms.relabel("本指名 回数", labels), "指名A 回数")
        self.assertEqual(terms.relabel("場内指名バック／同伴小計", labels), "場内Bバック／アフター小計")
        self.assertEqual(terms.relabel("売上", labels), "売上")


class Base(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.user = self.fx.users["manager"]
        self.c = client_for(self.user, self.store)
        self.owner = client_for(self.fx.users["owner"], self.store, reauth=True)
        self.staff = client_for(self.fx.users["staff"], self.store)
        self.today = business_date_now(self.store)
        self.month = f"{self.today.year}-{self.today.month:02d}"

    def paid_visit(self, table=0, method="cash"):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[table].id, "entries": [{"guest_count": 1, "course_id": self.fx.hourly.id}]})
        total = services.quote(v)[0].total
        p = {"method": method, "amount": str(total)}
        if method == "cash":
            p["tendered"] = str(total)
        return v, services.confirm_checkout(self.store, self.user, v.id, {"payments": [p]})

    def today_row(self):
        return [r for r in self.c.get("/api/reports/monthly", {"month": self.month}).json()["rows"] if r["date"] == self.today.isoformat()][0]


class CashItemTests(Base):
    def item(self, name, direction, affects_profit=True):
        r = self.owner.post("/api/store/cash-items", {"name": name, "direction": direction, "affects_profit": affects_profit}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()

    def move(self, client=None, **body):
        return (client or self.c).post("/api/cash/movements", {"reason": "テスト", **body}, format="json", **key())

    def test_master_permissions_and_store_scope(self):
        it = self.item("両替手数料", "out", affects_profit=False)
        self.assertEqual((it["affects_cash"], it["direction_label"]), (True, "出金"))
        # スタッフは作れない／店長は読める／入金・出金はあとから変えられない／affects_cash は変えられない
        self.assertEqual(self.staff.post("/api/store/cash-items", {"name": "x", "direction": "in"}, format="json").status_code, 403)
        self.assertEqual([x["name"] for x in self.c.get("/api/store/cash-items").json()], ["両替手数料"])
        self.assertEqual(self.owner.patch(f"/api/store/cash-items/{it['id']}", {"direction": "in"}, format="json").status_code, 400)
        r = self.owner.patch(f"/api/store/cash-items/{it['id']}", {"affects_cash": False, "name": "両替"}, format="json")
        self.assertEqual((r.status_code, r.json()["affects_cash"], r.json()["name"]), (200, True, "両替"))
        # 別店舗からは見えない・使えない
        other = standard_fixture("s2", "c2")
        oc = client_for(other.users["manager"], other.store)
        self.assertEqual(oc.get("/api/store/cash-items").json(), [])
        self.assertEqual(oc.post("/api/cash/movements", {"reason": "t", "amount": "100", "item_id": it["id"]}, format="json", **key()).status_code, 400)
        # 削除は停止（過去の記録は残る）。停止した項目では登録できない
        self.assertEqual(self.owner.delete(f"/api/store/cash-items/{it['id']}").status_code, 204)
        self.assertFalse(CashItem.objects.get(id=it["id"]).is_active)
        self.assertEqual(self.move(amount="100", item_id=it["id"]).status_code, 400)

    def test_movement_with_item_sets_kind_and_snapshot(self):
        tip = self.item("チップ預り", "in", affects_profit=False)
        fee = self.item("振込手数料", "out", affects_profit=True)
        r = self.move(amount="3000", item_id=tip["id"])
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual((r.json()["kind"], r.json()["item_name"]), ("other_in", "チップ預り"))
        self.assertEqual(self.move(amount="500", item_id=fee["id"], kind="other_out").status_code, 201)
        # 項目の向きと種別が合わないものは拒否
        self.assertEqual(self.move(amount="500", item_id=fee["id"], kind="expense").status_code, 400)
        mv = CashMovement.objects.get(item_id=tip["id"])
        self.assertEqual((mv.affects_profit, mv.item_name), (False, "チップ預り"))

    def test_profit_uses_item_flag_and_cash_is_unchanged(self):
        tip = self.item("チップ預り", "in", affects_profit=False)
        fee = self.item("振込手数料", "out", affects_profit=True)
        dep = self.item("両替", "out", affects_profit=False)
        self.move(amount="3000", item_id=tip["id"])      # 粗利に含めない入金
        self.move(amount="500", item_id=fee["id"])       # 粗利に含める出金
        self.move(amount="2000", item_id=dep["id"])      # 粗利に含めない出金
        self.move(amount="700", kind="other_in")         # 項目なし → これまでどおり含める
        self.move(amount="1000", kind="expense")         # 項目なし → これまでどおり含める
        r = self.today_row()
        self.assertEqual((D(r["income"]), D(r["expenses"])), (D(700), D(1500)))
        # 現金（理論現金）は種別どおり。項目の有無・フラグでは変わらない
        s = day_summary(current_open_day(self.store))
        self.assertEqual((D(s["cash_in_other"]), D(s["cash_out_other"]), D(s["cash_out_expense"])), (D(3700), D(2500), D(1000)))
        # あとで項目の設定を変えても、登録済みの入出金の扱いは変わらない（登録時の値を残している）
        self.owner.patch(f"/api/store/cash-items/{tip['id']}", {"affects_profit": True, "name": "チップ"}, format="json")
        self.assertEqual(D(self.today_row()["income"]), D(700))
        moves = self.c.get("/api/reports/daily", {"date": self.today.isoformat()}).json()["movements"]
        self.assertEqual([(m["label"], m["in_profit"]) for m in moves],
                         [("チップ預り", False), ("振込手数料", True), ("両替", False), ("その他入金", True), ("現金経費", True)])

    def test_cash_history_summary_and_voided(self):
        fee = self.item("振込手数料", "out")
        self.move(amount="500", item_id=fee["id"])
        self.move(amount="300", item_id=fee["id"])
        gone = self.move(amount="9999", kind="other_out").json()
        r = self.c.post(f"/api/cash/movements/{gone['id']}/void", {"reason": "入力ミス"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.move(amount="700", kind="other_in")
        d = self.c.get("/api/reports/cash-history", {"month": self.month}).json()
        self.assertEqual([(g["direction"], g["label"], g["count"], D(g["amount"]), g["is_item"]) for g in d["summary"]],
                         [("in", "その他入金", 1, D(700), False), ("out", "振込手数料", 2, D(800), True)])
        self.assertEqual((D(d["totals"]["in"]), D(d["totals"]["out"]), D(d["totals"]["profit_out"])), (D(700), D(800), D(800)))
        self.assertEqual(len(d["rows"]), 4)
        self.assertEqual([r["is_voided"] for r in d["rows"]].count(True), 1)
        # 別店舗の入出金は出ない／キャストは見られない
        other = standard_fixture("s2", "c2")
        self.assertEqual(client_for(other.users["manager"], other.store).get("/api/reports/cash-history", {"month": self.month}).json()["rows"], [])
        self.assertEqual(client_for(self.fx.users["cast"], self.store).get("/api/reports/cash-history").status_code, 403)


class TermTests(Base):
    def labels(self, client=None):
        return {t["key"]: t for t in (client or self.c).get("/api/store/terms").json()["terms"]}

    def test_defaults_update_and_reset(self):
        t = self.labels(self.staff)   # 読むのは店舗の誰でも
        self.assertEqual((t["nomination"]["label"], t["nomination"]["overridden"], list(t)), ("本指名", False, list(terms.TERMS)))
        r = self.owner.put("/api/store/terms", {"labels": {"nomination": "指名", "douhan": "同伴", "tag": " ラベル "}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        t = self.labels()
        self.assertEqual((t["nomination"]["label"], t["nomination"]["overridden"], t["douhan"]["overridden"], t["tag"]["label"]), ("指名", True, False, "ラベル"))
        self.assertEqual(TermOverride.objects.filter(store=self.store).count(), 2)   # 初期値と同じものは保存しない
        self.assertTrue(AuditLog.objects.filter(store=self.store, action="terms.update").exists())
        # 空にすると、その用語だけ初期値に戻る
        self.owner.put("/api/store/terms", {"labels": {"tag": ""}}, format="json")
        self.assertEqual(self.labels()["tag"]["label"], "タグ")
        # すべて初期値に戻す
        self.assertEqual(self.owner.delete("/api/store/terms").status_code, 200)
        self.assertFalse(TermOverride.objects.filter(store=self.store).exists())

    def test_validation_permission_and_store_scope(self):
        for bad in ({"unknown": "x"}, {"nomination": "あ" * 13}, {"nomination": "<b>"}, {"nomination": "同伴"}, {"nomination": "指名", "jounai": "指名"}):
            self.assertEqual(self.owner.put("/api/store/terms", {"labels": bad}, format="json").status_code, 400, bad)
        self.assertEqual(self.staff.put("/api/store/terms", {"labels": {"nomination": "指名"}}, format="json").status_code, 403)
        self.assertEqual(self.staff.delete("/api/store/terms").status_code, 403)
        # 再認証していないオーナーは変えられない
        self.assertEqual(client_for(self.fx.users["owner"], self.store, reauth=False).put("/api/store/terms", {"labels": {"nomination": "指名"}}, format="json").status_code, 403)
        # 別店舗には影響しない
        self.owner.put("/api/store/terms", {"labels": {"nomination": "指名"}}, format="json")
        other = standard_fixture("s2", "c2")
        self.assertEqual(self.labels(client_for(other.users["manager"], other.store))["nomination"]["label"], "本指名")


class DeletionHistoryTests(Base):
    def test_cancelled_visits_and_operations(self):
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]})
        line = services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products["beer"].id, "quantity": 1})
        self.assertEqual(self.c.post(f"/api/visits/{v.id}/lines/{line.id}/cancel", {"reason": "注文ミス"}, format="json", **key()).status_code, 200)
        self.assertEqual(self.c.post(f"/api/visits/{v.id}/cancel", {"reason": "入力ミス"}, format="json", **key()).status_code, 200)
        v2, co = self.paid_visit(1)
        self.assertEqual(self.owner.post(f"/api/checkouts/{co.id}/void", {"reason": "金額違い"}, format="json", **key()).status_code, 200)
        d = self.c.get("/api/reports/deletions").json()
        self.assertEqual([(r["public_id"], r["cancel_reason"], r["guest_count"]) for r in d["cancelled"]], [(v.public_id, "入力ミス", 2)])
        logs = [(r["action"], r["public_id"], r["reason"]) for r in d["logs"]]
        self.assertEqual(logs, [("checkout.void", v2.public_id, "金額違い"), ("visit.cancel", v.public_id, "入力ミス"), ("line.cancel", v.public_id, "注文ミス")])
        self.assertEqual((d["logs"][2]["detail"], D(d["logs"][0]["amount"])), ("ビール", D(2500)))
        # 別店舗の履歴は出ない／キャストは見られない
        other = standard_fixture("s2", "c2")
        od = client_for(other.users["manager"], other.store).get("/api/reports/deletions").json()
        self.assertEqual((od["cancelled"], od["logs"]), ([], []))
        self.assertEqual(client_for(self.fx.users["cast"], self.store).get("/api/reports/deletions").status_code, 403)


class ExportTests(Base):
    def sheets(self, resp):
        from openpyxl import load_workbook

        self.assertEqual(resp.status_code, 200, getattr(resp, "content", b"")[:200])
        self.assertIn("spreadsheetml", resp["Content-Type"])
        wb = load_workbook(io.BytesIO(resp.content))
        return {ws.title: [list(r) for r in ws.iter_rows(values_only=True)] for ws in wb.worksheets}

    def test_every_table_has_xlsx(self):
        from apps.sales.models import Tag

        tag = Tag.objects.create(store=self.store, name="呼び")
        v = services.open_visit(self.store, self.user, {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": 2, "course_id": self.fx.hourly.id}]})
        services.set_visit_tags(self.store, self.user, v.id, [tag.id])
        services.add_fee_line(self.store, self.user, v.id, "nomination", {"employee_id": self.fx.employees["cast1"].id})
        services.add_product_line(self.store, self.user, v.id, {"product_id": self.fx.products["beer"].id, "quantity": 2})
        total = services.quote(v)[0].total   # 5,000 + 1,000 + 1,600
        services.confirm_checkout(self.store, self.user, v.id, {"payments": [{"method": "cash", "amount": str(total), "tendered": str(total)}]})
        CashMovement.objects.create(store=self.store, business_day=current_open_day(self.store), kind="expense", amount=D(1000), reason="氷", occurred_at=timezone.now())
        o = self.owner
        slips = self.sheets(o.get("/api/reports/export/slips.xlsx"))["伝票一覧"]
        self.assertEqual(slips[0][:4], ["営業日", "伝票ID", "伝票番号", "卓"])
        self.assertEqual((slips[1][1], slips[1][slips[0].index("合計")], slips[1][slips[0].index("本指名")]), (v.public_id, 7600, "cast1"))   # 金額は数値セル
        self.assertEqual(self.sheets(o.get("/api/reports/export/tags.xlsx"))["タグ集計"][1][1:4], ["呼び", 1, 2])
        items = self.sheets(o.get("/api/reports/export/items.xlsx"))
        self.assertEqual((items["日付×商品"][1][0], items["日付×商品"][1][2], items["キャスト×商品"][0]), ("ビール", 2, ["キャスト", "商品", "個数", "金額"]))
        self.assertEqual(self.sheets(o.get("/api/reports/export/customers.xlsx"))["お客様分析"][0][0], "お客様")
        monthly = self.sheets(o.get("/api/reports/export/monthly.xlsx", {"month": self.month}))["収支（月次）"]
        self.assertEqual((monthly[1][0], monthly[1][1], monthly[1][monthly[0].index("出金（粗利対象）")]), ("合計", 7600, 1000))
        cash = self.sheets(o.get("/api/reports/export/cash.xlsx", {"month": self.month}))["レジ精算（月間）"]
        self.assertEqual((cash[1][0], cash[1][3]), (self.today.isoformat(), 7600))
        hist = self.sheets(o.get("/api/reports/export/cash-history.xlsx", {"month": self.month}))
        self.assertEqual((hist["項目ごとの合計"][1][:4], hist["明細"][1][3]), (["出金", "現金経費", 1, 1000], "現金経費"))
        self.assertEqual(list(self.sheets(o.get("/api/reports/export/deletions.xlsx"))), ["取消の操作", "取消済みの伝票"])
        cast = self.sheets(o.get("/api/reports/export/cast.xlsx", {"month": self.month}))["キャスト集計"]
        self.assertEqual((cast[0][0], cast[1][0], cast[1][cast[0].index("呼び本計")]), ("名前", "合計", 7600))
        self.assertEqual(self.sheets(o.get("/api/reports/export/staff.xlsx", {"month": self.month}))["スタッフ集計"][0][0], "名前")
        self.assertEqual(o.get("/api/reports/export/unknown.xlsx").status_code, 404)
        self.assertTrue(AuditLog.objects.filter(store=self.store, action="export.report.slips.xlsx").exists())

    def test_xlsx_permissions(self):
        cast = client_for(self.fx.users["cast"], self.store)
        for kind in ("slips", "tags", "items", "customers", "deletions", "monthly", "cash", "cash-history", "cast", "staff"):
            self.assertEqual(cast.get(f"/api/reports/export/{kind}.xlsx").status_code, 403, kind)
            self.assertEqual(self.staff.get(f"/api/reports/export/{kind}.xlsx").status_code, 403, kind)
        # キャスト・スタッフ集計は再認証が必要
        no_reauth = client_for(self.fx.users["owner"], self.store, reauth=False)
        self.assertEqual(no_reauth.get("/api/reports/export/cast.xlsx").status_code, 403)
        self.assertEqual(no_reauth.get("/api/reports/export/slips.xlsx").status_code, 200)

    def test_headers_follow_term_overrides(self):
        self.owner.put("/api/store/terms", {"labels": {"nomination": "指名", "jounai": "場内", "tag": "ラベル"}}, format="json")
        head = self.sheets(self.owner.get("/api/reports/export/slips.xlsx"))["伝票一覧"][0]
        self.assertEqual((head[10], head[11], head[12], head[13]), ("ラベル", "指名", "場内", "同伴"))
        cast = self.sheets(self.owner.get("/api/reports/export/cast.xlsx"))["キャスト集計"][0]
        self.assertIn("指名 回数", cast)
        self.assertIn("場内バック", cast)

    def test_pay_statement_pdf(self):
        cast1 = self.fx.employees["cast1"]
        cast2 = self.fx.employees["cast2"]
        r = self.owner.get(f"/api/reports/people/{cast1.id}.pdf", {"month": self.month})
        self.assertEqual((r.status_code, r["Content-Type"], r.content[:5]), (200, "application/pdf", b"%PDF-"))
        self.assertIn("pay_statement_", r["Content-Disposition"])
        # 本人は自分の分だけ。他人の分・別店舗の従業員は見られない
        me = client_for(self.fx.users["cast"], self.store)
        self.assertEqual(me.get(f"/api/reports/people/{cast1.id}.pdf").status_code, 200)
        self.assertEqual(me.get(f"/api/reports/people/{cast2.id}.pdf").status_code, 403)
        self.assertEqual(self.staff.get(f"/api/reports/people/{cast1.id}.pdf").status_code, 403)
        other = make_store("x9", company_code="c9")
        from apps.core.testing import add_employee

        stranger = add_employee(other, "他社の人", role="cast")
        self.assertEqual(self.owner.get(f"/api/reports/people/{stranger.id}.pdf").status_code, 404)
        # 全員分を見られる人でも、再認証していなければ他人の分は出せない
        self.assertEqual(client_for(self.fx.users["owner"], self.store, reauth=False).get(f"/api/reports/people/{cast1.id}.pdf").status_code, 403)
