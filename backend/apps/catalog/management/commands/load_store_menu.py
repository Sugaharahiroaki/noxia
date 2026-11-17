"""
店舗のメニュー（セット・商品）と料金の設定を JSON から登録する。

  python manage.py load_store_menu apps/catalog/store_menus/rivon.json
  python manage.py load_store_menu apps/catalog/store_menus/rivon.json --dry-run   # 保存せずに内容だけ表示
  python manage.py load_store_menu apps/catalog/store_menus/rivon.json --store "Bar Rivon"   # 画面で作った店舗に入れる

- 会社（code）と店舗（会社内の code）が無ければ作る。既にあれば名前などは変えない。
- 店舗設定は JSON に書いた項目だけを上書きする（書いていない項目はそのまま）。
- コース・カテゴリー・商品は店舗内の名前で照合して作成／更新する。JSON に無いものは消さない（販売の停止は画面で行う）。
- 伝票の明細は取引時点の価格を保存しているので、登録し直しても過去の会計は変わらない。
- 開発用 DB を seed_dev --reset で作り直したあとも、同じコマンドで入れ直せる。
"""
from __future__ import annotations

import json
import secrets
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import models, transaction

from apps.catalog.models import Category, Course, CourseExtensionOption, Product
from apps.core.models import AuditLog, Company, Store, StoreSettings

# 店舗設定のうち JSON から変えられない項目（暗証番号・関連）
SETTINGS_EXCLUDED = {"id", "store", "created_at", "updated_at", "void_pin_hash", "void_pin_failed_count", "void_pin_locked_until", "default_unlimited_course"}
COURSE_FIELDS = {"price", "duration_minutes", "extension_price", "extension_minutes", "tax_rate", "kind", "note", "is_active", "sort_order"}
CATEGORY_FIELDS = {"kind", "is_active", "sort_order"}
PRODUCT_FIELDS = {"short_name", "price", "tax_rate", "note", "is_active", "sort_order", "stock_mode", "volume_ml", "shot_ml",
                  "reorder_point", "min_stock", "cost_price", "is_bottle_keepable"}
NON_NEGATIVE = {"price", "extension_price", "cost_price"}


def _settings_fields() -> set[str]:
    return {f.name for f in StoreSettings._meta.concrete_fields if f.name not in SETTINGS_EXCLUDED and not f.is_relation}


