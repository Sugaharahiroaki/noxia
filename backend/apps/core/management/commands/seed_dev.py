"""
開発用データの投入。2社 × 各2店舗、各役割のユーザー、商品・コース・従業員・設定（すべて仮設定）。

  python manage.py seed_dev            # 既存の開発用データがあればスキップ
  python manage.py seed_dev --reset    # データベースを全消去してから投入（開発用DBのみ！）

本番では ALLOW_DEV_SEED=0 にして実行できないようにする。
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import AccountingAccessGrant, CastContract, StoreTaxProfile, TaxPayer, WithholdingRule
from apps.accounting.rules_data import ensure_default_rules
from apps.catalog.models import Category, Course, Product, Table
from apps.core.models import Company, CompanyMembership, Store, StoreMembership, StoreSettings, User
from apps.customers.models import BottleKeep, Customer
from apps.inventory.models import StockMovement, Supplier, SupplierProduct
from apps.line_integration.models import LineIntegration, LineUser, MessageTemplate
from apps.terminals.models import CardTerminal, TerminalConfig
from apps.staff.models import BackRule, Employee, EmployeeStoreAssignment, WageSetting

DEV_PASSWORD = "Dev-pass-1234"
SETTINGS_NOTE = "【仮設定】開発用データです。料金単位・税込区分・サービス料・無制限変更料金・指名/同伴料・端数処理・営業日切替時刻は営業開始前にオーナーが確定してください。"


class Command(BaseCommand):
    help = "開発用の会社・店舗・ユーザー・マスタを投入する"

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="全データを削除してから投入する（開発用DB専用）")

    def handle(self, *args, **options):
        if not settings.ALLOW_DEV_SEED:
            raise CommandError("ALLOW_DEV_SEED が無効のため実行できません（本番環境では実行しないでください）。")
        if options["reset"]:
            self.stdout.write(self.style.WARNING("データベースを全消去します..."))
            call_command("flush", "--noinput")
        if Company.objects.filter(code="dev-a").exists():
            self.stdout.write("開発用データは既に存在します（--reset で作り直せます）。")
            self._print_accounts()
            return
        with transaction.atomic():
            self._seed()
        self.stdout.write(self.style.SUCCESS("開発用データを投入しました。"))
        self._print_accounts()

    # ------------------------------------------------------------------
    def _user(self, username, display_name, **kw):
        u, created = User.objects.get_or_create(username=username, defaults={"display_name": display_name, **kw})
        if created:
            u.set_password(DEV_PASSWORD)
            u.save()
        return u

    def _seed(self):
        operator = self._user("operator", "運営者", is_platform_operator=True, is_staff=True, is_superuser=True)

        a = Company.objects.create(name="【開発用】株式会社A", code="dev-a", is_demo=True, customer_sharing_enabled=False, note="開発用ダミー会社")
        b = Company.objects.create(name="【開発用】B商事", code="dev-b", is_demo=True, customer_sharing_enabled=True, note="開発用ダミー会社（顧客共有ON）")

        a1 = self._store(a, "ラウンジA 本店", "a-honten", service_charge=False, receivable=True)
        a2 = self._store(a, "ラウンジA 二号店", "a-nigo", service_charge=True, receivable=False)
        b1 = self._store(b, "Bar B", "b-main", service_charge=False, receivable=False)
        b2 = self._store(b, "Bar B 別館", "b-annex", service_charge=False, receivable=False)

        # --- A社ユーザー ---
        owner_a = self._user("owner_a", "A社オーナー")
        CompanyMembership.objects.create(user=owner_a, company=a, role="owner")
        manager_a1 = self._user("manager_a1", "A本店 店長")
        CompanyMembership.objects.create(user=manager_a1, company=a, role="member")
        StoreMembership.objects.create(user=manager_a1, store=a1, role="manager")
        staff_a1 = self._user("staff_a1", "A本店 スタッフ")
        CompanyMembership.objects.create(user=staff_a1, company=a, role="member")
        StoreMembership.objects.create(user=staff_a1, store=a1, role="staff")
        cast_a1 = self._user("cast_a1", "あい")
        CompanyMembership.objects.create(user=cast_a1, company=a, role="member")
        StoreMembership.objects.create(user=cast_a1, store=a1, role="cast")
        staff_a2 = self._user("staff_a2", "A二号店 スタッフ")
        CompanyMembership.objects.create(user=staff_a2, company=a, role="member")
        StoreMembership.objects.create(user=staff_a2, store=a2, role="staff")

        # --- B社ユーザー ---
        owner_b = self._user("owner_b", "B社オーナー")
        CompanyMembership.objects.create(user=owner_b, company=b, role="owner")
        staff_b1 = self._user("staff_b1", "B店 スタッフ")
        CompanyMembership.objects.create(user=staff_b1, company=b, role="member")
        StoreMembership.objects.create(user=staff_b1, store=b1, role="staff")

        # --- A社 従業員（会社所属、店舗配属は別） ---
        today = timezone.localdate()
        mama = Employee.objects.create(company=a, full_name="佐藤 花子", display_name="ママ", hired_on=today - timedelta(days=700))
        EmployeeStoreAssignment.objects.create(employee=mama, store=a1, role="manager")
        EmployeeStoreAssignment.objects.create(employee=mama, store=a2, role="manager")
        taro = Employee.objects.create(company=a, full_name="鈴木 太郎", display_name="太郎", hired_on=today - timedelta(days=300))
        EmployeeStoreAssignment.objects.create(employee=taro, store=a1, role="staff")
        ai = Employee.objects.create(company=a, full_name="高橋 愛", display_name="あい", hired_on=today - timedelta(days=200), user=cast_a1)
        EmployeeStoreAssignment.objects.create(employee=ai, store=a1, role="cast")
        mio = Employee.objects.create(company=a, full_name="田中 美緒", display_name="みお", hired_on=today - timedelta(days=120))
        EmployeeStoreAssignment.objects.create(employee=mio, store=a1, role="cast")
        EmployeeStoreAssignment.objects.create(employee=mio, store=a2, role="cast")  # 複数店舗勤務でも1人
        rin = Employee.objects.create(company=a, full_name="伊藤 凛", display_name="りん", hired_on=today - timedelta(days=60))
        EmployeeStoreAssignment.objects.create(employee=rin, store=a2, role="cast")
        for emp, store, wage in [(mama, a1, 2000), (mama, a2, 2000), (taro, a1, 1300), (ai, a1, 1500), (mio, a1, 1500), (mio, a2, 1600), (rin, a2, 1400)]:
            WageSetting.objects.create(employee=emp, store=store, hourly_wage=Decimal(wage), effective_from=today - timedelta(days=30), note="【仮設定】開発用")

        # --- B社 従業員 ---
        bmama = Employee.objects.create(company=b, full_name="山田 由美", display_name="ゆみママ", hired_on=today - timedelta(days=400))
        EmployeeStoreAssignment.objects.create(employee=bmama, store=b1, role="manager")
        bcast = Employee.objects.create(company=b, full_name="小林 奏", display_name="かなで", hired_on=today - timedelta(days=90))
        EmployeeStoreAssignment.objects.create(employee=bcast, store=b1, role="cast")
        WageSetting.objects.create(employee=bcast, store=b1, hourly_wage=Decimal(1400), effective_from=today - timedelta(days=30), note="【仮設定】")

        # --- バック設定（A本店: 共通 + 個人別） ---
        eff = today - timedelta(days=30)
        BackRule.objects.create(store=a1, name="キャストドリンク 50%（共通・仮）", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(50), effective_from=eff, created_by=owner_a, note="【仮設定】")
        BackRule.objects.create(store=a1, name="ボトル 10%（共通・仮）", target_type="category_kind", category_kind="bottle", calc_type="percent", value=Decimal(10), effective_from=eff, created_by=owner_a, note="【仮設定】")
        BackRule.objects.create(store=a1, name="指名バック 500円（共通・仮）", target_type="nomination", calc_type="fixed", value=Decimal(500), effective_from=eff, created_by=owner_a, note="【仮設定】")
        BackRule.objects.create(store=a1, name="同伴バック 1,000円（共通・仮）", target_type="douhan", calc_type="fixed", value=Decimal(1000), effective_from=eff, created_by=owner_a, note="【仮設定】")
        BackRule.objects.create(store=a1, employee=ai, name="あい: キャストドリンク 60%（個人・仮）", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(60), effective_from=eff, created_by=owner_a, note="【仮設定】個人別は共通より優先")
        BackRule.objects.create(store=a2, name="キャストドリンク 40%（共通・仮）", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(40), effective_from=eff, created_by=owner_a, note="【仮設定】")
        BackRule.objects.create(store=b1, name="キャストドリンク 50%（共通・仮）", target_type="category_kind", category_kind="cast_drink", calc_type="percent", value=Decimal(50), effective_from=eff, created_by=owner_b, note="【仮設定】")

        # --- 顧客・キープ（A本店） ---
        tanaka = Customer.objects.create(company=a, store=a1, display_name="田中様", assigned_employee=ai, memo="開発用ダミー顧客。常連。", created_by=owner_a)
        Customer.objects.create(company=a, store=a1, display_name="佐々木様", memo="開発用ダミー顧客。", created_by=owner_a)
        Customer.objects.create(company=a, store=a2, display_name="二号店の山本様", memo="開発用ダミー顧客（二号店）。", created_by=owner_a)
        Customer.objects.create(company=b, store=b1, display_name="B店の中村様", shared_in_company=True, memo="開発用ダミー顧客（B社・会社内共有）。", created_by=owner_b)
        champagne_a1 = Product.objects.get(store=a1, name="シャンパン（モエ）")
        BottleKeep.objects.create(store=a1, customer=tanaka, product=champagne_a1, name=champagne_a1.name, location="棚A-3", registered_on=today - timedelta(days=10),
                                  expires_on=today + timedelta(days=80), remaining_note="半分", created_by=owner_a)

        # --- LINE（未接続。テンプレートとモック利用者） ---
        for store in (a1, a2, b1, b2):
            LineIntegration.objects.create(store=store, status=LineIntegration.STATUS_NOT_CONNECTED, note="開発用: 認証情報なし（未接続）。送信はモック。")
            MessageTemplate.objects.create(store=store, name="ご来店のお礼", kind="thanks", body="{name}、本日はご来店ありがとうございました。またのお越しをお待ちしております。{store}")
            MessageTemplate.objects.create(store=store, name="来店案内", kind="visit_guide", body="{name}、いつもありがとうございます。今週も{store}でお待ちしております。")
        LineUser.objects.create(store=a1, line_user_id="Udev000000000000000000000000000001", display_name="（開発用）田中様のLINE", consent="opted_in", customer=tanaka, linked_at=timezone.now(), linked_by=owner_a, note="開発用モック。実在しない利用者ID。")

        # --- カード決済端末（A本店だけ模擬端末。実際の決済はしない） ---
        TerminalConfig.objects.create(store=a1, provider="mock", note="開発用: 模擬端末（承認・拒否を画面で選ぶ）。実際の決済はしない。")
        CardTerminal.objects.create(store=a1, provider="mock", name="レジ横（模擬）", device_id="mock_device_dev", status="paired", paired_at=timezone.now())

        # --- 経理（事業者・キャスト契約・記帳代行／税理士の権限）。すべて仮設定 ---
        ensure_default_rules(WithholdingRule)
        tp_a = TaxPayer.objects.create(company=a, name="【開発用】株式会社A", entity_type="corporation", tax_office_name="（開発用）麹町",
                                       reference_number="0000000000", address="（開発用）東京都千代田区1-1", note="開発用ダミー事業者")
        tp_b = TaxPayer.objects.create(company=b, name="【開発用】B商事（山田 由美）", entity_type="sole_proprietor", tax_office_name="（開発用）新宿",
                                       address="（開発用）東京都新宿区1-1", note="開発用ダミー事業者（個人事業主）")
        for store, tp in ((a1, tp_a), (a2, tp_a), (b1, tp_b), (b2, tp_b)):
            StoreTaxProfile.objects.create(store=store, taxpayer=tp)
        since = today - timedelta(days=200)
        CastContract.objects.create(company=a, employee=ai, contract_type="outsourcing", start_date=since, calc_period="daily", tax_category="hostess_fee",
                                    tax_review_status="confirmed", tax_reviewed_by=owner_a, tax_reviewed_at=timezone.now(), created_by=owner_a,
                                    review_note="【仮設定】開発用: 業務委託の実態として確認した想定")
        CastContract.objects.create(company=a, employee=mio, contract_type="outsourcing", start_date=since, calc_period="monthly", created_by=owner_a,
                                    deductions=[{"code": "welfare", "label": "厚生費", "calc": "percent", "value": "5"}],
                                    review_note="【仮設定】開発用: 税務区分の確認待ち（日払い画面で「要確認」になる例）")
        CastContract.objects.create(company=a, employee=rin, contract_type="employment", start_date=since, calc_period="monthly", tax_category="salary",
                                    tax_review_status="confirmed", tax_reviewed_by=owner_a, tax_reviewed_at=timezone.now(), created_by=owner_a,
                                    review_note="【仮設定】開発用: 雇用（給与所得）の例")
        CastContract.objects.create(company=b, employee=bcast, contract_type="outsourcing", start_date=since, calc_period="daily", tax_category="hostess_fee",
                                    tax_review_status="confirmed", tax_reviewed_by=owner_b, tax_reviewed_at=timezone.now(), created_by=owner_b,
                                    review_note="【仮設定】開発用")
        bookkeeper = self._user("bookkeeper_dev", "記帳代行（開発用）")
        AccountingAccessGrant.objects.create(company=a, user=bookkeeper, role="bookkeeper", granted_by=owner_a, note="開発用: 記帳代行サービスの担当者")
        advisor = self._user("zeirishi_dev", "税理士（開発用）")
        AccountingAccessGrant.objects.create(company=a, user=advisor, role="tax_advisor", granted_by=owner_a, note="開発用: 顧問税理士の想定")

        self.stdout.write(f"  会社A: {a1.name} (store_id={a1.id}), {a2.name} (store_id={a2.id})")
        self.stdout.write(f"  会社B: {b1.name} (store_id={b1.id}), {b2.name} (store_id={b2.id})")

    def _store(self, company, name, code, *, service_charge: bool, receivable: bool) -> Store:
        store = Store.objects.create(company=company, name=name, code=code, is_demo=True, webhook_token=secrets.token_urlsafe(24))
        unlimited = Course.objects.create(store=store, name="飲み放題・時間無制限", price=Decimal(3500), duration_minutes=None, extension_price=Decimal(0), sort_order=1)
        Course.objects.create(store=store, name="飲み放題・1時間", price=Decimal(2500), duration_minutes=60, extension_price=Decimal(2500), extension_minutes=60, sort_order=2)
        StoreSettings.objects.create(
            store=store, settings_note=SETTINGS_NOTE,
            price_basis="per_person", prices_include_tax=True, default_tax_rate=Decimal("10.00"),
            service_charge_enabled=service_charge, service_charge_rate=Decimal("10.00") if service_charge else Decimal("0"),
            unlimited_change_mode="fixed", unlimited_change_price=Decimal(1000),  # 【仮設定】例として差額1,000円
            unlimited_change_after_ext_mode="same_as_change",
            default_unlimited_course=unlimited,
            nomination_fee=Decimal(1000), douhan_fee=Decimal(2000),  # 【仮設定】
            alarm_mode="both", default_opening_float=Decimal(50000), receivable_allowed=receivable,
        )
        cats = {}
        for i, (name, kind) in enumerate([("ドリンク", "drink"), ("キャストドリンク", "cast_drink"), ("ボトル", "bottle"), ("ショット", "shot"), ("フード", "food")]):
            cats[kind] = Category.objects.create(store=store, name=name, kind=kind, sort_order=i)
        products = [
            dict(category=cats["drink"], name="ビール", price=800, sort_order=1),
            dict(category=cats["drink"], name="ハイボール", price=700, sort_order=2),
            dict(category=cats["drink"], name="ソフトドリンク", price=600, sort_order=3),
            dict(category=cats["cast_drink"], name="キャストドリンク", price=1500, sort_order=1),
            dict(category=cats["cast_drink"], name="キャストシャンパングラス", price=3000, sort_order=2),
            dict(category=cats["bottle"], name="シャンパン（モエ）", price=30000, sort_order=1, stock_mode="bottle", reorder_point=2, min_stock=4, cost_price=9000, is_bottle_keepable=True),
            dict(category=cats["bottle"], name="シャンパン（ドンペリ）", price=80000, sort_order=2, stock_mode="bottle", reorder_point=1, min_stock=2, cost_price=28000, is_bottle_keepable=True),
            dict(category=cats["bottle"], name="ウイスキーボトル", price=15000, sort_order=3, stock_mode="bottle", reorder_point=2, min_stock=3, cost_price=4000, is_bottle_keepable=True),
            dict(category=cats["shot"], name="テキーラショット", price=1000, sort_order=1, stock_mode="shot", volume_ml=750, shot_ml=30, reorder_point=1, min_stock=2, cost_price=3000),
            dict(category=cats["food"], name="乾き物", price=500, sort_order=1),
            dict(category=cats["food"], name="フルーツ盛り", price=3000, sort_order=2),
        ]
        for p in products:
            Product.objects.create(store=store, **{k: (Decimal(v) if k in ("price", "cost_price") and v is not None else v) for k, v in p.items()})
        for i in range(1, 7 if code.endswith("honten") else 5):
            Table.objects.create(store=store, name=f"{i}卓", capacity=4, sort_order=i)
        supplier = Supplier.objects.create(store=store, name="酒販X（開発用）", contact="03-0000-0000", order_method="電話またはFAX（手動）")
        champagne = Product.objects.get(store=store, name="シャンパン（モエ）")
        tequila = Product.objects.get(store=store, name="テキーラショット")
        SupplierProduct.objects.create(store=store, supplier=supplier, product=champagne, purchase_price=Decimal(54000), order_unit_name="ケース(6本)", units_per_order_unit=Decimal(6), is_preferred=True)
        SupplierProduct.objects.create(store=store, supplier=supplier, product=tequila, purchase_price=Decimal(3000), order_unit_name="本", units_per_order_unit=Decimal(1), is_preferred=True)
        now = timezone.now()
        StockMovement.objects.create(store=store, product=champagne, kind="receive", quantity=Decimal(6), reference="開発用初期在庫", occurred_at=now)
        StockMovement.objects.create(store=store, product=tequila, kind="receive", quantity=Decimal(3), reference="開発用初期在庫", occurred_at=now)
        StockMovement.objects.create(store=store, product=Product.objects.get(store=store, name="ウイスキーボトル"), kind="receive", quantity=Decimal(4), reference="開発用初期在庫", occurred_at=now)
        return store

    def _print_accounts(self):
        self.stdout.write("")
        self.stdout.write("開発用ログイン（パスワードは共通: %s）" % DEV_PASSWORD)
        rows = [
            ("operator", "運営者（全社。アクセスは記録される）"),
            ("owner_a", "A社オーナー（A本店・A二号店）"),
            ("manager_a1", "A本店 店舗管理者"),
            ("staff_a1", "A本店 スタッフ"),
            ("cast_a1", "A本店 キャスト（従業員「あい」に紐付け）"),
            ("staff_a2", "A二号店 スタッフ"),
            ("bookkeeper_dev", "A社の記帳代行担当者（経理の記帳・freee 連携。税務判断はできない）"),
            ("zeirishi_dev", "A社の顧問税理士の想定（経理の閲覧・確認コメント）"),
            ("owner_b", "B社オーナー（Bar B・別館）"),
            ("staff_b1", "Bar B スタッフ"),
        ]
        for u, d in rows:
            self.stdout.write(f"  {u:12s} {d}")
