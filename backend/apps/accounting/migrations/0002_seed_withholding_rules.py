"""
ホステス等の報酬・料金の源泉徴収ルール（国税庁 No.2807 令和8年4月1日現在法令等、令和9年分 源泉徴収税額表で確認）。

- 〜令和8年12月: 所得税10% ＋ 復興特別所得税（所得税額の2.1%）＝ 10.21%
- 令和9年1月〜:  所得税10% ＋ 防衛特別所得税（所得税額の1%）＋ 復興特別所得税（所得税額の1.1%、令和29年まで）＝ 10.21%
  合計の税率は変わらないが、内訳と根拠が変わるため別のルールとして登録する。
控除額は 5,000円 × 計算期間の日数、1円未満の端数は切り捨て。
"""
from datetime import date
from decimal import Decimal

from django.db import migrations

SOURCE = "https://www.nta.go.jp/taxes/shiraberu/taxanswer/gensen/2807.htm"
BASIS = "所得税法204条1項6号・205条2号・216条、所得税法施行令322条、所得税基本通達204-2〜3"

RULES = [
    {
        "version": "所得税10%＋復興特別所得税2.1%（平成25年1月〜令和8年12月支払分）",
        "effective_from": date(2013, 1, 1),
        "effective_to": date(2026, 12, 31),
        "components": [{"name": "所得税", "rate": "10%"}, {"name": "復興特別所得税", "rate": "所得税額の2.1%"}],
        "legal_basis": BASIS + "、復興財源確保法",
    },
    {
        "version": "所得税10%＋防衛特別所得税1%＋復興特別所得税1.1%（令和9年1月以後支払分）",
        "effective_from": date(2027, 1, 1),
        "effective_to": None,
        "components": [{"name": "所得税", "rate": "10%"}, {"name": "防衛特別所得税", "rate": "所得税額の1%"},
                       {"name": "復興特別所得税", "rate": "所得税額の1.1%（令和29年12月31日まで）"}],
        "legal_basis": BASIS + "、復興財源確保法、防衛財源確保法",
    },
]


def seed(apps, schema_editor):
    Rule = apps.get_model("accounting", "WithholdingRule")
    for r in RULES:
        Rule.objects.get_or_create(
            category="hostess_fee", effective_from=r["effective_from"],
            defaults={**r, "rate_percent": Decimal("10.21"), "daily_deduction": Decimal("5000"), "rounding": "floor", "source_url": SOURCE},
        )


def unseed(apps, schema_editor):
    apps.get_model("accounting", "WithholdingRule").objects.filter(category="hostess_fee", effective_from__in=[r["effective_from"] for r in RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [("accounting", "0001_initial")]
    operations = [migrations.RunPython(seed, unseed)]
