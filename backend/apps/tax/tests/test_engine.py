"""所得税エンジンのゴールデンテスト（令和8年分の設定 JSON を直接読む。値は国税庁の公表値・ミセミルの検算例）。"""
import json
from decimal import Decimal
from pathlib import Path

from django.test import SimpleTestCase

from apps.tax.rules import engine

CFG = json.loads((Path(__file__).resolve().parent.parent / "data" / "year_2026.json").read_text(encoding="utf-8"))


class SalaryIncomeTests(SimpleTestCase):
    def test_table_examples(self):
        cases = [(0, 0), (740999, 0), (741000, 1000), (1000000, 260000), (2191000, 1451000), (2195000, 1453000), (2199999, 1456000),
                 (4905000, 3483200), (2204000, 1462800), (6600000, 4840000), (8500000, 6550000), (20000000, 18050000)]
        for rev, want in cases:
            inc, _ = engine.salary_income(rev, CFG)
            self.assertEqual(inc, Decimal(want), rev)


class DeductionTests(SimpleTestCase):
    def test_basic_deduction_r8(self):
        self.assertEqual(engine.basic_deduction(3483200, CFG), Decimal(1040000))
        self.assertEqual(engine.basic_deduction(4890000, CFG), Decimal(1040000))
        self.assertEqual(engine.basic_deduction(4890001, CFG), Decimal(670000))
        self.assertEqual(engine.basic_deduction(6550001, CFG), Decimal(620000))
        self.assertEqual(engine.basic_deduction(23500001, CFG), Decimal(480000))
        self.assertEqual(engine.basic_deduction(25000001, CFG), Decimal(0))

    def test_spouse(self):
        self.assertEqual(engine.spouse_deduction(5000000, {"has": True, "income": 500000}, CFG), (Decimal(380000), "spouse"))
        self.assertEqual(engine.spouse_deduction(5000000, {"has": True, "income": 500000, "age70": True}, CFG), (Decimal(480000), "spouse"))
        self.assertEqual(engine.spouse_deduction(9200000, {"has": True, "income": 500000}, CFG), (Decimal(260000), "spouse"))
        self.assertEqual(engine.spouse_deduction(5000000, {"has": True, "income": 980000}, CFG), (Decimal(360000), "spouse_special"))
        self.assertEqual(engine.spouse_deduction(5000000, {"has": True, "income": 1320000}, CFG), (Decimal(30000), "spouse_special"))
        self.assertEqual(engine.spouse_deduction(5000000, {"has": True, "income": 1400000}, CFG)[0], Decimal(0))
        self.assertEqual(engine.spouse_deduction(10000001, {"has": True, "income": 0}, CFG)[0], Decimal(0))

    def test_dependents(self):
        amt, detail = engine.dependents_deduction({"general": 1, "specific": 1, "elderly_cohabiting": 1, "specific_relatives": [{"income": 900000}]}, CFG)
        self.assertEqual(amt, Decimal(380000 + 630000 + 580000 + 610000))
        self.assertEqual(len(detail), 4)

    def test_life_insurance(self):
        amt, d = engine.life_insurance_deduction({"new_general": 50000, "care": 30000, "new_pension": 100000}, CFG)
        # 新一般 50,000 → 50,000×1/4+20,000=32,500 / 介護 30,000 → 25,000 / 年金 100,000 → 40,000（上限）
        self.assertEqual(amt, Decimal(32500 + 25000 + 40000))
        amt, _ = engine.life_insurance_deduction({"new_general": 120000, "old_general": 120000, "care": 120000, "new_pension": 120000}, CFG)
        self.assertEqual(amt, Decimal(120000))  # 合計上限
        amt, _ = engine.life_insurance_deduction({"new_general": 120000, "has_under23": True}, CFG)
        self.assertEqual(amt, Decimal(60000))  # 23歳未満の扶養親族がいる場合の一般（新）の上限

    def test_earthquake_medical_donation(self):
        self.assertEqual(engine.earthquake_deduction({"earthquake": 60000}, CFG), Decimal(50000))
        self.assertEqual(engine.earthquake_deduction({"old_long_term": 15000}, CFG), Decimal(12500))
        self.assertEqual(engine.medical_deduction({"paid": 300000, "reimbursed": 50000}, 4000000, CFG), Decimal(150000))
        self.assertEqual(engine.medical_deduction({"paid": 150000}, 1500000, CFG), Decimal(75000))  # 足切り 1,500,000×5%
        self.assertEqual(engine.donation_deduction(50000, 4000000, CFG), Decimal(48000))
        self.assertEqual(engine.donation_deduction(5000000, 4000000, CFG), Decimal(1598000))  # 40% 上限

    def test_blue_deduction_capped_by_income(self):
        self.assertEqual(engine.blue_deduction(50000, "blue_10", CFG), Decimal(50000))
        self.assertEqual(engine.blue_deduction(500000, "blue_10", CFG), Decimal(100000))
        self.assertEqual(engine.blue_deduction(-1000, "blue_10", CFG), Decimal(0))
        self.assertEqual(engine.blue_deduction(500000, "white", CFG), Decimal(0))


class TaxTests(SimpleTestCase):
    def test_brackets_and_rounding(self):
        self.assertEqual(engine.income_tax(2696000, CFG)[0], Decimal(172100))
        self.assertEqual(engine.income_tax(2146000, CFG)[0], Decimal(117100))
        self.assertEqual(engine.income_tax(1950000, CFG)[0], Decimal(97500))
        self.assertEqual(engine.income_tax(50000000, CFG)[0], Decimal(50000000 * 0.45 - 4796000))
        self.assertEqual(engine.floor_to(Decimal("1234567"), 1000), Decimal(1234000))
        self.assertEqual(engine.floor_to(Decimal("1234567"), 100), Decimal(1234500))

    def test_reconstruction_surtax(self):
        self.assertEqual(engine.surtax(974000, CFG), Decimal(20454))  # No.2260 の例
        self.assertEqual(engine.defense_surtax(974000, CFG), Decimal(0))  # 令和8年分は防衛特別所得税なし
