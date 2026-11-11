"""
会計金額の計算（Django 側を正とする。浮動小数点は使わない）。

計算手順:
 1. 有効な明細を税率ごとにまとめる（値引きは負の明細として同じ税率グループに入る）。
 2. サービス料を計算する（対象明細・値引き反映・順序は店舗設定）。
    - 価格が税込(内税)の場合: 税込基準額 × 料率。サービス料自体も標準税率の税込金額として扱う。
    - 価格が税抜(外税)の場合:
        before_tax: 税抜基準額 × 料率。サービス料に標準税率の消費税がかかる。
        after_tax : 税込基準額 × 料率。サービス料には消費税をかけない。
 3. 税率グループごとに税額を計算する（丸め方は店舗設定: 既定は切り捨て）。
 4. 合計を丸め単位・丸め方で丸める。差分は「端数調整」として保存し、税額は変えない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from apps.core.money import D, ZERO, percent_of, round_yen, tax_from_included, tax_on_excluded

COURSE_TYPES = {"course_set", "extension", "course_change"}
PRODUCT_TYPES = {"product"}
FEE_TYPES = {"nomination", "douhan"}


@dataclass
class LineInput:
    id: int | None
    line_type: str
    amount: Decimal
    tax_rate: Decimal
    tax_included: bool


@dataclass
class BillTotals:
    subtotal: Decimal = ZERO                 # 値引き前の明細合計（入力金額ベース）
    discount_total: Decimal = ZERO           # 値引き合計（正の値）
    service_charge: Decimal = ZERO           # サービス料（表示金額: 内税なら税込、外税なら税抜）
    service_charge_tax: Decimal = ZERO       # サービス料にかかる税（外税 before_tax のときのみ別建て）
    tax_total: Decimal = ZERO
    tax_breakdown: list[dict] = field(default_factory=list)
    total_before_rounding: Decimal = ZERO
    rounding_adjustment: Decimal = ZERO
    total: Decimal = ZERO
    total_excl_tax: Decimal = ZERO
    breakdown: dict = field(default_factory=dict)
    settings_snapshot: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "subtotal": str(self.subtotal),
            "discount_total": str(self.discount_total),
            "service_charge": str(self.service_charge),
            "service_charge_tax": str(self.service_charge_tax),
            "tax_total": str(self.tax_total),
            "tax_breakdown": self.tax_breakdown,
            "total_before_rounding": str(self.total_before_rounding),
            "rounding_adjustment": str(self.rounding_adjustment),
            "total": str(self.total),
            "total_excl_tax": str(self.total_excl_tax),
            "breakdown": {k: str(v) for k, v in self.breakdown.items()},
            "settings_snapshot": self.settings_snapshot,
        }


def settings_snapshot(st) -> dict:
    return {
        "price_basis": st.price_basis,
        "prices_include_tax": st.prices_include_tax,
        "default_tax_rate": str(st.default_tax_rate),
        "service_charge_enabled": st.service_charge_enabled,
        "service_charge_rate": str(st.service_charge_rate),
        "service_charge_target": st.service_charge_target,
        "service_charge_order": st.service_charge_order,
        "service_charge_after_discount": st.service_charge_after_discount,
        "tax_rounding": st.tax_rounding,
        "service_charge_rounding": st.service_charge_rounding,
        "total_rounding_unit": st.total_rounding_unit,
        "total_rounding_mode": st.total_rounding_mode,
    }


def _sc_applies(line_type: str, target: str) -> bool:
    if target == "course_only":
        return line_type in COURSE_TYPES
    if target == "products_only":
        return line_type in PRODUCT_TYPES
    return True


def compute_totals(lines: Iterable[LineInput], st) -> BillTotals:
    lines = list(lines)
    t = BillTotals()
    t.settings_snapshot = settings_snapshot(st)
    tax_mode = st.tax_rounding
    default_rate = D(st.default_tax_rate)

    # --- 1. 税率グループ ---
    groups: dict[Decimal, dict] = {}

    def grp(rate: Decimal) -> dict:
        rate = D(rate).quantize(Decimal("0.01"))
        if rate not in groups:
            groups[rate] = {"incl": ZERO, "excl": ZERO}
        return groups[rate]

    breakdown: dict[str, Decimal] = {}
    for ln in lines:
        amt = D(ln.amount)
        breakdown[ln.line_type] = breakdown.get(ln.line_type, ZERO) + amt
        if ln.line_type == "discount":
            t.discount_total += -amt
        else:
            t.subtotal += amt
        g = grp(ln.tax_rate)
        if ln.tax_included:
            g["incl"] += amt
        else:
            g["excl"] += amt
    t.breakdown = breakdown

    # --- 2. サービス料 ---
    sc = ZERO
    sc_tax = ZERO
    if st.service_charge_enabled and D(st.service_charge_rate) > 0:
        basis_incl = ZERO
        basis_excl = ZERO
        for ln in lines:
            if ln.line_type == "discount":
                if not st.service_charge_after_discount:
                    continue
            elif not _sc_applies(ln.line_type, st.service_charge_target):
                continue
            amt = D(ln.amount)
            if ln.tax_included:
                basis_incl += amt
            else:
                basis_excl += amt
        sc_mode = st.service_charge_rounding
        if st.prices_include_tax:
            # 内税: 税込基準額 × 料率。外税明細が混在していれば税込換算して合算する。
            mixed_incl = basis_incl
            if basis_excl:
                mixed_incl += basis_excl + tax_on_excluded(basis_excl, default_rate, tax_mode)
            sc = percent_of(mixed_incl, st.service_charge_rate, sc_mode)
            if sc:
                grp(default_rate)["incl"] += sc
        else:
            if st.service_charge_order == "after_tax":
                base = basis_excl + tax_on_excluded(basis_excl, default_rate, tax_mode) + basis_incl
                sc = percent_of(base, st.service_charge_rate, sc_mode)
                # 税をかけない: 税率0のグループとして加える
                if sc:
                    grp(ZERO)["excl"] += sc
            else:
                base = basis_excl
                if basis_incl:
                    base += basis_incl - tax_from_included(basis_incl, default_rate, tax_mode)
                sc = percent_of(base, st.service_charge_rate, sc_mode)
                if sc:
                    grp(default_rate)["excl"] += sc
                    sc_tax = tax_on_excluded(sc, default_rate, tax_mode)
    t.service_charge = sc
    t.service_charge_tax = sc_tax

    # --- 3. 税額 ---
    total_incl = ZERO
    total_excl = ZERO
    tax_total = ZERO
    breakdown_rows = []
    for rate in sorted(groups.keys(), reverse=True):
        g = groups[rate]
        tax_in = tax_from_included(g["incl"], rate, tax_mode)
        tax_ex = tax_on_excluded(g["excl"], rate, tax_mode)
        excl = (g["incl"] - tax_in) + g["excl"]
        tax = tax_in + tax_ex
        incl = excl + tax
        if g["incl"] == 0 and g["excl"] == 0:
            continue
        breakdown_rows.append({
            "rate": str(rate),
            "taxable_excl": str(excl),
            "tax": str(tax),
            "total_incl": str(incl),
        })
        total_incl += incl
        total_excl += excl
        tax_total += tax
    t.tax_breakdown = breakdown_rows
    t.tax_total = tax_total
    t.total_before_rounding = total_incl

    # --- 4. 合計の丸め ---
    total = round_yen(total_incl, st.total_rounding_mode, int(st.total_rounding_unit or 1))
    t.rounding_adjustment = total - total_incl
    t.total = total
    t.total_excl_tax = total - tax_total
    return t


def line_inputs_from_orderlines(order_lines) -> list[LineInput]:
    return [
        LineInput(id=ol.id, line_type=ol.line_type, amount=D(ol.amount), tax_rate=D(ol.tax_rate), tax_included=ol.tax_included)
        for ol in order_lines
    ]
