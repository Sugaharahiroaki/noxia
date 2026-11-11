"""
ホステス等の報酬・料金の源泉徴収税額の計算（所得税法204条1項6号・205条2号、施行令322条）。

  税額 = (源泉対象額 − 控除額) × 税率   ※ 1円未満切り捨て（ルールの rounding）
  控除額 = 1日あたりの控除額（5,000円）× 報酬計算期間の暦日数
           − 同じ期間に支払う給与等の額

- 「計算期間の日数」は営業日数・出勤日数ではなく、計算期間の初日から末日までの全日数（国税庁 No.2807）。
- 控除額は「事業者×キャスト×日」の台帳（DeductionUsage）で管理する。1日分の控除（5,000円）は、
  その日を含む支払いのどれか1回の分としてしか使えない。同じ計算期間への追加支払い、月単位の契約での
  日々の前払いでは、まだ使っていない控除だけを使う（重複して控除しない）。
- 税率・控除額はコードに固定せず WithholdingRule（支払日で選ぶ）から読む。
- 計算期間・税務区分・ルールが不明なときは計算せず needs_review を返す（自動確定しない）。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from apps.core.money import D, HUNDRED, ZERO, round_yen

ENGINE_VERSION = "hostess-withholding/1"


@dataclass
class WithholdingCalc:
    status: str  # calculated / needs_review
    reasons: list[str] = field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    period_days: int = 0
    target_amount: Decimal = ZERO
    deduction_available: Decimal = ZERO
    deduction_applied: Decimal = ZERO
    taxable_amount: Decimal = ZERO
    rate_percent: Decimal = ZERO
    withholding_amount: Decimal = ZERO
    rule_id: int | None = None
    rule_version: str = ""
    rounding: str = "floor"
    allocations: list[tuple[date, Decimal]] = field(default_factory=list)

    def basis(self) -> dict:
        """明細に保存する計算根拠（人が読める説明つき）。"""
        expl = ""
        if self.status == "calculated":
            expl = (
                f"（源泉対象額 {self.target_amount:,.0f}円 − 控除額 {self.deduction_applied:,.0f}円）× {self.rate_percent}%"
                f" ＝ {self.withholding_amount:,.0f}円（{self.rounding_label()}）。"
                f"控除額は 計算期間 {self.period_start}〜{self.period_end}（{self.period_days}日）のうち未使用分 {self.deduction_available:,.0f}円 から充当。"
            )
        return {
            "engine_version": ENGINE_VERSION,
            "status": self.status,
            "reasons": self.reasons,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "period_days": self.period_days,
            "target_amount": str(self.target_amount),
            "deduction_available": str(self.deduction_available),
            "deduction_applied": str(self.deduction_applied),
            "taxable_amount": str(self.taxable_amount),
            "rate_percent": str(self.rate_percent),
            "rounding": self.rounding,
            "withholding_amount": str(self.withholding_amount),
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "explanation": expl,
        }

    def rounding_label(self) -> str:
        return {"floor": "1円未満切り捨て", "ceil": "1円未満切り上げ", "half_up": "1円未満四捨五入"}.get(self.rounding, self.rounding)


# --- 計算期間 -----------------------------------------------------------------
def calc_period_for(period_type: str, work_date: date) -> tuple[date, date] | None:
    """契約の報酬計算期間の種類から、勤務日を含む計算期間を返す。未設定なら None。"""
    if period_type == "daily":
        return work_date, work_date
    if period_type == "weekly":
        start = work_date - timedelta(days=work_date.weekday())
        return start, start + timedelta(days=6)
    if period_type == "semimonthly":
        last = calendar.monthrange(work_date.year, work_date.month)[1]
        if work_date.day <= 15:
            return work_date.replace(day=1), work_date.replace(day=15)
        return work_date.replace(day=16), work_date.replace(day=last)
    if period_type == "monthly":
        last = calendar.monthrange(work_date.year, work_date.month)[1]
        return work_date.replace(day=1), work_date.replace(day=last)
    return None


def period_dates(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --- 純粋な計算 ---------------------------------------------------------------
def compute_hostess(
    target_amount, period_start: date, period_end: date, *, rate_percent, daily_deduction, rounding: str = "floor",
    used_by_day: dict[date, Decimal] | None = None, rule_id=None, rule_version: str = "",
) -> WithholdingCalc:
    """
    used_by_day: すでに使われた控除額（日付→額）。給与等の支払いで減った分もここに含める。
    控除は計算期間の初日から順に、各日の残り（daily_deduction − 使用済み）を使う。
    """
    target = D(target_amount)
    calc = WithholdingCalc(
        status="calculated", period_start=period_start, period_end=period_end, period_days=(period_end - period_start).days + 1,
        target_amount=target, rate_percent=D(rate_percent), rule_id=rule_id, rule_version=rule_version, rounding=rounding,
    )
    if period_end < period_start:
        return WithholdingCalc(status="needs_review", reasons=["計算期間の終了日が開始日より前です。"])
    used_by_day = used_by_day or {}
    per_day = D(daily_deduction)
    remaining_by_day = []
    for d in period_dates(period_start, period_end):
        rest = per_day - D(used_by_day.get(d, ZERO))
        if rest > 0:
            remaining_by_day.append((d, rest))
    calc.deduction_available = sum((r for _, r in remaining_by_day), ZERO)
    if target <= 0:
        return calc
    need = min(target, calc.deduction_available)
    calc.deduction_applied = need
    for d, rest in remaining_by_day:
        if need <= 0:
            break
        take = min(rest, need)
        calc.allocations.append((d, take))
        need -= take
    calc.taxable_amount = target - calc.deduction_applied
    calc.withholding_amount = round_yen(calc.taxable_amount * calc.rate_percent / HUNDRED, rounding) if calc.taxable_amount > 0 else ZERO
    return calc


def solve_gross_for_net(cash_plus_deductions, deduction_available, rate_percent, rounding: str = "floor", target_ratio=Decimal("1")) -> Decimal:
    """
    「金額を変えて渡す」用: 手渡し額＋その他控除 X から、この支払いで精算する報酬額 g を求める。
      g − 税額(g) = X となる最小の整数 g（税額は g×target_ratio の対象額から控除を引いた残りに税率）。
    g を1円増やすと（g − 税額）は0か1円増えるので、必ず解がある。
    """
    x = D(cash_plus_deductions)
    if x <= 0:
        return ZERO
    rate = D(rate_percent) / HUNDRED
    avail = D(deduction_available)
    ratio = D(target_ratio)

    def tax(g: Decimal) -> Decimal:
        taxable = g * ratio - min(g * ratio, avail)
        return round_yen(taxable * rate, rounding) if taxable > 0 else ZERO

    if ratio <= 0 or x * ratio <= avail:
        return x
    # 近似値から前後を探す
    approx = (x - avail * rate) / (Decimal(1) - rate * ratio)
    g = max(x, round_yen(approx, "floor") - 3)
    while g - tax(g) < x:
        g += 1
    while g > x and (g - 1) - tax(g - 1) >= x:
        g -= 1
    return g


# --- ルール・台帳（DB）-------------------------------------------------------------
def rule_for(category: str, on_date: date):
    from .models import WithholdingRule

    from django.db.models import Q

    return (
        WithholdingRule.objects.filter(category=category, is_active=True, effective_from__lte=on_date)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=on_date))
        .order_by("-effective_from", "-id")
        .first()
    )


def used_deductions(taxpayer, employee, start: date, end: date) -> dict[date, Decimal]:
    """事業者×キャストの、期間内の日ごとの使用済み控除額（取消された支払いの分は台帳から消えている）。"""
    from .models import DeductionUsage

    rows = (
        DeductionUsage.objects.filter(taxpayer=taxpayer, employee=employee, date__gte=start, date__lte=end)
        .values("date").annotate(s=Sum("amount"))
    )
    return {r["date"]: r["s"] or ZERO for r in rows}


def calculate_for_payment(taxpayer, employee, *, target_amount, period: tuple[date, date] | None, paid_on: date) -> WithholdingCalc:
    """支払い1回分の計算（DB は読むだけ）。"""
    reasons = []
    if taxpayer is None:
        reasons.append("この店舗の事業者（源泉徴収義務者）が設定されていません。「経理設定」で店舗の事業者を設定してください。")
    if period is None:
        reasons.append("契約の報酬計算期間が未設定のため、控除額（5,000円×日数）を計算できません。")
    rule = rule_for("hostess_fee", paid_on)
    if rule is None:
        reasons.append(f"{paid_on.isoformat()} に適用できる源泉徴収の計算ルールが登録されていません（運営者に確認してください）。")
    if reasons:
        c = WithholdingCalc(status="needs_review", reasons=reasons, target_amount=D(target_amount))
        if period:
            c.period_start, c.period_end = period
            c.period_days = (period[1] - period[0]).days + 1
        return c
    used = used_deductions(taxpayer, employee, period[0], period[1])
    return compute_hostess(
        target_amount, period[0], period[1], rate_percent=rule.rate_percent, daily_deduction=rule.daily_deduction,
        rounding=rule.rounding, used_by_day=used, rule_id=rule.id, rule_version=rule.version,
    )
