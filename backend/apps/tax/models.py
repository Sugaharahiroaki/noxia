"""
確定申告（所得税）: 個人事業主の申告情報・年度別の税制設定・申告書データ・版・出力・入力ガイドの進捗・申告関連の監査ログ。

- 税率・控除額はコードに書かず TaxYearConfiguration（年度別 JSON）に持つ。計算のたびに版（TaxReturnVersion）へ設定のハッシュと一緒に凍結する。
- 所得控除などの入力（inputs）は暗号化して保存する。氏名・個人番号は持たない。
- NOXIA は申告書を送信しない。XTX は公式ソフトでの取込試験が完了した年度だけ公開する（settings.TAX_XTX_RELEASED_YEARS）。
"""
from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.accounting.models import FiscalYear, TaxPayer
from apps.core.db_fields import EncryptedTextField
from apps.core.models import TimestampedModel


class IndividualTaxProfile(TimestampedModel):
    """個人事業主の申告情報（TaxPayer と 1 対 1。法人には作らない）。"""

    taxpayer = models.OneToOneField(TaxPayer, on_delete=models.CASCADE, related_name="individual_profile")
    individual_name = models.CharField("氏名", max_length=60, blank=True)
    trade_name = models.CharField("屋号", max_length=100, blank=True)
    business_start_date = models.DateField("開業日", null=True, blank=True)
    blue_approval_status = models.CharField(max_length=10, default="none")  # none / applied / approved / revoked
    blue_approved_from_year = models.PositiveIntegerField(null=True, blank=True)
    blue_application_date = models.DateField(null=True, blank=True)
    bookkeeping_method = models.CharField(max_length=10, default="simple")  # simple / double
    consumption_tax_status = models.CharField(max_length=20, default="exempt")  # exempt / taxable_general / taxable_simplified / taxable_2wari
    invoice_registration_number = models.CharField("適格請求書発行事業者の登録番号", max_length=14, blank=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "個人事業主の申告情報"

    def blue_for_year(self, year: int) -> bool:
        return self.blue_approval_status == "approved" and (self.blue_approved_from_year or 9999) <= year


class TaxYearConfiguration(TimestampedModel):
    """年度別の税制・様式・入力ガイドの定義。公式資料で確認できた項目だけ verified にする。"""

    year = models.PositiveIntegerField(unique=True)
    status = models.CharField(max_length=10, default="draft")  # draft / verified / published
    income_tax = models.JSONField(default=dict)
    surtaxes = models.JSONField(default=dict)
    deductions = models.JSONField(default=dict)
    statement_lines = models.JSONField(default=dict)
    guide = models.JSONField(default=dict)
    forms = models.JSONField(default=dict)
    sources = models.JSONField(default=list)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "年度別税制設定"
        ordering = ["-year"]

    def compute_hash(self) -> str:
        payload = json.dumps({"income_tax": self.income_tax, "surtaxes": self.surtaxes, "deductions": self.deductions,
                              "statement_lines": self.statement_lines, "year": self.year}, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()

    def save(self, *args, **kwargs):
        self.content_hash = self.compute_hash()
        return super().save(*args, **kwargs)

    def as_dict(self) -> dict:
        return {"year": self.year, "status": self.status, "income_tax": self.income_tax, "surtaxes": self.surtaxes, "deductions": self.deductions,
                "statement_lines": self.statement_lines, "guide": self.guide, "forms": self.forms, "sources": self.sources, "note": self.note,
                "content_hash": self.content_hash, "verified_at": self.verified_at}


class TaxReturnStatus(models.TextChoices):
    DRAFT = "draft", "作成中"
    IN_REVIEW = "in_review", "確認中（未対応の項目あり）"
    COMPLETE = "complete", "完成（転記できます）"
    EXPORTED = "exported", "出力済み"
    FILED = "filed_by_user", "申告済み（利用者の記録）"
    LOCKED = "locked", "ロック"


class TaxReturn(TimestampedModel):
    """事業者×年度の申告書作成データ。inputs は暗号化。"""

    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="tax_returns")
    fiscal_year = models.OneToOneField(FiscalYear, on_delete=models.PROTECT, related_name="tax_return")
    config = models.ForeignKey(TaxYearConfiguration, on_delete=models.PROTECT, related_name="returns")
    filing_type = models.CharField(max_length=10, default="white")  # white / blue_10
    status = models.CharField(max_length=15, choices=TaxReturnStatus.choices, default=TaxReturnStatus.DRAFT)
    inputs_enc = EncryptedTextField(blank=True)  # JSON 文字列（暗号化）
    business_summary = models.JSONField(default=dict, blank=True)
    computed = models.JSONField(default=dict, blank=True)
    unsupported_items = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    version = models.PositiveIntegerField(default=1)
    completed_at = models.DateTimeField(null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    filed_at = models.DateTimeField(null=True, blank=True)
    filed_note = models.CharField(max_length=200, blank=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        verbose_name = verbose_name_plural = "申告書データ"
        ordering = ["-fiscal_year__year"]

    @property
    def inputs(self) -> dict:
        if not self.inputs_enc:
            return {}
        try:
            return json.loads(self.inputs_enc)
        except ValueError:
            return {}

    @inputs.setter
    def inputs(self, value: dict) -> None:
        self.inputs_enc = json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)


class TaxReturnVersion(models.Model):
    """計算を実行するたびに追記する版。inputs・computed・設定のハッシュ・仕訳のハッシュを凍結する。"""

    tax_return = models.ForeignKey(TaxReturn, on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reason = models.CharField(max_length=100, blank=True)
    inputs_enc = EncryptedTextField(blank=True)
    business_summary = models.JSONField(default=dict, blank=True)
    computed = models.JSONField(default=dict, blank=True)
    unsupported_items = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    config_hash = models.CharField(max_length=64, blank=True)
    journal_snapshot_hash = models.CharField(max_length=64, blank=True)
    is_final = models.BooleanField(default=False)

    class Meta:
        ordering = ["-version_no"]
        constraints = [models.UniqueConstraint(fields=["tax_return", "version_no"], name="uniq_tax_return_version")]

    @property
    def inputs(self) -> dict:
        try:
            return json.loads(self.inputs_enc) if self.inputs_enc else {}
        except ValueError:
            return {}


class TaxExport(TimestampedModel):
    version = models.ForeignKey(TaxReturnVersion, on_delete=models.CASCADE, related_name="exports")
    format = models.CharField(max_length=40)  # pdf_forms / pdf_guide / xlsx_return / xtx
    file = models.FileField(upload_to="tax_exports/%Y/%m/")
    filename = models.CharField(max_length=200)
    sha256 = models.CharField(max_length=64)
    size = models.PositiveIntegerField(default=0)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    validation = models.JSONField(default=dict, blank=True)
    is_released = models.BooleanField(default=True)
    download_count = models.PositiveIntegerField(default=0)
    last_downloaded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]


class GuideProgress(models.Model):
    tax_return = models.ForeignKey(TaxReturn, on_delete=models.CASCADE, related_name="guide_progress")
    item_key = models.CharField(max_length=60)
    checked = models.BooleanField(default=False)
    checked_at = models.DateTimeField(null=True, blank=True)
    copied_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tax_return", "item_key"], name="uniq_guide_progress")]


class TaxAuditLog(models.Model):
    """申告関連の監査ログ（追記専用・ハッシュ連鎖）。一般の操作は core.AuditLog。"""

    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="tax_audit_logs")
    fiscal_year = models.ForeignKey(FiscalYear, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax_return = models.ForeignKey(TaxReturn, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=40)
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["taxpayer", "created_at"])]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("申告の監査ログは変更できません。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("申告の監査ログは削除できません。")


def record_tax_audit(taxpayer: TaxPayer, action: str, *, user=None, tax_return: TaxReturn | None = None, fiscal_year: FiscalYear | None = None,
                     detail: dict | None = None) -> TaxAuditLog:
    """ハッシュ連鎖で追記する（直前の行のハッシュを含めて sha256）。個人情報は detail に入れない。"""
    prev = TaxAuditLog.objects.filter(taxpayer=taxpayer).order_by("-id").first()
    prev_hash = prev.hash if prev else ""
    now = timezone.now()
    payload = json.dumps({"prev": prev_hash, "action": action, "detail": detail or {}, "at": now.isoformat(), "user": getattr(user, "id", None),
                          "return": getattr(tax_return, "id", None)}, sort_keys=True, ensure_ascii=False, default=str)
    return TaxAuditLog.objects.create(
        taxpayer=taxpayer, fiscal_year=fiscal_year or (tax_return.fiscal_year if tax_return else None), tax_return=tax_return, user=user, action=action,
        detail=detail or {}, created_at=now, prev_hash=prev_hash, hash=hashlib.sha256(payload.encode()).hexdigest(),
    )


def verify_chain(taxpayer: TaxPayer) -> bool:
    prev = ""
    for row in TaxAuditLog.objects.filter(taxpayer=taxpayer).order_by("id"):
        if row.prev_hash != prev:
            return False
        payload = json.dumps({"prev": row.prev_hash, "action": row.action, "detail": row.detail, "at": row.created_at.isoformat(), "user": row.user_id,
                              "return": row.tax_return_id}, sort_keys=True, ensure_ascii=False, default=str)
        if hashlib.sha256(payload.encode()).hexdigest() != row.hash:
            return False
        prev = row.hash
    return True
