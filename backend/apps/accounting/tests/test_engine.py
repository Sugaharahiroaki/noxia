"""源泉税計算エンジン・納付期限・ルール選択の単体テスト。"""
from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from apps.accounting import jp_holidays as h
from apps.accounting.models import WithholdingRule
from apps.accounting.withholding import calc_period_for, compute_hostess, rule_for, solve_gross_for_net

R = Decimal("10.21")
DED = Decimal("5000")


def calc(target, start, end, used=None):
    return compute_hostess(Decimal(target), start, end, rate_percent=R, daily_deduction=DED, used_by_day=used)


class HostessCalcTests(SimpleTestCase):
    def test_daily_fee(self):
        """1. 日額報酬: (22,000 − 5,000×1日) × 10.21% = 1,735.7 → 1,735円（1円未満切り捨て）"""
        c = calc(22000, date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(c.status, "calculated")
        self.assertEqual(c.period_days, 1)
        self.assertEqual(c.deduction_applied, Decimal("5000"))
        self.assertEqual(c.withholding_amount, Decimal("1735"))

    def test_nta_example_rounding(self):
        """6. 端数処理: 国税庁 No.2807 の例 3/1〜3/31（31日）・75万円 → (750,000 − 155,000) × 10.21% = 60,749.5 → 60,749円"""
        c = calc(750000, date(2026, 3, 1), date(2026, 3, 31))
        self.assertEqual(c.period_days, 31)
        self.assertEqual(c.deduction_applied, Decimal("155000"))
        self.assertEqual(c.withholding_amount, Decimal("60749"))
        self.assertIn("60,749", c.basis()["explanation"])

    def test_zero_tax(self):
        """5. 源泉税0円: 控除額以下の報酬は税額0（控除は対象額まで）"""
        for target in (4000, 5000):
            c = calc(target, date(2026, 10, 1), date(2026, 10, 1))
            self.assertEqual(c.withholding_amount, Decimal("0"))
            self.assertEqual(c.deduction_applied, Decimal(target))
        c = calc(5010, date(2026, 10, 1), date(2026, 10, 1))
        self.assertEqual(c.withholding_amount, Decimal("1"))  # 10 × 10.21% = 1.021 → 1

    def test_calendar_days_not_attendance_days(self):
        """計算期間の日数は暦日数（出勤日数ではない）: 半月（16〜31日）は16日分の控除"""
        c = calc(300000, date(2026, 10, 16), date(2026, 10, 31))
        self.assertEqual(c.period_days, 16)
        self.assertEqual(c.deduction_applied, Decimal("80000"))
        self.assertEqual(c.withholding_amount, Decimal("22462"))  # 220,000 × 10.21% = 22,462

    def test_no_double_deduction_same_period(self):
        """4. 同じ期間の控除は一度だけ: 使用済みの日の控除は使わない"""
        first = calc(100000, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(first.deduction_applied, Decimal("100000"))  # 155,000 のうち 100,000 を使用
        used = {}
        for d, a in first.allocations:
            used[d] = used.get(d, Decimal(0)) + a
        second = calc(100000, date(2026, 10, 1), date(2026, 10, 31), used)
        self.assertEqual(second.deduction_available, Decimal("55000"))
        self.assertEqual(second.deduction_applied, Decimal("55000"))
        self.assertEqual(second.withholding_amount, Decimal("4594"))  # 45,000 × 10.21% = 4,594.5
        # 期間が一部重なる支払い（10/16〜10/31）も、使用済みの日の控除は使えない
        used2 = dict(used)
        for d, a in second.allocations:
            used2[d] = used2.get(d, Decimal(0)) + a
        third = calc(50000, date(2026, 10, 16), date(2026, 10, 31), used2)
        self.assertEqual(third.deduction_available, Decimal("0"))
        self.assertEqual(third.withholding_amount, Decimal("5105"))

    def test_salary_in_period_reduces_deduction(self):
        """同じ日に給与等を払っていれば、その額を控除額から差し引く（施行令322条）"""
        c = calc(10000, date(2026, 10, 1), date(2026, 10, 1), {date(2026, 10, 1): Decimal("3000")})
        self.assertEqual(c.deduction_applied, Decimal("2000"))
        self.assertEqual(c.withholding_amount, Decimal("816"))  # 8,000 × 10.21% = 816.8

    def test_invalid_period(self):
        c = calc(10000, date(2026, 10, 2), date(2026, 10, 1))
        self.assertEqual(c.status, "needs_review")


class GrossUpTests(SimpleTestCase):
    def test_solve_gross_exact(self):
        for cash in (1, 4999, 5000, 5001, 10000, 12345, 99999, 250000):
            for avail in (0, 5000, 155000):
                g = solve_gross_for_net(cash, avail, R)
                taxable = max(g - avail, 0)
                tax = (Decimal(taxable) * R / 100).to_integral_value(rounding="ROUND_FLOOR")
                self.assertEqual(g - tax, cash, (cash, avail, g))
                if g > cash:  # 最小の解であること
                    t1 = (Decimal(max(g - 1 - avail, 0)) * R / 100).to_integral_value(rounding="ROUND_FLOOR")
                    self.assertLess(g - 1 - t1, cash)


class PeriodTests(SimpleTestCase):
    def test_periods(self):
        d = date(2026, 10, 21)  # 水曜日
        self.assertEqual(calc_period_for("daily", d), (d, d))
        self.assertEqual(calc_period_for("weekly", d), (date(2026, 10, 19), date(2026, 10, 25)))
        self.assertEqual(calc_period_for("semimonthly", d), (date(2026, 10, 16), date(2026, 10, 31)))
        self.assertEqual(calc_period_for("semimonthly", date(2026, 2, 3)), (date(2026, 2, 1), date(2026, 2, 15)))
        self.assertEqual(calc_period_for("monthly", date(2028, 2, 10)), (date(2028, 2, 1), date(2028, 2, 29)))
        self.assertIsNone(calc_period_for("unset", d))


class DueDateTests(SimpleTestCase):
    def test_monthly_due_dates(self):
        """18. 納付期限: 翌月10日。土日祝日なら休日明け（国税通則法10条2項）"""
        self.assertEqual(h.monthly_due_date(date(2026, 10, 1)), date(2026, 11, 10))  # 火曜
        self.assertEqual(h.monthly_due_date(date(2026, 12, 1)), date(2027, 1, 12))   # 1/10 日曜・1/11 成人の日
        self.assertEqual(h.monthly_due_date(date(2026, 4, 1)), date(2026, 5, 11))    # 5/10 日曜
        self.assertEqual(h.monthly_due_date(date(2027, 9, 1)), date(2027, 10, 12))   # 10/10 日曜・10/11 スポーツの日

    def test_special_due_for_salary_only(self):
        """給与等の納期の特例: 1〜6月分は7月10日、7〜12月分は翌年1月20日"""
        self.assertEqual(h.special_due_period(date(2026, 3, 1)), (date(2026, 1, 1), date(2026, 6, 30), date(2026, 7, 10)))
        self.assertEqual(h.special_due_period(date(2026, 9, 1)), (date(2026, 7, 1), date(2026, 12, 31), date(2027, 1, 20)))

    def test_holidays_2026(self):
        hs = h.holidays(2026)
        self.assertEqual(hs[date(2026, 5, 6)], "振替休日")
        self.assertEqual(hs[date(2026, 9, 22)], "国民の休日")
        self.assertEqual(hs[date(2026, 3, 20)], "春分の日")
        self.assertEqual(hs[date(2026, 9, 23)], "秋分の日")
        self.assertTrue(h.is_tax_closed_day(date(2026, 12, 30)))
        self.assertTrue(h.is_tax_closed_day(date(2027, 1, 2)))

    def test_form_codes(self):
        self.assertEqual(h.fiscal_year_code(date(2026, 11, 10)), "08")
        self.assertEqual(h.fiscal_year_code(date(2027, 1, 12)), "08")  # 1〜3月の納付は前年度
        self.assertEqual(h.fiscal_year_code(date(2027, 4, 12)), "09")
        self.assertEqual(h.nouki_code(2027, 1), "50901")
        self.assertEqual(h.nouki_code(2026, 10), "50810")


class RuleSelectionTests(TestCase):
    def test_rules_by_payment_date(self):
        """15. 税率改正への対応: 支払日で適用ルールを選ぶ。新しいルールを足せばコードを変えずに切り替わる"""
        r2026 = rule_for("hostess_fee", date(2026, 12, 31))
        r2027 = rule_for("hostess_fee", date(2027, 1, 1))
        self.assertIn("復興特別所得税2.1%", r2026.version)
        self.assertIn("防衛特別所得税", r2027.version)
        self.assertEqual(r2026.rate_percent, Decimal("10.210"))
        self.assertEqual(r2027.rate_percent, Decimal("10.210"))
        # 仮の改正（2030年から税率・控除額が変わる）を登録すると、その日以降だけ新ルール
        r2027.effective_to = date(2029, 12, 31)
        r2027.save()
        WithholdingRule.objects.create(category="hostess_fee", version="仮の改正", effective_from=date(2030, 1, 1), rate_percent=Decimal("10.5"),
                                       daily_deduction=Decimal("6000"))
        self.assertEqual(rule_for("hostess_fee", date(2029, 12, 31)).id, r2027.id)
        new = rule_for("hostess_fee", date(2030, 1, 1))
        self.assertEqual(new.version, "仮の改正")
        c = compute_hostess(Decimal(20000), date(2030, 1, 1), date(2030, 1, 1), rate_percent=new.rate_percent, daily_deduction=new.daily_deduction)
        self.assertEqual(c.withholding_amount, Decimal("1470"))  # (20,000 − 6,000) × 10.5%

    def test_no_rule_means_needs_review(self):
        from apps.accounting.withholding import calculate_for_payment

        WithholdingRule.objects.all().update(is_active=False)
        c = calculate_for_payment(object(), None, target_amount=Decimal(10000), period=(date(2026, 10, 1), date(2026, 10, 1)), paid_on=date(2026, 10, 2))
        self.assertEqual(c.status, "needs_review")
        self.assertTrue(any("ルール" in r for r in c.reasons))
