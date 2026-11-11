"""
非同期ジョブ（DB キュー）を実行する。

  manage.py run_jobs            実行待ちをまとめて処理して終了（cron 向け）
  manage.py run_jobs --loop     常駐して interval 秒ごとに処理（開発・systemd 向け）
"""
from __future__ import annotations

import time

from django.core.management.base import BaseCommand

from apps.core import jobs


class Command(BaseCommand):
    help = "非同期ジョブ（AI 読取・CSV 解析など）を実行する"

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="常駐して繰り返し実行する")
        parser.add_argument("--interval", type=float, default=2.0, help="常駐時の確認間隔（秒）")
        parser.add_argument("--limit", type=int, default=20, help="1回に処理する最大件数")

    def handle(self, *args, **opts):
        # ジョブの処理関数を登録するために各アプリの handlers を import する
        for mod in ("apps.expenses.ocr.runner", "apps.banking.jobs", "apps.tax.jobs"):
            try:
                __import__(mod)
            except ImportError:
                pass
        if not opts["loop"]:
            n = jobs.run_pending(opts["limit"])
            self.stdout.write(f"{n} 件処理しました")
            return
        self.stdout.write("ジョブの実行を開始します（Ctrl+C で終了）")
        try:
            while True:
                n = jobs.run_pending(opts["limit"])
                if n == 0:
                    time.sleep(opts["interval"])
        except KeyboardInterrupt:
            self.stdout.write("終了します")
