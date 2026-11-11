"""
銀行: 口座・CSV の列対応・取込ファイル・明細・照合（消込）。

- 銀行明細は「照合するだけ」で、売上・経費にはしない（元データの仕訳の経路は1本: tax_database_design.md §7）。
- 取込ファイルは sha256 で、明細は row_hash で二重取込を防ぐ。残高列があれば前回の最終残高との連続性を確かめる。
- 照合は「明細1行 ⇄ 対象1件」を1行で表し、多対一・一対多は複数行（配分額）で表す。解除は status=voided（履歴を残す）。
- 口座番号は下4桁だけ保存する。ファイルは MEDIA_ROOT に置き、公開 URL は作らない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.models import Company, TimestampedModel


class BankAccountType(models.TextChoices):
    ORDINARY = "ordinary", "普通"
    CHECKING = "checking", "当座"
    SAVINGS = "savings", "貯蓄"


class BankAccount(TimestampedModel):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="bank_accounts")
    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.PROTECT, related_name="bank_accounts")
    name = models.CharField("表示名", max_length=60)
    bank_name = models.CharField("銀行名", max_length=60, blank=True)
    bank_code = models.CharField("銀行コード（4桁）", max_length=4, blank=True)
    branch_name = models.CharField("支店名", max_length=60, blank=True)
    branch_code = models.CharField("支店コード（3桁）", max_length=3, blank=True)
    account_type = models.CharField(max_length=10, choices=BankAccountType.choices, default=BankAccountType.ORDINARY)
    account_number_last4 = models.CharField("口座番号の下4桁", max_length=4, blank=True)
    holder_name = models.CharField("名義", max_length=60, blank=True)
    # 普通預金の勘定科目（事業者の標準科目 key="bank"）。口座別の内訳は JournalLine.bank_account_ref
    account = models.ForeignKey("accounting.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="bank_accounts")
    opening_balance = models.DecimalField("取込開始時の残高", max_digits=12, decimal_places=2, default=Decimal("0"))
    opening_balance_date = models.DateField(null=True, blank=True)
    is_business = models.BooleanField("事業専用の口座（兼用なら False）", default=True)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "銀行口座"
        ordering = ["taxpayer_id", "id"]

    def __str__(self) -> str:
        return self.name


class BankImportProfile(TimestampedModel):
    """銀行ごとの CSV の列対応（一度決めたら使い回す）。"""

    taxpayer = models.ForeignKey("accounting.TaxPayer", on_delete=models.CASCADE, related_name="bank_import_profiles")
    bank_account = models.ForeignKey(BankAccount, null=True, blank=True, on_delete=models.CASCADE, related_name="import_profiles")
    name = models.CharField(max_length=80)
    encoding = models.CharField(max_length=20, default="auto")  # auto / cp932 / utf-8 / utf-8-sig / euc_jp
    delimiter = models.CharField(max_length=5, default="auto")
    header_rows = models.PositiveSmallIntegerField("見出しまでの行数（0 = 自動）", default=0)
    footer_rows = models.PositiveSmallIntegerField(default=0)
    # {"date": 0, "description": 1, "withdrawal": 2, "deposit": 3, "balance": 4, "counterparty": null, "amount_signed": null}
    column_map = models.JSONField(default=dict)
    date_format = models.CharField(max_length=40, blank=True)
    amount_rules = models.JSONField(default=dict, blank=True)  # {"negative_is_withdrawal": true}
    sample_hash = models.CharField(max_length=64, blank=True)
    header_signature = models.CharField("見出し行のハッシュ（同じ見出しなら自動適用）", max_length=64, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["taxpayer_id", "id"]


class ImportStatus(models.TextChoices):
    PARSED = "parsed", "確認待ち"
    COMMITTED = "committed", "取込済み"
    ROLLED_BACK = "rolled_back", "取消"


class BankStatementImport(TimestampedModel):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="imports")
    profile = models.ForeignKey(BankImportProfile, null=True, blank=True, on_delete=models.SET_NULL, related_name="imports")
    file = models.FileField(upload_to="bank_imports/%Y/%m/")
    sha256 = models.CharField(max_length=64)
    original_name = models.CharField(max_length=200)
    encoding_detected = models.CharField(max_length=20, blank=True)
    column_map = models.JSONField(default=dict, blank=True)
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0)
    rows_error = models.PositiveIntegerField(default=0)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    balance_check = models.CharField(max_length=10, default="unknown")  # ok / mismatch / unknown
    balance_note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=12, choices=ImportStatus.choices, default=ImportStatus.PARSED)
    error_log = models.JSONField(default=list, blank=True)
    imported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    committed_at = models.DateTimeField(null=True, blank=True)
    rolled_back_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]
        constraints = [
            # 同じファイルの二重取込を防ぐ（取消したものは再取込できる）
            models.UniqueConstraint(fields=["bank_account", "sha256"], condition=~Q(status="rolled_back"), name="uniq_bank_import_sha256_active"),
        ]


class TxStatus(models.TextChoices):
    UNMATCHED = "unmatched", "未照合"
    SUGGESTED = "suggested", "候補あり"
    MATCHED = "matched", "照合済み"
    PARTIAL = "partially_matched", "一部照合"
    IGNORED = "ignored", "対象外"
    TRANSFER = "transfer", "口座間の振替"


class BankTransaction(TimestampedModel):
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name="transactions")
    statement_import = models.ForeignKey(BankStatementImport, on_delete=models.CASCADE, related_name="transactions")
    row_no = models.PositiveIntegerField()
    date = models.DateField()
    description = models.CharField(max_length=200, blank=True)
    counterparty = models.CharField("相手名（正規化）", max_length=100, blank=True)
    amount_in = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    amount_out = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    balance = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    raw = models.JSONField(default=list, blank=True)
    row_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=TxStatus.choices, default=TxStatus.UNMATCHED)
    ignore_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["date", "row_no", "id"]
        constraints = [models.UniqueConstraint(fields=["bank_account", "row_hash"], name="uniq_bank_tx_row_hash")]
        indexes = [models.Index(fields=["bank_account", "date"]), models.Index(fields=["bank_account", "status"])]

    @property
    def amount(self) -> Decimal:
        return self.amount_in if self.amount_in else self.amount_out

    @property
    def direction(self) -> str:
        return "in" if self.amount_in else "out"


class MatchType(models.TextChoices):
    CARD_SETTLEMENT = "card_settlement", "カード売上の入金"
    BANK_DEPOSIT = "bank_deposit", "売上金の預入"
    EMPLOYEE_PAYMENT = "employee_payment", "スタッフへの振込"
    EXPENSE = "expense", "経費の支払い"
    PAYABLE_PAYMENT = "payable_payment", "買掛金・未払金の支払い"
    OWNER_TRANSFER = "owner_transfer", "経営者との資金移動"
    TAX_REMITTANCE = "tax_remittance", "源泉所得税の納付"
    TRANSFER = "transfer", "自社口座間の振替"
    MANUAL_JOURNAL = "manual_journal", "手入力の仕訳"


class RecStatus(models.TextChoices):
    SUGGESTED = "suggested", "候補"
    CONFIRMED = "confirmed", "確定"
    VOIDED = "voided", "解除"


class BankReconciliation(TimestampedModel):
    """照合＝消込。1件は明細1行 ⇄ 対象1件。"""

    bank_transaction = models.ForeignKey(BankTransaction, on_delete=models.CASCADE, related_name="reconciliations")
    match_type = models.CharField(max_length=20, choices=MatchType.choices)
    card_settlement = models.ForeignKey("accounting.CardSettlement", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    bank_deposit = models.ForeignKey("cash.BankDeposit", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    employee_payment = models.ForeignKey("staff.EmployeePayment", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    expense = models.ForeignKey("accounting.Expense", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    # expenses.PayablePayment の ID（expenses アプリは並行して開発中のため FK にしない）
    payable_payment_ref = models.PositiveBigIntegerField(null=True, blank=True)
    owner_transfer = models.ForeignKey("accounting.OwnerFundTransfer", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    tax_remittance = models.ForeignKey("accounting.TaxRemittance", null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciliations")
    counter_transaction = models.ForeignKey(BankTransaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="counter_reconciliations")
    journal = models.ForeignKey("accounting.Journal", null=True, blank=True, on_delete=models.SET_NULL, related_name="bank_reconciliations")
    amount = models.DecimalField("配分額（対象側の金額）", max_digits=12, decimal_places=2)
    fee_amount = models.DecimalField("手数料差", max_digits=12, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=10, choices=RecStatus.choices, default=RecStatus.SUGGESTED)
    rule = models.CharField("候補を出したルール", max_length=40, blank=True)
    score = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    label = models.CharField(max_length=200, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    store = models.ForeignKey("core.Store", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-id"]
        indexes = [models.Index(fields=["bank_transaction", "status"]), models.Index(fields=["match_type", "status"])]

    @property
    def target_id(self):
        for f in ("card_settlement_id", "bank_deposit_id", "employee_payment_id", "expense_id", "payable_payment_ref", "owner_transfer_id",
                  "tax_remittance_id", "counter_transaction_id", "journal_id"):
            v = getattr(self, f, None)
            if v:
                return v
        return None
