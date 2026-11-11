from django.apps import AppConfig


class BankingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.banking"
    verbose_name = "銀行"

    def ready(self):
        # 口座間振替の仕訳を journals.build の対象に登録する
        from . import services  # noqa: F401
