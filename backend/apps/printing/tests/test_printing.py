"""レシートプリンター: 概算伝票（いま／延長したら）・会計伝票・領収書・精算伝票・設定・別店舗の拒否・印刷データ。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from rest_framework.test import APITestCase

from apps.catalog.models import CourseExtensionOption, Product
from apps.cash.models import BusinessDay
from apps.core.models import AuditLog
from apps.core.testing import client_for, make_store, standard_fixture
from apps.printing.models import PrinterConfig
from apps.printing.slip import Slip, pair_lines, plain_lines, to_epos_xml, width
from apps.sales.models import Checkout, ReceiptIssue


def key():
    return {"HTTP_IDEMPOTENCY_KEY": str(uuid.uuid4())}


def texts(slip: dict) -> str:
    return "\n".join(f"{r['left']} {r['right']}".strip() for r in slip["rows"])


class PrintBase(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.store = self.fx.store
        self.c = client_for(self.fx.users["manager"], self.store)
        CourseExtensionOption.objects.create(course=self.fx.hourly, minutes=30, price=Decimal(2000), sort_order=1)
        CourseExtensionOption.objects.create(course=self.fx.hourly, minutes=60, price=Decimal(3500), sort_order=2)

    def open_visit(self, guests=2, course=None, beers=1):
        course = course or self.fx.hourly
        r = self.c.post("/api/visits", {"table_id": self.fx.tables[0].id, "entries": [{"guest_count": guests, "course_id": course.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        v = r.json()
        if beers:
            r = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": beers}, format="json", **key())
            self.assertEqual(r.status_code, 201, r.content)
        return v

    def checkout(self, visit_id, payments):
        r = self.c.post(f"/api/visits/{visit_id}/checkouts", {"payments": payments}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()


class EstimateTests(PrintBase):
    def test_tiers_now_and_each_extension_option(self):
        v = self.open_visit()  # セット 2,500×2 + ビール 800 = 5,800（内税・サービス料なし）
        r = self.c.get(f"/api/visits/{v['id']}/print/estimate")
        self.assertEqual(r.status_code, 200, r.content)
        tiers = r.json()["tiers"]
        self.assertEqual([t["minutes"] for t in tiers], [0, 30, 60])
        self.assertEqual([Decimal(t["total"]) for t in tiers], [Decimal(5800), Decimal(9800), Decimal(12800)])
        end0 = datetime.fromisoformat(tiers[0]["planned_end_at"])
        self.assertEqual(datetime.fromisoformat(tiers[1]["planned_end_at"]) - end0, timedelta(minutes=30))
        self.assertEqual(datetime.fromisoformat(tiers[2]["planned_end_at"]) - end0, timedelta(minutes=60))
        body = texts(r.json()["slip"])
        self.assertIn("いまお会計の場合", body)
        self.assertIn("30分 延長した場合", body)
        self.assertIn("60分 延長した場合", body)
        self.assertIn("¥9,800", body)
        # 見積は何も記録しない
        self.assertEqual(Checkout.objects.count(), 0)

    def test_extension_tier_equals_total_after_real_extension(self):
        """サービス料・税の計算は会計と同じ（延長したあとの見積と一致する）。"""
        st = self.fx.settings
        st.service_charge_enabled = True
        st.service_charge_rate = Decimal("20")
        st.save()
        v = self.open_visit(guests=3, beers=2)
        tiers = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["tiers"]
        entry_id = self.c.get(f"/api/visits/{v['id']}").json()["course_entries"][0]["id"]
        opt30 = CourseExtensionOption.objects.get(course=self.fx.hourly, minutes=30)
        r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry_id}/extend", {"option_id": opt30.id}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal(tiers[1]["total"]))

    def test_two_sets_extend_together_and_are_named(self):
        """途中から合流したお客様のセットも、同じ分数の候補があれば一緒に延長した金額。セットが 1 つのときは名前を出さない。"""
        v = self.open_visit(beers=0)  # セット 2,500×2 = 5,000
        one = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["tiers"]
        self.assertEqual(one[1]["extended_entries"], [])
        r = self.c.post(f"/api/visits/{v['id']}/course-entries", {"guest_count": 1, "course_id": self.fx.hourly.id}, format="json", **key())
        self.assertIn(r.status_code, (200, 201), r.content)
        tiers = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["tiers"]
        # いま 2,500×3 = 7,500。30 分延長: 2,000×2 + 2,000×1 を足す
        self.assertEqual([Decimal(t["total"]) for t in tiers[:2]], [Decimal(7500), Decimal(13500)])
        self.assertEqual(len(tiers[1]["extended_entries"]), 2)

    def test_payment_method_rates_show_cash_and_card(self):
        st = self.fx.settings
        st.service_charge_enabled = True
        st.service_charge_rate = Decimal("20")
        st.service_charge_rate_card = Decimal("25")
        st.save()
        v = self.open_visit()
        r = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()
        bm = r["tiers"][0]["by_method"]
        self.assertGreater(Decimal(bm["card"]), Decimal(bm["cash"]))
        self.assertIn("カードでのお支払い", texts(r["slip"]))

    def test_unlimited_course_has_only_now(self):
        v = self.open_visit(course=self.fx.unlimited)
        tiers = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["tiers"]
        self.assertEqual([t["minutes"] for t in tiers], [0])

    def test_course_without_options_uses_saved_extension(self):
        CourseExtensionOption.objects.all().delete()
        v = self.open_visit()
        tiers = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["tiers"]
        self.assertEqual([(t["minutes"], Decimal(t["total"])) for t in tiers], [(0, Decimal(5800)), (60, Decimal(10800))])

    def test_printable_follows_printer_settings(self):
        v = self.open_visit()
        self.assertFalse(self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["printable"])
        PrinterConfig.objects.update_or_create(store=self.store, defaults={"enabled": True, "print_estimate": True})
        r = self.c.get(f"/api/visits/{v['id']}/print/estimate").json()
        self.assertTrue(r["printable"])
        self.assertTrue(r["epos_xml"].startswith('<epos-print xmlns="http://www.epson-pos.com/schemas/2011/03/epos-print">'))
        self.assertIn('<text lang="ja"', r["epos_xml"])
        self.assertIn('<cut type="feed"/>', r["epos_xml"])
        PrinterConfig.objects.filter(store=self.store).update(print_estimate=False)
        self.assertFalse(self.c.get(f"/api/visits/{v['id']}/print/estimate").json()["printable"])

    def test_closed_visit_is_refused(self):
        v = self.open_visit()
        self.checkout(v["id"], [{"method": "cash", "amount": "5800", "tendered": "10000"}])
        self.assertEqual(self.c.get(f"/api/visits/{v['id']}/print/estimate").status_code, 409)


class ReceiptTests(PrintBase):
    def test_receipt_prints_confirmed_amounts(self):
        PrinterConfig.objects.create(store=self.store, enabled=True, shop_name="NOXIA 本店", phone="06-0000-0000", invoice_number="T1234567890123", receipt_message="またのお越しを")
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "cash", "amount": "5800", "tendered": "10000"}])
        r = self.c.get(f"/api/checkouts/{co['id']}/print/receipt")
        self.assertEqual(r.status_code, 200, r.content)
        body = texts(r.json()["slip"])
        for s in ("NOXIA 本店", "登録番号 T1234567890123", "ビール", "合計 ¥5,800", "10%対象 ¥5,800（内消費税 ¥527）", "お預かり ¥10,000", "お釣り ¥4,200", "またのお越しを"):
            self.assertIn(s, body)
        self.assertTrue(r.json()["printable"])

    def test_receipt_shows_discount_service_and_card(self):
        st = self.fx.settings
        st.service_charge_enabled = True
        st.service_charge_rate = Decimal("10")
        st.save()
        v = self.open_visit()
        self.c.post(f"/api/visits/{v['id']}/lines/batch", {"items": [{"kind": "discount", "amount": "800", "reason": "常連"}]}, format="json", **key())
        q = self.c.post(f"/api/visits/{v['id']}/quote", {"method": "card"}, format="json").json()
        co = self.checkout(v["id"], [{"method": "card", "amount": q["total"]}])
        body = texts(self.c.get(f"/api/checkouts/{co['id']}/print/receipt").json()["slip"])
        self.assertIn("値引き -¥800", body)
        self.assertIn("サービス料（10%）", body)
        self.assertIn(f"カード ¥{int(Decimal(q['total'])):,}", body)


class RyoshushoTests(PrintBase):
    def test_issue_and_reissue(self):
        PrinterConfig.objects.create(store=self.store, enabled=True)
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "cash", "amount": "5800", "tendered": "6000"}])
        r = self.c.post(f"/api/checkouts/{co['id']}/print/ryoshusho", {"addressee": "株式会社テスト"}, format="json", **key())
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertFalse(d["issued_before"])
        body = texts(d["slip"])
        for s in ("領 収 書", "株式会社テスト 様", "¥5,800-", "但し お飲食代として", "現金 ¥5,800"):
            self.assertIn(s, body)
        self.assertNotIn("再発行", body)
        self.assertEqual(AuditLog.objects.filter(action="print.ryoshusho", target_id=str(co["id"])).count(), 1)
        # 発行すると、売上・会計履歴の「発行済」と同じ記録（印刷）が付く
        issue = ReceiptIssue.objects.get(checkout_id=co["id"])
        self.assertEqual((issue.method, issue.amount, issue.addressee, issue.proviso, issue.reissue), ("printed", Decimal("5800"), "株式会社テスト", "お飲食代として", False))
        r2 = self.c.post(f"/api/checkouts/{co['id']}/print/ryoshusho", {"addressee": "株式会社テスト", "proviso": "ご宴会代として"}, format="json", **key())
        self.assertTrue(r2.json()["issued_before"])
        self.assertIn("（再発行）", texts(r2.json()["slip"]))
        self.assertIn("但し ご宴会代として", texts(r2.json()["slip"]))
        self.assertEqual(list(ReceiptIssue.objects.filter(checkout_id=co["id"]).values_list("reissue", flat=True)), [False, True])
        row = next(c for c in self.c.get("/api/sales/history").json()["checkouts"] if c["id"] == co["id"])
        self.assertEqual([(x["method_label"], x["reissue"]) for x in row["receipt_issues"]], [("印刷", False), ("印刷", True)])

    def test_preview_does_not_record_and_handwritten_counts(self):
        PrinterConfig.objects.create(store=self.store, enabled=True)
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "cash", "amount": "5800", "tendered": "6000"}])
        # 下見（GET）では記録しない
        self.assertFalse(self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").json()["issued_before"])
        self.assertFalse(ReceiptIssue.objects.exists())
        # 手書きで渡した記録があれば、印刷は「再発行」
        self.assertEqual(self.c.post(f"/api/checkouts/{co['id']}/receipt-issues", {}, format="json", **key()).status_code, 201)
        self.assertTrue(self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").json()["issued_before"])
        r = self.c.post(f"/api/checkouts/{co['id']}/print/ryoshusho", {}, format="json", **key())
        self.assertIn("（再発行）", texts(r.json()["slip"]))
        # 打ち間違いとして取り消した記録は数えない
        for i in ReceiptIssue.objects.filter(checkout_id=co["id"]):
            self.assertEqual(self.c.post(f"/api/receipt-issues/{i.id}/cancel", {}, format="json", **key()).status_code, 200)
        self.assertFalse(self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").json()["issued_before"])

    def test_receivable_is_excluded_and_stamp_box(self):
        PrinterConfig.objects.create(store=self.store, enabled=True, stamp_box_from=Decimal(3000))
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "cash", "amount": "3800", "tendered": "3800"}, {"method": "receivable", "amount": "2000", "debtor_name": "山田"}])
        body = texts(self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").json()["slip"])
        self.assertIn("¥3,800-", body)
        self.assertIn("後日のお支払い（未収）¥2,000 は含みません", body)
        r = self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").json()
        self.assertIn("stamp", [row["kind"] for row in r["slip"]["rows"]])

    def test_refused_for_voided_or_receivable_only(self):
        v = self.open_visit()
        co = self.checkout(v["id"], [{"method": "receivable", "amount": "5800", "debtor_name": "山田"}])
        self.assertEqual(self.c.post(f"/api/checkouts/{co['id']}/print/ryoshusho", {}, format="json", **key()).status_code, 400)
        Checkout.objects.filter(id=co["id"]).update(status="voided")
        self.assertEqual(self.c.get(f"/api/checkouts/{co['id']}/print/ryoshusho").status_code, 409)


class ClosingTests(PrintBase):
    def test_closing_slip_before_close(self):
        v = self.open_visit()
        self.checkout(v["id"], [{"method": "cash", "amount": "5800", "tendered": "10000"}])
        day = BusinessDay.objects.get(store=self.store, status="open")
        r = self.c.get(f"/api/cash/days/{day.id}/print/closing")
        self.assertEqual(r.status_code, 200, r.content)
        body = texts(r.json()["slip"])
        self.assertIn("精算伝票（締め前）", body)
        self.assertIn("売上合計 ¥5,800", body)
        self.assertIn("会計件数 1件", body)

    def test_staff_without_closing_cap_is_refused(self):
        day = BusinessDay.objects.get(store=self.store, status="open")
        cast = client_for(self.fx.users["cast"], self.store)
        self.assertEqual(cast.get(f"/api/cash/days/{day.id}/print/closing").status_code, 403)


class ConfigTests(PrintBase):
    def test_read_by_staff_and_edit_by_manager(self):
        staff = client_for(self.fx.users["staff"], self.store)
        r = staff.get("/api/printing/config")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertEqual(r.json()["shop_name_default"], self.store.name)
        self.assertEqual(staff.put("/api/printing/config", {"enabled": True}, format="json").status_code, 403)

        r = self.c.put("/api/printing/config", {"enabled": True, "invoice_number": "123"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.put("/api/printing/config", {"enabled": True, "model_name": "TM-m30III", "invoice_number": "t1234567890123", "paper_width": 80}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["invoice_number"], "T1234567890123")
        self.assertTrue(PrinterConfig.objects.get(store=self.store).enabled)
        self.assertEqual(self.c.put("/api/printing/config", {"paper_width": 70}, format="json").status_code, 400)

    def test_edit_needs_settings_edit(self):
        c = client_for(self.fx.users["staff"], self.store)
        r = c.put("/api/printing/config", {"enabled": True}, format="json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"]["code"], "forbidden")

    def test_test_print(self):
        r = self.c.get("/api/printing/test")
        self.assertEqual(r.status_code, 200)
        self.assertIn("テスト印刷", texts(r.json()["slip"]))


class OtherStoreTests(PrintBase):
    def test_other_store_ids_are_refused(self):
        other = make_store("s2", company=self.fx.company)
        oc = client_for(self.fx.users["owner"], other.store)
        r = oc.post("/api/visits", {"table_id": other.tables[0].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        ov = r.json()
        r = oc.post(f"/api/visits/{ov['id']}/checkouts", {"payments": [{"method": "cash", "amount": "2500", "tendered": "3000"}]}, format="json", **key())
        self.assertEqual(r.status_code, 201, r.content)
        oco = r.json()
        oday = BusinessDay.objects.get(store=other.store, status="open")
        # 自店舗（X-Store-Id = s1）の権限で、別店舗の伝票・会計・営業日を指定する
        ov2 = oc.post("/api/visits", {"table_id": other.tables[1].id, "entries": [{"guest_count": 1, "course_id": other.hourly.id}]}, format="json", **key()).json()
        self.assertEqual(self.c.get(f"/api/visits/{ov2['id']}/print/estimate").status_code, 404)
        self.assertEqual(self.c.get(f"/api/checkouts/{oco['id']}/print/receipt").status_code, 404)
        self.assertEqual(self.c.post(f"/api/checkouts/{oco['id']}/print/ryoshusho", {}, format="json", **key()).status_code, 404)
        self.assertEqual(self.c.get(f"/api/cash/days/{oday.id}/print/closing").status_code, 404)


class SlipLayoutTests(APITestCase):
    def test_width_counts_full_width_as_two(self):
        self.assertEqual(width("ABC"), 3)
        self.assertEqual(width("合計"), 4)
        self.assertEqual(width("¥1,000"), 6)

    def test_pair_fits_columns_and_wraps_long_names(self):
        self.assertEqual(pair_lines("合計", "¥5,800", 20), ["合計" + " " * 10 + "¥5,800"])
        lines = pair_lines("とても長い商品の名前がここに入ります", "¥30,000", 20)
        self.assertTrue(all(width(s) <= 20 for s in lines))
        self.assertTrue(lines[-1].endswith("¥30,000"))

    def test_xml_escapes_text(self):
        s = Slip(kind="test", title="t", columns=48).text("R&B <special>")
        xml = to_epos_xml(s)
        self.assertIn("R&amp;B &lt;special&gt;", xml)
        self.assertNotIn("R&B", xml)

    def test_yen_sign_is_sent_as_backslash_for_japanese_printers(self):
        xml = to_epos_xml(Slip(kind="test", title="t", columns=48).pair("合計", "¥5,800"))
        self.assertIn("\\5,800", xml)
        self.assertNotIn("¥", xml)

    def test_58mm_paper_has_35_columns(self):
        self.assertEqual(PrinterConfig(paper_width=58).columns(), 35)
        self.assertEqual(PrinterConfig(paper_width=80).columns(), 48)

    def test_plain_lines_never_exceed_columns(self):
        s = Slip(kind="test", title="t", columns=30)
        s.text("センター", align="center").pair("左", "右").pair("あいうえおかきくけこさしすせそ", "¥1,234,567").rule()
        self.assertTrue(all(width(ln) <= 30 for ln in plain_lines(s)))

    def test_product_name_with_ampersand_in_receipt_xml(self):
        fx = standard_fixture(code="s9", company_code="c9")
        p = Product.objects.create(store=fx.store, category=fx.categories["drink"], name="R&B カクテル", price=Decimal(1000))
        c = client_for(fx.users["manager"], fx.store)
        v = c.post("/api/visits", {"table_id": fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": fx.hourly.id}]}, format="json", **key()).json()
        c.post(f"/api/visits/{v['id']}/lines", {"kind": "product", "product_id": p.id, "quantity": 1}, format="json", **key())
        co = c.post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "3500", "tendered": "3500"}]}, format="json", **key()).json()
        xml = c.get(f"/api/checkouts/{co['id']}/print/receipt").json()["epos_xml"]
        self.assertIn("R&amp;B カクテル", xml)
