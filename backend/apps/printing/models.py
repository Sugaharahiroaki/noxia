"""
レシートプリンター（店舗ごと）。

iPad の Safari（Web アプリ）は Bluetooth の機器に直接つなげないので、Bluetooth の接続はエプソンの無料アプリ
「Epson TM Print Assistant」に任せる。NOXIA は伝票の中身（ePOS-Print XML）を作り、URL でそのアプリに渡す。
どのプリンターに出すか（Bluetooth のペアリング・機器の選択）は iPad の設定とそのアプリで行う。

金額はここでは計算しない。会計で確定した値（Checkout）、見積（sales.services.estimate_tiers / quote）、
締めの集計（cash.services.day_summary）をそのまま印字する。
"""
from __future__ import annotations

from django.db import models

from apps.core.models import Store, TimestampedModel


class PrinterConnection(models.TextChoices):
    TM_PRINT_ASSISTANT = "tm_print_assistant", "Bluetooth（エプソンのアプリ「TM Print Assistant」経由）"


class PrinterConfig(TimestampedModel):
    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="printer_config")
    enabled = models.BooleanField("プリンターを使う", default=False)
    printer_name = models.CharField("プリンター名（画面の表示用）", max_length=40, default="レシートプリンター")
    model_name = models.CharField("機種（例: TM-m30III）", max_length=40, blank=True)
    connection = models.CharField(max_length=30, choices=PrinterConnection.choices, default=PrinterConnection.TM_PRINT_ASSISTANT)
    paper_width = models.PositiveSmallIntegerField("用紙の幅(mm)", default=80)  # 80 / 58

    # このプリンターから出すもの
    print_estimate = models.BooleanField("概算伝票（いま／延長したら）", default=True)
    print_receipt = models.BooleanField("会計伝票（レシート）", default=True)
    print_ryoshusho = models.BooleanField("領収書", default=True)
    print_closing = models.BooleanField("精算伝票（締め）", default=True)

    # レシート・領収書に載せる店の情報（空の店名は店舗名を使う）
    shop_name = models.CharField("印字する店名", max_length=60, blank=True)
    address1 = models.CharField(max_length=60, blank=True)
    address2 = models.CharField(max_length=60, blank=True)
    address3 = models.CharField(max_length=60, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    invoice_number = models.CharField("適格請求書発行事業者の登録番号（T＋13桁）", max_length=14, blank=True)
    receipt_message = models.CharField("レシートの最後に入れる一言", max_length=120, blank=True)

    # 領収書
    ryoshusho_proviso = models.CharField("但し書きの初期値", max_length=40, default="お飲食代として")
    stamp_box_from = models.DecimalField(
        "現金の領収額がこの金額以上なら収入印紙の枠を出す（空 = 出さない）", max_digits=12, decimal_places=0, null=True, blank=True,
    )

    def display_shop_name(self) -> str:
        return self.shop_name or self.store.name

    def columns(self) -> int:
        """
        半角の文字数（フォント A・12 ドット幅、203dpi = 8 ドット/mm）。
        TM-m30III の印字幅（TM Print Assistant の README）: 80mm 用紙 = 72.0mm = 576 ドット = 48 桁、58mm 用紙 = 52.5mm = 420 ドット = 35 桁。
        """
        return 35 if self.paper_width == 58 else 48
