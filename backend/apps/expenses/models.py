"""
領収書・経費の登録ワークフローと買掛金。

- 店舗タブレットで撮った領収書 1 枚＝ ExpenseReceipt 1 件。原本は accounting.Evidence（1 対 1）。
- AI の読取結果（ocr_*）、スタッフの確認値（receipt_date など）、管理者の確定値（approved_*）を別に持ち、AI の値は自動確定しない。
- 承認で会計へ反映する: 店舗現金 → cash.CashMovement（レジ締めと帳簿の両方で同じ 1 件）／それ以外 → accounting.Expense、後日払いは Payable。
- すべての状態遷移は ExpenseReceiptEvent に追記する。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import Company, Store, TimestampedModel


class ReceiptStatus(models.TextChoices):
    DRAFT = "draft", "下書き"
    OCR_RUNNING = "ocr_running", "AI読取中"
    PENDING = "pending", "確認待ち"
    APPROVED = "approved", "承認済み"
    RETURNED = "returned", "差戻し"
    REJECTED = "rejected", "却下"
    VOIDED = "voided", "取消"


class ReceiptPaymentMethod(models.TextChoices):
    STORE_CASH = "store_cash", "店舗現金（レジ）"
    OWNER_PERSONAL = "owner_personal", "オーナー個人資金"
    BANK = "bank", "銀行振込"
    CARD = "card", "クレジットカード"
    OTHER = "other", "その他"


class OcrStatus(models.TextChoices):
    NONE = "none", "未実行"
    RUNNING = "running", "読取中"
    DONE = "done", "読取済み"
    FAILED = "failed", "読取失敗"
    SKIPPED = "skipped", "読取なし（手入力）"


class Payable(TimestampedModel):
    """買掛金・未払金（後日払いの請求・カード払い）。支払いは PayablePayment。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.PROTECT, related_name="payables")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    supplier = models.ForeignKey("inventory.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="payables")
    counterparty = models.CharField("取引先", max_length=100, blank=True)
    invoice_date = models.DateField("請求日・取引日")
    due_date = models.DateField("支払期日", null=True, blank=True)
    amount = models.DecimalField("金額（税込）", max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    tax_breakdown = models.JSONField(default=list, blank=True)
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="payables")
    description = models.CharField(max_length=200, blank=True)
    evidence = models.ForeignKey("accounting.Evidence", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    source = models.CharField(max_length=20, default="manual")  # receipt / manual / purchase_order
    purchase_order = models.ForeignKey("inventory.PurchaseOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="payables")
    status = models.CharField(max_length=20, default="open")  # open / partially_paid / paid / voided
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-invoice_date", "-id"]
        indexes = [models.Index(fields=["taxpayer", "status"]), models.Index(fields=["taxpayer", "invoice_date"])]

    @property
    def balance(self) -> Decimal:
        return self.amount - self.paid_amount

    def refresh_status(self) -> None:
        if self.is_voided:
            self.status = "voided"
        elif self.paid_amount >= self.amount:
            self.status = "paid"
        elif self.paid_amount > 0:
            self.status = "partially_paid"
        else:
            self.status = "open"


class PayablePayment(TimestampedModel):
    payable = models.ForeignKey(Payable, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, default="bank")  # bank / cash_register / owner_personal / card
    # レジ現金で払ったときの出金（kind=expense・科目=買掛金／未払金。日次仕訳がそのまま支払いの仕訳になる）
    cash_movement = models.OneToOneField("cash.CashMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="payable_payment")
    bank_transaction_ref = models.PositiveBigIntegerField("照合した銀行明細の ID", null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]


class ExpenseReceipt(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="expense_receipts")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="expense_receipts")
    taxpayer = models.ForeignKey("accounting.TaxPayer", null=True, blank=True, on_delete=models.PROTECT, related_name="expense_receipts")
    evidence = models.OneToOneField("accounting.Evidence", on_delete=models.PROTECT, related_name="receipt")
    status = models.CharField(max_length=20, choices=ReceiptStatus.choices, default=ReceiptStatus.DRAFT)
    payment_method = models.CharField(max_length=20, choices=ReceiptPaymentMethod.choices, default=ReceiptPaymentMethod.STORE_CASH)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    # 二重登録防止キー（multipart は再送ごとに本文が変わるため、@idempotent ではなくこのキーで同じ登録を返す）
    client_key = models.CharField(max_length=80, blank=True)

    # --- AI 読取（自動確定しない） ---
    ocr_status = models.CharField(max_length=10, choices=OcrStatus.choices, default=OcrStatus.NONE)
    ocr_provider = models.CharField(max_length=20, blank=True)
    ocr_model = models.CharField(max_length=60, blank=True)
    ocr_prompt_version = models.CharField(max_length=20, blank=True)
    ocr_raw = models.JSONField(default=dict, blank=True)
    ocr_date = models.DateField(null=True, blank=True)
    ocr_vendor = models.CharField(max_length=100, blank=True)
    ocr_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    ocr_tax_breakdown = models.JSONField(default=list, blank=True)
    ocr_invoice_number = models.CharField(max_length=14, blank=True)
    ocr_summary = models.CharField(max_length=100, blank=True)
    ocr_suggested_account_key = models.CharField(max_length=40, blank=True)
    ocr_confidence = models.JSONField("missing_fields・warnings など", default=dict, blank=True)
    ocr_cost_jpy = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    ocr_error = models.CharField(max_length=300, blank=True)
    ocr_job = models.ForeignKey("core.BackgroundJob", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    # --- スタッフの確認値（登録値） ---
    receipt_date = models.DateField(null=True, blank=True)
    vendor = models.CharField(max_length=100, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax_breakdown = models.JSONField(default=list, blank=True)
    invoice_number = models.CharField(max_length=14, blank=True)
    summary = models.CharField(max_length=100, blank=True)
    memo = models.CharField(max_length=200, blank=True)

    # --- 管理者の確定値 ---
    approved_date = models.DateField(null=True, blank=True)
    approved_vendor = models.CharField(max_length=100, blank=True)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    approved_tax_breakdown = models.JSONField(default=list, blank=True)
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")
    approved_tax_category = models.CharField(max_length=20, blank=True)
    approved_store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_payment_method = models.CharField(max_length=20, blank=True)
    approved_note = models.CharField(max_length=200, blank=True)

    duplicate_candidates = models.JSONField(default=list, blank=True)
    duplicate_override_reason = models.CharField(max_length=200, blank=True)

    cash_movement = models.OneToOneField("cash.CashMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="expense_receipt")
    expense = models.OneToOneField("accounting.Expense", null=True, blank=True, on_delete=models.SET_NULL, related_name="receipt")
    payable = models.ForeignKey(Payable, null=True, blank=True, on_delete=models.SET_NULL, related_name="receipts")

    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    return_reason = models.CharField(max_length=200, blank=True)
    reject_reason = models.CharField(max_length=200, blank=True)
    corrected_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections")
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["store", "status"]),
            models.Index(fields=["taxpayer", "status"]),
            models.Index(fields=["taxpayer", "receipt_date"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["submitted_by", "client_key"]),
        ]

    def __str__(self) -> str:
        return f"領収書#{self.id} {self.get_status_display()}"

    @property
    def effective_date(self):
        return self.approved_date or self.receipt_date or self.ocr_date

    @property
    def effective_amount(self):
        return self.approved_amount if self.approved_amount is not None else (self.total_amount if self.total_amount is not None else self.ocr_total)

    @property
    def effective_vendor(self) -> str:
        return self.approved_vendor or self.vendor or self.ocr_vendor


class ExpenseReceiptEvent(models.Model):
    """承認・差戻しなどの履歴（追記のみ）。"""

    receipt = models.ForeignKey(ExpenseReceipt, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=20)  # submit / ocr_done / ocr_failed / ocr_skipped / edit / approve / return / resubmit / reject / void / correct
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-id"]
