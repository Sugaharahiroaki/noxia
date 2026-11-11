"""年度別税制設定（apps/tax/data/year_YYYY.json）を DB に投入する。既存の行は --update のときだけ上書きする。"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.tax import config


class Command(BaseCommand):
    help = "年度別の税制設定を data/year_YYYY.json から投入する"

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, default=None)
        parser.add_argument("--update", action="store_true")

    def handle(self, *args, **opts):
        years = [opts["year"]] if opts["year"] else config.available_years()
        for y in years:
            cfg = config.ensure_config(y, update=opts["update"])
            self.stdout.write(f"{y}: {'なし' if cfg is None else cfg.status} {getattr(cfg, 'content_hash', '')[:12]}")
