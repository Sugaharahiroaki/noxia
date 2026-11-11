"""テスト用のファクトリ。"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalog.models import Category, Course, Product, Table
from apps.core.models import Company, CompanyMembership, Store, StoreMembership, StoreSettings, User
from apps.staff.models import Employee, EmployeeStoreAssignment, WageSetting

PASSWORD = "Test-pass-1234"


@dataclass
class StoreFixture:
    company: Company
    store: Store
    settings: StoreSettings
    unlimited: Course
    hourly: Course
    tables: list[Table]
    categories: dict[str, Category]
    products: dict[str, Product]
    employees: dict[str, Employee] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)


def make_user(username: str, **kw) -> User:
    u = User.objects.create_user(username=username, password=PASSWORD, display_name=kw.pop("display_name", username), **kw)
    return u


def make_store(code: str = "s1", company: Company | None = None, company_code: str = "c1", open_register: bool = True, **settings_kw) -> StoreFixture:
    """open_register=True: 開店処理済み（釣銭 0 円）の状態で作る。開店そのものを試すテストは False にする。"""
    if company is None:
        company = Company.objects.create(name=f"会社{company_code}", code=company_code)
    store = Store.objects.create(company=company, name=f"店舗{code}", code=code, webhook_token=secrets.token_urlsafe(16))
    unlimited = Course.objects.create(store=store, name="飲み放題・時間無制限", price=Decimal(3500), duration_minutes=None, extension_price=Decimal(0))
    hourly = Course.objects.create(store=store, name="飲み放題・1時間", price=Decimal(2500), duration_minutes=60, extension_price=Decimal(2500), extension_minutes=60)
    base = dict(
        price_basis="per_person", prices_include_tax=True, default_tax_rate=Decimal("10.00"),
        unlimited_change_mode="fixed", unlimited_change_price=Decimal(1000), unlimited_change_after_ext_mode="same_as_change",
        default_unlimited_course=unlimited, nomination_fee=Decimal(1000), douhan_fee=Decimal(2000), alarm_mode="both",
        default_opening_float=Decimal(50000), receivable_allowed=True,
    )
    base.update(settings_kw)
    st = StoreSettings.objects.create(store=store, **base)
    cats = {}
    for i, (name, kind) in enumerate([("ドリンク", "drink"), ("キャストドリンク", "cast_drink"), ("ボトル", "bottle"), ("ショット", "shot"), ("フード", "food")]):
        cats[kind] = Category.objects.create(store=store, name=name, kind=kind, sort_order=i)
    products = {
        "beer": Product.objects.create(store=store, category=cats["drink"], name="ビール", price=Decimal(800)),
        "cast_drink": Product.objects.create(store=store, category=cats["cast_drink"], name="キャストドリンク", price=Decimal(1500)),
        "champagne": Product.objects.create(store=store, category=cats["bottle"], name="シャンパン", price=Decimal(30000), stock_mode="bottle", cost_price=Decimal(9000), is_bottle_keepable=True, reorder_point=Decimal(2), min_stock=Decimal(4)),
        "tequila": Product.objects.create(store=store, category=cats["shot"], name="テキーラショット", price=Decimal(1000), stock_mode="shot", volume_ml=Decimal(750), shot_ml=Decimal(30), cost_price=Decimal(3000)),
        "food": Product.objects.create(store=store, category=cats["food"], name="乾き物", price=Decimal(500), tax_rate=Decimal("8.00")),
    }
    tables = [Table.objects.create(store=store, name=f"{i}卓", sort_order=i) for i in range(1, 4)]
    if open_register:
        from apps.cash.models import BusinessDay
        from apps.core.business_date import business_date_now

        BusinessDay.objects.create(store=store, date=business_date_now(store), status="open", opened_at=timezone.now(), opening_float=0)
    return StoreFixture(company=company, store=store, settings=st, unlimited=unlimited, hourly=hourly, tables=tables, categories=cats, products=products)


def add_user(fx: StoreFixture, key: str, *, company_role: str | None = None, store_role: str | None = None, username: str | None = None, employee: Employee | None = None) -> User:
    username = username or f"{key}_{fx.store.code}"
    user = make_user(username)
    if company_role:
        CompanyMembership.objects.create(user=user, company=fx.company, role=company_role)
    else:
        CompanyMembership.objects.create(user=user, company=fx.company, role="member")
    if store_role:
        StoreMembership.objects.create(user=user, store=fx.store, role=store_role)
    if employee is not None:
        employee.user = user
        employee.save(update_fields=["user"])
    fx.users[key] = user
    return user


def add_employee(fx: StoreFixture, key: str, *, role: str = "cast", wage: Decimal | None = Decimal(1500)) -> Employee:
    emp = Employee.objects.create(company=fx.company, full_name=key, display_name=key, hired_on=timezone.localdate() - timedelta(days=100))
    EmployeeStoreAssignment.objects.create(employee=emp, store=fx.store, role=role)
    if wage is not None:
        WageSetting.objects.create(employee=emp, store=fx.store, hourly_wage=wage, effective_from=timezone.localdate() - timedelta(days=100))
    fx.employees[key] = emp
    return emp


def client_for(user: User, store: Store | None = None, reauth: bool = True) -> APIClient:
    c = APIClient()
    c.force_login(user)
    if reauth:
        session = c.session
        session["reauth_until"] = timezone.now().timestamp() + 3600
        session["reauth_source"] = "explicit"
        session.save()
    if store is not None:
        c.credentials(HTTP_X_STORE_ID=str(store.id))
    return c


def standard_fixture(code: str = "s1", company_code: str = "c1", open_register: bool = True, **settings_kw) -> StoreFixture:
    """オーナー・店長・スタッフ・キャスト（従業員紐付け）を揃えた標準フィクスチャ。"""
    fx = make_store(code, company_code=company_code, open_register=open_register, **settings_kw)
    add_user(fx, "owner", company_role="owner")
    add_user(fx, "manager", store_role="manager")
    add_user(fx, "staff", store_role="staff")
    cast = add_employee(fx, "cast1", role="cast")
    add_employee(fx, "cast2", role="cast")
    add_employee(fx, "staff1", role="staff", wage=Decimal(1200))
    add_user(fx, "cast", store_role="cast", employee=cast)
    return fx
