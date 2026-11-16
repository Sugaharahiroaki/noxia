"""
伝票 ID・会計 ID（連番にしないランダムな英数字）の追加。
既存の行は消さず・変えず、新しい列にだけ重複しない ID を入れる（列を足す → 既存の行に入れる → 一意制約を付ける）。
"""
from django.db import migrations, models

import apps.core.ids


def fill_public_ids(apps, schema_editor):
    from apps.core.ids import new_public_id

    used: set[str] = set()
    for model_name in ("Visit", "Checkout"):
        Model = apps.get_model("sales", model_name)
        for pk in Model.objects.filter(public_id__isnull=True).values_list("pk", flat=True).iterator():
            value = new_public_id()
            while value in used:
                value = new_public_id()
            used.add(value)
            Model.objects.filter(pk=pk).update(public_id=value)


class Migration(migrations.Migration):
    dependencies = [("sales", "0004_t3_tags_extension_options")]

    operations = [
        migrations.AddField(model_name="visit", name="public_id", field=models.CharField("伝票ID", max_length=16, null=True, editable=False)),
        migrations.AddField(model_name="checkout", name="public_id", field=models.CharField("会計ID", max_length=16, null=True, editable=False)),
        migrations.RunPython(fill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(model_name="visit", name="public_id",
                              field=models.CharField("伝票ID", max_length=16, unique=True, default=apps.core.ids.new_public_id, editable=False)),
        migrations.AlterField(model_name="checkout", name="public_id",
                              field=models.CharField("会計ID", max_length=16, unique=True, default=apps.core.ids.new_public_id, editable=False)),
    ]
