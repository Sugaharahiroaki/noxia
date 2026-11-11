"""
容量節約のための定期削除。

  python manage.py cleanup_records            # 2日より古い二重登録防止記録を削除
  python manage.py cleanup_records --days 7

レジ締めのたびにも自動で実行される。業務データ（伝票・会計・勤怠・操作履歴など）は削除しない。
"""
from django.core.management.base import BaseCommand

from apps.cash.services import IDEMPOTENCY_RETENTION_DAYS, purge_old_idempotency_records


class Command(BaseCommand):
    help = "古い二重登録防止記録（応答のコピー）を削除する"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=IDEMPOTENCY_RETENTION_DAYS)

    def handle(self, *args, **options):
        n = purge_old_idempotency_records(max(1, options["days"]))
        self.stdout.write(f"{n} 件削除しました。")
