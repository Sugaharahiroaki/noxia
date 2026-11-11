from django.apps import AppConfig


class ExpensesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.expenses"
    verbose_name = "領収書・経費"

    def ready(self):
        # 買掛金の仕訳を journals.build に差し込む／AI 読取ジョブの処理関数を登録する
        from apps.accounting import journals

        from . import services
        from .ocr import runner  # noqa: F401  (jobs.register の副作用)

        journals.register_build_hook(services.build_hook)