class Command(BaseCommand):
    help = "店舗のメニュー（セット・商品）と料金の設定を JSON から登録する"

    def add_arguments(self, parser):
        parser.add_argument("path", help="メニューの JSON ファイル（例: apps/catalog/store_menus/rivon.json）")
        parser.add_argument("--dry-run", action="store_true", help="登録内容を表示するだけで保存しない")
        parser.add_argument(
            "--store",
            help="画面で作った既存の店舗に入れる（店舗名か店舗 ID）。指定すると JSON の会社・店舗コードは使わず、会社・店舗は作らない",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CommandError(f"ファイルが見つかりません: {path}")
        except json.JSONDecodeError as e:
            raise CommandError(f"JSON の形式が正しくありません: {e}")
        if not isinstance(data, dict):
            raise CommandError("JSON の一番外側は {...} にしてください。")

        self.lines: list[str] = []
        self.created = 0
        self.updated = 0
        target = self._find_store(options["store"]) if options.get("store") else None
        with transaction.atomic():
            store = self._load(data, source=path.name, target=target)
            if options["dry_run"]:
                transaction.set_rollback(True)
        for line in self.lines:
            self.stdout.write(line)
        summary = f"{store.company.name} / {store.name}: 追加 {self.created} 件、変更 {self.updated} 件"
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"[確認のみ・保存していません] {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(summary))

    # ------------------------------------------------------------------
    def _note(self, verb: str, text: str):
        self.lines.append(f"  {verb}: {text}")
        if verb == "追加":
            self.created += 1
        elif verb == "変更":
            self.updated += 1

    def _clean(self, model, name: str, raw, instance, label: str):
        try:
            field = model._meta.get_field(name)
        except FieldDoesNotExist:
            raise CommandError(f"{label}: 「{name}」という項目はありません。")
        if isinstance(field, models.DecimalField) and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            raw = str(raw)
        try:
            value = field.clean(raw, instance)
        except ValidationError as e:
            raise CommandError(f"{label}: {name} の値が正しくありません（{' '.join(e.messages)}）")
        if name in NON_NEGATIVE and value is not None and Decimal(value) < 0:
            raise CommandError(f"{label}: {name} は 0 以上にしてください。")
        return value

    def _apply(self, model, obj, values: dict, *, allowed: set[str], label: str) -> list[str]:
        """values を obj に入れる。変わった項目名を返す（保存はしない）。"""
        changed = []
        for name, raw in values.items():
            if name not in allowed:
                raise CommandError(f"{label}: 「{name}」は登録できない項目です。")
            value = self._clean(model, name, raw, obj, label)
            if getattr(obj, name) != value:
                setattr(obj, name, value)
                changed.append(name)
        return changed

    def _require(self, d, key: str, label: str):
        v = d.get(key) if isinstance(d, dict) else None
        if v is None or (isinstance(v, str) and not v.strip()):
            raise CommandError(f"{label}: 「{key}」を書いてください。")
        return v.strip() if isinstance(v, str) else v

    def _find_store(self, key: str) -> Store:
        """--store の値（店舗 ID か店舗名。名前は大文字・小文字を区別しない）から店舗を 1 つに決める。"""
        key = key.strip()
        qs = Store.objects.select_related("company")
        found = list(qs.filter(id=int(key))) if key.isdigit() else list(qs.filter(name__iexact=key))
        if len(found) == 1:
            return found[0]
        stores = "\n".join(f"  ID {s.id}: {s.company.name} / {s.name}（店舗コード {s.code}）" for s in qs.order_by("id"))
        why = "見つかりません" if not found else "同じ名前の店舗が複数あります。店舗 ID で指定してください"
        raise CommandError(f"店舗「{key}」が{why}。登録されている店舗:\n{stores or '  （なし）'}")

    # ------------------------------------------------------------------
    def _load(self, data: dict, *, source: str, target: Store | None = None) -> Store:
        if target is not None:
            store, company = target, target.company
        else:
            company_in = data.get("company") or {}
            store_in = data.get("store") or {}
            company_code = self._require(company_in, "code", "会社")
            store_code = self._require(store_in, "code", "店舗")

            company = Company.objects.filter(code=company_code).first()
            if company is None:
                company = Company(code=company_code, name=self._require(company_in, "name", "会社"), note=company_in.get("note") or "")
                self._full_clean(company, "会社")
                company.save()
                self._note("追加", f"会社 {company.name}（{company.code}）")
            store = Store.objects.filter(company=company, code=store_code).first()
            if store is None:
                store = Store(company=company, code=store_code, name=self._require(store_in, "name", "店舗"), webhook_token=secrets.token_urlsafe(24))
                self._full_clean(store, "店舗")
                store.save()
                self._note("追加", f"店舗 {store.name}（{store.code}）")

        st, st_created = StoreSettings.objects.get_or_create(store=store)
        changed = self._apply(StoreSettings, st, data.get("settings") or {}, allowed=_settings_fields(), label="店舗設定")
        if changed:
            st.save()
            self._note("変更", f"店舗設定（{'、'.join(changed)}）")

        for i, c in enumerate(data.get("courses") or [], start=1):
            self._load_course(store, c, i)
        for i, cat_in in enumerate(data.get("categories") or [], start=1):
            self._load_category(store, cat_in, i)

        if self.created or self.updated:
            AuditLog.objects.create(
                company=company, store=store, user=None, action="catalog.menu.load", target_type="Store", target_id=str(store.id),
                changes={"filename": source, "created": self.created, "updated": self.updated},
            )
        return store

    def _full_clean(self, obj, label: str):
        try:
            obj.full_clean()
        except ValidationError as e:
            raise CommandError(f"{label}: 値が正しくありません（{' '.join(e.messages)}）")

    def _load_course(self, store: Store, c: dict, index: int):
        name = self._require(c, "name", f"コース {index} 件目")
        label = f"コース「{name}」"
        self._require(c, "price", label)
        values = {k: v for k, v in c.items() if k not in ("name", "extension_options")}
        values.setdefault("sort_order", index)
        course = Course.objects.filter(store=store, name=name).order_by("id").first()
        is_new = course is None
        if is_new:
            course = Course(store=store, name=name)
        changed = self._apply(Course, course, values, allowed=COURSE_FIELDS, label=label)
        if is_new or changed:
            course.save()
        if "extension_options" in c:
            opts = []
            for j, o in enumerate(c.get("extension_options") or [], start=1):
                minutes = self._clean(CourseExtensionOption, "minutes", self._require(o, "minutes", f"{label} の延長"), None, f"{label} の延長")
                price = self._clean(CourseExtensionOption, "price", self._require(o, "price", f"{label} の延長"), None, f"{label} の延長")
                if price < 0:
                    raise CommandError(f"{label} の延長: price は 0 以上にしてください。")
                opts.append((minutes, price, j))
            current = [(o.minutes, o.price, o.sort_order) for o in course.extension_options.all()]
            if current != opts:
                course.extension_options.all().delete()
                for minutes, price, j in opts:
                    CourseExtensionOption.objects.create(course=course, minutes=minutes, price=price, sort_order=j)
                changed.append("extension_options")
        if is_new:
            self._note("追加", f"コース {name} {int(course.price):,}円")
        elif changed:
            self._note("変更", f"コース {name}（{'、'.join(changed)}）")

    def _load_category(self, store: Store, cat_in: dict, index: int):
        name = self._require(cat_in, "name", f"カテゴリー {index} 件目")
        label = f"カテゴリー「{name}」"
        self._require(cat_in, "kind", label)
        values = {k: v for k, v in cat_in.items() if k not in ("name", "products")}
        values.setdefault("sort_order", index)
        cat = Category.objects.filter(store=store, name=name).order_by("id").first()
        is_new = cat is None
        if is_new:
            cat = Category(store=store, name=name)
        changed = self._apply(Category, cat, values, allowed=CATEGORY_FIELDS, label=label)
        if is_new or changed:
            cat.save()
            self._note("追加" if is_new else "変更", f"カテゴリー {name}" + ("" if is_new else f"（{'、'.join(changed)}）"))
        for j, p in enumerate(cat_in.get("products") or [], start=1):
            self._load_product(store, cat, p, j)

    def _load_product(self, store: Store, cat: Category, p: dict, index: int):
        name = self._require(p, "name", f"{cat.name} の商品 {index} 件目")
        label = f"商品「{name}」"
        self._require(p, "price", label)
        values = {k: v for k, v in p.items() if k != "name"}
        values.setdefault("sort_order", index)
        product = Product.objects.filter(store=store, name=name).order_by("id").first()
        is_new = product is None
        if is_new:
            product = Product(store=store, name=name, category=cat)
        changed = self._apply(Product, product, values, allowed=PRODUCT_FIELDS, label=label)
        if not is_new and product.category_id != cat.id:
            product.category = cat
            changed.append("category")
        if is_new or changed:
            product.save()
        if is_new:
            self._note("追加", f"商品 {cat.name} / {name} {int(product.price):,}円")
        elif changed:
            self._note("変更", f"商品 {name}（{'、'.join(changed)}）")
