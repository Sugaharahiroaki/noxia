"""load_store_menu: JSON からのメニュー登録（BAR RIVON の実データ）、やり直しで増えないこと、TAX 20% の金額。"""
from __future__ import annotations

import io
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.catalog.models import Category, Course, Product
from apps.core.models import AuditLog, Company, Store, StoreSettings
from apps.sales.pricing import LineInput, compute_totals

RIVON = Path(__file__).resolve().parents[1] / "store_menus" / "rivon.json"


def load(path, *extra) -> str:
    out = io.StringIO()
    call_command("load_store_menu", str(path), *extra, stdout=out)
    return out.getvalue()


class RivonMenuTests(TestCase):
    def store(self) -> Store:
        return Store.objects.get(company__code="rivon", code="rivon")

    def test_creates_store_settings_and_menu(self):
        load(RIVON)
        store = self.store()
        self.assertEqual(store.name, "BAR RIVON")
        st = StoreSettings.objects.get(store=store)
        self.assertTrue(st.prices_include_tax)
        self.assertTrue(st.service_charge_enabled)
        self.assertEqual(st.service_charge_rate, Decimal("20"))
        self.assertEqual(st.douhan_fee, Decimal("1500"))
        self.assertIsNone(st.nomination_fee)  # メニューに無いので未設定のまま
        men = Course.objects.get(store=store, name="MEN 1時間")
        self.assertEqual((men.price, men.duration_minutes, men.kind), (Decimal("3500"), 60, "セット"))
        self.assertEqual(Course.objects.get(store=store, name="WOMEN 1時間").price, Decimal("2000"))
        self.assertEqual(Product.objects.get(store=store, name="キャストドリンク").category.kind, "cast_drink")
        self.assertEqual(Product.objects.get(store=store, name="テキーラ").price, Decimal("1500"))
        self.assertEqual(Product.objects.get(store=store, name="アルマンドゴールド").price, Decimal("140000"))
        self.assertEqual(Product.objects.filter(store=store, category__kind="bottle").count(), 6)
        self.assertEqual(Product.objects.filter(store=store, price=0).count(), 16)
        self.assertEqual(Product.objects.filter(store=store).count(), 24)
        self.assertEqual(AuditLog.objects.filter(store=store, action="catalog.menu.load").count(), 1)

    def test_rerun_does_not_duplicate(self):
        load(RIVON)
        out = load(RIVON)
        store = self.store()
        self.assertIn("追加 0 件、変更 0 件", out)
        self.assertEqual(Company.objects.filter(code="rivon").count(), 1)
        self.assertEqual(Course.objects.filter(store=store).count(), 2)
        self.assertEqual(Category.objects.filter(store=store).count(), 5)
        self.assertEqual(Product.objects.filter(store=store).count(), 24)
        self.assertEqual(AuditLog.objects.filter(store=store, action="catalog.menu.load").count(), 1)

    def test_rerun_updates_changed_price_only(self):
        load(RIVON)
        data = json.loads(RIVON.read_text(encoding="utf-8"))
        data["categories"][0]["products"][0]["price"] = 1200
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "menu.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            out = load(p)
        self.assertIn("追加 0 件、変更 1 件", out)
        self.assertEqual(Product.objects.get(store=self.store(), name="キャストドリンク").price, Decimal("1200"))

    def test_dry_run_saves_nothing(self):
        out = load(RIVON, "--dry-run")
        self.assertIn("保存していません", out)
        self.assertFalse(Company.objects.filter(code="rivon").exists())

    def test_invalid_value_saves_nothing(self):
        data = json.loads(RIVON.read_text(encoding="utf-8"))
        data["categories"][-1]["kind"] = "wine"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "menu.json"
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(CommandError):
                load(p)
        self.assertFalse(Company.objects.filter(code="rivon").exists())

    def test_tax_20_and_cash_5_percent_off(self):
        """TAX 20% = 表示価格×1.2（カード・標準）。現金は料率 14% で、TAX 込みの合計のちょうど 5% 引き。"""
        load(RIVON)
        st = StoreSettings.objects.get(store=self.store())
        ten = Decimal("10.00")
        lines = [
            LineInput(None, "course_set", Decimal("3500"), ten, True),  # MEN 1時間 × 1
            LineInput(None, "product", Decimal("1000"), ten, True),     # キャストドリンク
            LineInput(None, "douhan", Decimal("1500"), ten, True),      # 同伴料
        ]
        for method in (None, "card"):
            t = compute_totals(lines, st, method)
            self.assertEqual(t.service_charge, Decimal("1200"))
            self.assertEqual(t.total, Decimal("7200"))
            self.assertEqual(t.tax_total, Decimal("654"))  # 7,200 × 10/110 の切り捨て
        cash = compute_totals(lines, st, "cash")
        self.assertEqual(cash.total, Decimal("6840"))  # 7,200 × 0.95
        self.assertEqual(cash.tax_total, Decimal("621"))  # 6,840 × 10/110 の切り捨て
