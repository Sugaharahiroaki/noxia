"""古い監査ログの整理（保存期間を過ぎた一般操作のログだけ。税務・経理の操作は残す）。"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.models import AuditLog

KEEP_PREFIXES = ("accounting.", "tax.", "expenses.", "banking.", "closing.", "export.", "yearend.")


class Command(BaseCommand):
    help = "保存期間（既定 730 日）を過ぎた一般操作の監査ログを削除する（経理・税務の操作は残す）"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=730)
        parser.add_argument("--yes", action="store_true", help="確認なしで削除する")

    def handle(self, *args, **opts):
        cutoff = timezone.now() - timedelta(days=opts["days"])
        qs = AuditLog.objects.filter(created_at__lt=cutoff)
        for p in KEEP_PREFIXES:
            qs = qs.exclude(action__startswith=p)
        n = qs.count()
        if not opts["yes"]:
            self.stdout.write(f"{n} 件が対象です。削除するには --yes を付けてください。")
            return
        # モデルの delete() は禁止しているため、QuerySet で直接削除する
        deleted, _ = qs._raw_delete(qs.db), None  # noqa: SLF001
        self.stdout.write(f"{deleted} 件削除しました")
