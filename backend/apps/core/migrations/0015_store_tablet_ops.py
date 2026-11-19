from django.db import migrations, models


class Migration(migrations.Migration):
    """店舗タブレット用アカウントに許可する操作（列の追加だけ。None = 既定 = これまでどおり）。"""

    dependencies = [
        ("core", "0014_store_rounding_units"),
    ]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="tablet_ops",
            field=models.JSONField(blank=True, default=None, null=True, verbose_name="タブレットに許可する操作"),
        ),
    ]
