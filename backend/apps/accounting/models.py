"""
経理: 事業者（源泉徴収義務者）・キャスト契約・源泉所得税・月次管理・納付・証憑・経費・仕訳・freee 連携・記帳代行の権限。

- 会社(Company)は NOXIA の契約単位。税務上の「事業者」(TaxPayer)は会社の中に1つ以上あり、店舗はどれか1つの事業者に属する。
  源泉所得税の法定集計（徴収高計算書）は事業者単位、店舗別の表示は管理用の内訳。
- 税率・控除額はコードに固定せず、適用開始日付きの WithholdingRule に保存する。計算結果には使ったルールと計算根拠を残す。
- 支払い確定後の記録は書き換えない。取消は is_voided、訂正は WithholdingAdjustment で履歴を残す。
- 個人番号（マイナンバー）は保存しない。
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.db_fields import EncryptedTextField
from apps.core.models import Company, Store, TimestampedModel


# --- 事業者 -------------------------------------------------------------------
class EntityType(models.TextChoices):
    CORPORATION = "corporation", "法人"
    SOLE_PROPRIETOR = "sole_proprietor", "個人事業主"


class TaxPayer(TimestampedModel):
    """源泉徴収義務者・記帳の単位となる事業者（法人または個人事業主）。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="taxpayers")
    name = models.CharField("事業者名（法人名、または屋号・氏名）", max_length=100)
    entity_type = models.CharField(max_length=20, choices=EntityType.choices)
    corporate_number = models.CharField("法人番号（13桁・法人のみ）", max_length=13, blank=True)
    representative_name = models.CharField("代表者名", max_length=60, blank=True)
    address = models.CharField("所在地（納税地）", max_length=200, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    tax_office_name = models.CharField("所轄税務署", max_length=40, blank=True)
    reference_number = models.CharField("源泉所得税の整理番号（税務署から通知）", max_length=20, blank=True)
    # 給与等の納期の特例（所得税法216条）。ホステス報酬には適用されない（毎月納付）
    payroll_due_special = models.BooleanField("給与等の納期の特例の承認を受けている", default=False)
    fiscal_year_end_month = models.PositiveSmallIntegerField("決算月", default=12)
    # 申告のやり方。freee のときは銀行照合・固定資産・年間決算・確定申告の画面を出さない（freee 側で行う）
    filing_method = models.CharField("申告方法", max_length=12, blank=True, default="",
                                     choices=[("", "未設定"), ("freee", "freee で申告（仕訳を freee に渡す）"), ("nta_corner", "作成コーナーへ転記（NOXIA の画面を使う）")])
    is_active = models.BooleanField(default=True)
    note = models.TextField(blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "事業者"
        ordering = ["company_id", "id"]

    def __str__(self) -> str:
        return self.name


class StoreTaxProfile(TimestampedModel):
    """店舗がどの事業者の店か。未設定の店舗の源泉計算は「要確認」になる。"""

    store = models.OneToOneField(Store, on_delete=models.CASCADE, related_name="tax_profile")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="store_profiles")


# --- 記帳代行・税理士等の権限 ------------------------------------------------------
class AccountingRole(models.TextChoices):
    BOOKKEEPER = "bookkeeper", "記帳代行担当者"
    TAX_ADVISOR = "tax_advisor", "税理士等の外部専門家"


class AccountingAccessGrant(TimestampedModel):
    """会社オーナーが記帳代行担当者・税理士等に経理データの閲覧・入力を許可した記録（会社単位）。"""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="accounting_grants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounting_grants")
    role = models.CharField(max_length=20, choices=AccountingRole.choices)
    is_active = models.BooleanField(default=True)
    expires_on = models.DateField("有効期限（任意）", null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "user"], name="uniq_accounting_grant")]


# --- キャスト契約・報酬項目 -----------------------------------------------------------
class ContractType(models.TextChoices):
    EMPLOYMENT = "employment", "雇用契約"
    OUTSOURCING = "outsourcing", "業務委託契約"
    OTHER = "other", "その他（税務確認が必要）"


class TaxCategory(models.TextChoices):
    SALARY = "salary", "給与所得"
    HOSTESS_FEE = "hostess_fee", "ホステス等の報酬・料金"
    OUTSOURCING_FEE = "outsourcing_fee", "一般業務委託費（源泉徴収なし・要確認）"
    UNDETERMINED = "undetermined", "未確定（要確認）"


class TaxReviewStatus(models.TextChoices):
    UNREVIEWED = "unreviewed", "未確認"
    CONFIRMED = "confirmed", "確認済み"
    NEEDS_REVIEW = "needs_review", "要確認（専門家に相談中など）"


class CalcPeriodType(models.TextChoices):
    DAILY = "daily", "日ごと"
    WEEKLY = "weekly", "週ごと（月曜〜日曜）"
    SEMIMONTHLY = "semimonthly", "半月ごと（1〜15日／16日〜末日）"
    MONTHLY = "monthly", "月ごと（1日〜末日）"
    UNSET = "unset", "未設定（要確認）"


class CastContract(TimestampedModel):
    """
    キャストの契約。契約名称（雇用／業務委託）だけで税務区分を決めない。
    税務区分は経営者が実態を確認して「確認済み」にしたときだけ計算に使う（それまでは要確認）。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="cast_contracts")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="contracts")
    contract_type = models.CharField(max_length=20, choices=ContractType.choices)
    start_date = models.DateField("契約開始日")
    end_date = models.DateField("契約終了日", null=True, blank=True)
    calc_period = models.CharField("報酬計算期間", max_length=20, choices=CalcPeriodType.choices, default=CalcPeriodType.UNSET)
    payment_method = models.CharField("支払方法", max_length=20, default="daily_cash")  # daily_cash / monthly_bank / mixed / other
    payment_note = models.CharField(max_length=200, blank=True)
    tax_category = models.CharField(max_length=20, choices=TaxCategory.choices, default=TaxCategory.UNDETERMINED)
    tax_review_status = models.CharField(max_length=20, choices=TaxReviewStatus.choices, default=TaxReviewStatus.UNREVIEWED)
    tax_reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    tax_reviewed_at = models.DateTimeField(null=True, blank=True)
    review_checklist = models.JSONField("実態確認のチェック", default=dict, blank=True)
    review_note = models.TextField("確認メモ", blank=True)
    advisor_note = models.TextField("税理士等のコメント", blank=True)
    advisor_noted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    advisor_noted_at = models.DateTimeField(null=True, blank=True)
    # 源泉所得税とは別の控除（厚生費など）。[{"code","label","calc":"percent|per_day|per_payment","value"}]
    deductions = models.JSONField(default=list, blank=True)
    document = models.FileField("契約書", upload_to="contracts/%Y/%m/", blank=True)
    document_name = models.CharField(max_length=200, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["employee_id", "-start_date", "-id"]
        indexes = [models.Index(fields=["company", "employee", "is_active"])]


class PayItemSetting(TimestampedModel):
    """報酬項目ごとの税務上の扱い（会社単位）。行がなければ compensation.DEFAULT_PAY_ITEMS の既定値を使う。"""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_item_settings")
    code = models.CharField(max_length=40)
    label = models.CharField(max_length=60)
    withholding_target = models.BooleanField("ホステス報酬の源泉徴収の対象", default=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_pay_item_per_company")]


# --- 源泉所得税 ---------------------------------------------------------------------
class WithholdingRule(TimestampedModel):
    """
    源泉徴収の計算ルール（全社共通。運営者が管理）。税制改正は新しい行を追加し、過去の行は変更しない。
    category=hostess_fee: 税額 = (源泉対象額 − daily_deduction × 計算期間の暦日数) × rate_percent%（rounding で端数処理）
    """

    category = models.CharField(max_length=20, default=TaxCategory.HOSTESS_FEE)
    version = models.CharField("ルール名・版", max_length=80)
    effective_from = models.DateField("適用開始日（支払日基準）")
    effective_to = models.DateField("適用終了日", null=True, blank=True)
    rate_percent = models.DecimalField("税率(%)", max_digits=6, decimal_places=3)
    components = models.JSONField("税率の内訳", default=list, blank=True)
    daily_deduction = models.DecimalField("1日あたりの控除額", max_digits=10, decimal_places=2)
    rounding = models.CharField(max_length=10, default="floor")
    legal_basis = models.TextField("根拠法令", blank=True)
    source_url = models.URLField("確認した資料", blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = verbose_name_plural = "源泉徴収ルール"
        ordering = ["category", "-effective_from"]
        indexes = [models.Index(fields=["category", "effective_from"])]

    def __str__(self) -> str:
        return self.version


class WithholdingStatus(models.TextChoices):
    CALCULATED = "calculated", "計算済み"
    NEEDS_REVIEW = "needs_review", "要確認"
    EXTERNAL = "external", "給与（外部の給与計算で源泉徴収）"
    VOIDED = "voided", "取消"


class WithholdingRecord(TimestampedModel):
    """
    キャストへの1回の支払いの源泉徴収明細。支払い（staff.EmployeePayment）と1対1。
    集計は実際の支払日（paid_on）の年月で行う。報酬計算期間（period_*）は支払日とは別に記録する。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, null=True, blank=True, on_delete=models.PROTECT, related_name="withholding_records")
    store = models.ForeignKey(Store, on_delete=models.PROTECT, related_name="withholding_records")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="withholding_records")
    payment = models.OneToOneField("staff.EmployeePayment", on_delete=models.PROTECT, related_name="withholding")
    contract = models.ForeignKey(CastContract, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    paid_at = models.DateTimeField()
    paid_on = models.DateField("支払日")
    work_date = models.DateField("勤務日（営業日）", null=True, blank=True)
    tax_category = models.CharField(max_length=20, choices=TaxCategory.choices)
    status = models.CharField(max_length=20, choices=WithholdingStatus.choices)
    review_reasons = models.JSONField(default=list, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    period_days = models.PositiveIntegerField(default=0)
    gross_amount = models.DecimalField("報酬総額（この支払いで精算した額）", max_digits=12, decimal_places=2)
    target_amount = models.DecimalField("源泉徴収の対象額", max_digits=12, decimal_places=2)
    deduction_available = models.DecimalField("使える控除額", max_digits=12, decimal_places=2, default=Decimal("0"))
    deduction_applied = models.DecimalField("控除した額（支払額からの控除額）", max_digits=12, decimal_places=2, default=Decimal("0"))
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"))
    rule = models.ForeignKey(WithholdingRule, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    rule_version = models.CharField(max_length=80, blank=True)
    rate_percent = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))
    withholding_amount = models.DecimalField("源泉所得税", max_digits=12, decimal_places=2, default=Decimal("0"))
    other_deduction_amount = models.DecimalField("その他控除", max_digits=12, decimal_places=2, default=Decimal("0"))
    net_amount = models.DecimalField("差引支給額", max_digits=12, decimal_places=2)
    items = models.JSONField("報酬項目の内訳", default=list, blank=True)
    other_deductions = models.JSONField("その他控除の内訳", default=list, blank=True)
    calc_basis = models.JSONField("計算根拠", default=dict, blank=True)
    engine_version = models.CharField(max_length=40, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-paid_at", "-id"]
        indexes = [
            models.Index(fields=["taxpayer", "tax_category", "paid_on"]),
            models.Index(fields=["store", "paid_on"]),
            models.Index(fields=["employee", "paid_on"]),
        ]


class DeductionUsage(models.Model):
    """
    控除額（1日あたり5,000円など）の使用台帳。事業者×キャスト×日ごとに上限まで。
    同じ計算期間への追加支払い・期間が重なる支払いで控除を重複して使わないためのもの。
    """

    record = models.ForeignKey(WithholdingRecord, null=True, blank=True, on_delete=models.CASCADE, related_name="deduction_usages")
    salary_entry = models.ForeignKey("SalaryWithholdingEntry", null=True, blank=True, on_delete=models.CASCADE, related_name="deduction_usages")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="+")
    employee = models.ForeignKey("staff.Employee", on_delete=models.CASCADE, related_name="+")
    date = models.DateField()
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        indexes = [models.Index(fields=["taxpayer", "employee", "date"])]


class SalaryKind(models.TextChoices):
    REGULAR = "regular", "俸給・給料等"
    BONUS = "bonus", "賞与"
    DAILY_WORKER = "daily_worker", "日雇労務者の賃金（丙欄）"


class SalaryWithholdingEntry(TimestampedModel):
    """
    給与の源泉徴収額（外部の給与計算ソフトで計算した結果を記録する）。NOXIA は給与の税額表による計算をしない。
    period_* を入れると、同じ期間のホステス報酬の控除額から給与の額を差し引く（所得税法施行令322条）。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="salary_entries")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="salary_entries")
    kind = models.CharField(max_length=20, choices=SalaryKind.choices, default=SalaryKind.REGULAR)
    paid_on = models.DateField("支給日")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    gross_amount = models.DecimalField("支給額", max_digits=12, decimal_places=2)
    withholding_amount = models.DecimalField("源泉徴収税額", max_digits=12, decimal_places=2, default=Decimal("0"))
    headcount = models.PositiveIntegerField("人員（従業員を指定しない合計入力の場合）", default=1)
    source = models.CharField(max_length=30, default="external_payroll")  # external_payroll / noxia_payment
    payment = models.ForeignKey("staff.EmployeePayment", null=True, blank=True, on_delete=models.SET_NULL, related_name="salary_entries")
    note = models.CharField(max_length=200, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-paid_on", "-id"]
        indexes = [models.Index(fields=["taxpayer", "paid_on"])]


class AdjustmentKind(models.TextChoices):
    ADDITIONAL = "additional", "追加徴収（計算誤りの訂正など）"
    REFUND = "refund", "過納額の精算（過払い報酬の返還など）"
    UNWITHHELD = "unwithheld", "支払時に未徴収だった税額（支払者が納付）"
    CORRECTION = "correction", "その他の訂正"


class WithholdingAdjustment(TimestampedModel):
    """源泉税額の訂正。元の明細は書き換えず、訂正を記録した日の月に増減として集計する。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="withholding_adjustments")
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    record = models.ForeignKey(WithholdingRecord, null=True, blank=True, on_delete=models.SET_NULL, related_name="adjustments")
    category = models.CharField(max_length=20, choices=TaxCategory.choices, default=TaxCategory.HOSTESS_FEE)
    kind = models.CharField(max_length=20, choices=AdjustmentKind.choices)
    applies_on = models.DateField("計上日（この日の月に集計）")
    target_delta = models.DecimalField("対象額の増減", max_digits=12, decimal_places=2, default=Decimal("0"))
    deduction_delta = models.DecimalField("控除額の増減", max_digits=12, decimal_places=2, default=Decimal("0"))
    amount_delta = models.DecimalField("税額の増減", max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=300)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-applies_on", "-id"]


class MonthStatus(models.TextChoices):
    NOT_AGGREGATED = "not_aggregated", "未集計"
    AGGREGATED = "aggregated", "集計済み"
    NEEDS_REVIEW = "needs_review", "要確認"
    OWNER_CONFIRMED = "owner_confirmed", "経営者確認済み"
    AWAITING_PAYMENT = "awaiting_payment", "納付待ち"
    PAID = "paid", "納付済み"
    OVERDUE = "overdue", "期限超過"
    CORRECTED = "corrected", "訂正あり"


class WithholdingMonth(TimestampedModel):
    """
    月次（納期の特例を受けた給与は半年）の源泉所得税の集計と確認・納付の状態。
    snapshot は集計・確認した時点の数字。現在の数字と違えば「訂正あり」として再確認を求める。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="withholding_months")
    category = models.CharField(max_length=20, choices=TaxCategory.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=20, choices=MonthStatus.choices, default=MonthStatus.AGGREGATED)
    snapshot = models.JSONField(default=dict, blank=True)
    snapshot_hash = models.CharField(max_length=64, blank=True)
    aggregated_at = models.DateTimeField(null=True, blank=True)
    aggregated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    advisor_note = models.TextField(blank=True)
    advisor_checked_at = models.DateTimeField(null=True, blank=True)
    advisor_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    due_date = models.DateField()

    class Meta:
        ordering = ["-period_start", "category"]
        constraints = [models.UniqueConstraint(fields=["taxpayer", "category", "period_start"], name="uniq_withholding_month")]


class RemittanceMethod(models.TextChoices):
    ETAX_DIRECT = "etax_direct", "e-Tax（ダイレクト納付）"
    ETAX_BANKING = "etax_banking", "e-Tax（インターネットバンキング等）"
    CREDIT_CARD = "credit_card", "クレジットカード納付"
    QR_CONVENIENCE = "qr_convenience", "コンビニ納付（QRコード）"
    COUNTER = "counter", "金融機関・税務署の窓口（納付書）"
    OTHER = "other", "その他"


class TaxRemittance(TimestampedModel):
    """源泉所得税の納付記録（経営者が e-Tax 等で納付したあとに記録する。NOXIA は納付を代行しない）。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="remittances")
    month = models.ForeignKey(WithholdingMonth, on_delete=models.PROTECT, related_name="remittances")
    paid_on = models.DateField("納付日")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=RemittanceMethod.choices)
    paid_from = models.CharField("納付に使ったお金", max_length=20, default="bank")  # bank / cash_register / owner_personal
    cash_movement = models.OneToOneField("cash.CashMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="tax_remittance")
    evidence = models.ForeignKey("Evidence", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-paid_on", "-id"]


# --- 証憑・経費・資金移動 ---------------------------------------------------------------
class EvidenceKind(models.TextChoices):
    RECEIPT = "receipt", "領収書"
    INVOICE = "invoice", "請求書"
    TAX_PAYMENT = "tax_payment", "納付証憑"
    CONTRACT = "contract", "契約書"
    STATEMENT = "statement", "明細書・その他"


class Evidence(TimestampedModel):
    """
    証憑ファイル。電子帳簿保存法の検索要件（取引年月日・金額・取引先）を項目として持つ。
    削除は論理削除（理由・削除者を残す）で、ファイルは保存期間内は消さない。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="evidences")
    taxpayer = models.ForeignKey(TaxPayer, null=True, blank=True, on_delete=models.PROTECT, related_name="evidences")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=20, choices=EvidenceKind.choices)
    file = models.FileField(upload_to="evidence/%Y/%m/")
    original_name = models.CharField(max_length=200)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64)
    transaction_date = models.DateField("取引年月日", null=True, blank=True)
    amount = models.DecimalField("取引金額", max_digits=12, decimal_places=2, null=True, blank=True)
    counterparty = models.CharField("取引先", max_length=100, blank=True)
    description = models.CharField(max_length=200, blank=True)
    retention_until = models.DateField("保存期限", null=True, blank=True)
    # 電子帳簿保存法: 電子取引データ（PDF・メール等で受け取ったもの）と、紙を撮影・スキャンしたものを区別する
    origin = models.CharField("原本の種類", max_length=20, default="paper_photo")  # paper_photo / scan / electronic
    phash = models.CharField("知覚ハッシュ（類似画像の検出用）", max_length=16, blank=True)
    captured_at = models.DateTimeField("撮影・受領日時", null=True, blank=True)
    page_count = models.PositiveSmallIntegerField(default=1)
    image_width = models.PositiveIntegerField(null=True, blank=True)
    image_height = models.PositiveIntegerField(null=True, blank=True)
    quality_flags = models.JSONField("画質などの注意", default=list, blank=True)
    is_deleted = models.BooleanField(default=False)
    deleted_reason = models.CharField(max_length=200, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "transaction_date"]), models.Index(fields=["company", "counterparty"]),
                   models.Index(fields=["company", "sha256"]), models.Index(fields=["company", "phash"])]


class ExpensePaidFrom(models.TextChoices):
    BANK = "bank", "事業用口座"
    CARD = "card", "事業用カード（後日引落し）"
    OFFICE_CASH = "office_cash", "手許現金（レジ以外）"
    OWNER_PERSONAL = "owner_personal", "経営者の個人のお金で立替"


class Expense(TimestampedModel):
    """
    レジ現金以外で払った経費（レジ現金の経費は cash.CashMovement の expense で記録済み）。
    訂正は取消＋新規登録（corrected_from）で履歴を残す。経営者の個人資金での支払いは事業主借等で仕訳する。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="expenses")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    date = models.DateField()
    amount = models.DecimalField("金額（税込）", max_digits=12, decimal_places=2)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("10.00"))
    account_key = models.CharField("勘定科目", max_length=40, default="misc_expense")
    account = models.ForeignKey("Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="expenses")
    # 8%/10% が混在する領収書の内訳 [{"rate": "8", "taxable_incl": "1080", "tax": "80"}]。空なら tax_rate で単一税率
    tax_breakdown = models.JSONField(default=list, blank=True)
    counterparty = models.CharField(max_length=100, blank=True)
    invoice_number = models.CharField("適格請求書発行事業者の登録番号（任意）", max_length=14, blank=True)
    description = models.CharField(max_length=200, blank=True)
    paid_from = models.CharField(max_length=20, choices=ExpensePaidFrom.choices, default=ExpensePaidFrom.BANK)
    evidence = models.ForeignKey(Evidence, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    corrected_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="corrections")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["taxpayer", "date"])]


class OwnerFundTransfer(TimestampedModel):
    """
    経営者と事業の間のお金の移動。売上・経費ではない。
    個人事業主: 投入=事業主借 / 引出=事業主貸。法人: 投入=役員借入金 / 引出=役員借入金の返済（勘定科目は対応付けで変更可）。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="owner_transfers")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    date = models.DateField()
    direction = models.CharField(max_length=10)  # in（事業へ投入）/ out（事業から引出）
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, default="bank")  # bank / cash_register
    cash_movement = models.OneToOneField("cash.CashMovement", null=True, blank=True, on_delete=models.SET_NULL, related_name="owner_transfer")
    note = models.CharField(max_length=200, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-date", "-id"]


class CardSettlement(TimestampedModel):
    """カード決済会社からの入金（売上の入金と手数料）。売掛金（カード）との照合に使う。"""

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="card_settlements")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    deposit_date = models.DateField("入金日")
    sales_amount = models.DecimalField("対象の売上額", max_digits=12, decimal_places=2)
    fee_amount = models.DecimalField("決済手数料", max_digits=12, decimal_places=2, default=Decimal("0"))
    deposit_amount = models.DecimalField("入金額", max_digits=12, decimal_places=2)
    provider_name = models.CharField(max_length=60, blank=True)
    # 対象のカード売上の期間（未入金残高の計算用。空なら期間を特定しない）
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    # 入金した銀行口座（banking.BankAccount の ID。循環参照を避けるため FK にしない）
    bank_account_ref = models.PositiveBigIntegerField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)
    is_voided = models.BooleanField(default=False)
    void_reason = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-deposit_date", "-id"]


# --- 勘定科目・仕訳行・会計年度 ---------------------------------------------------------------
class AccountCategory(models.TextChoices):
    ASSET = "asset", "資産"
    LIABILITY = "liability", "負債"
    EQUITY = "equity", "資本（元入金）"
    OWNER = "owner", "事業主貸借"
    REVENUE = "revenue", "収益"
    COGS = "cogs", "売上原価"
    EXPENSE = "expense", "経費"


class Account(TimestampedModel):
    """
    勘定科目（事業者ごと）。key は仕訳の科目キー（journals.ACCOUNTS と同じ値）で安定、name は表示名（変更可）。
    statement_line は収支内訳書・青色申告決算書の行キー（null は帳票に出ない科目）。
    """

    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="accounts")
    key = models.SlugField(max_length=40)
    code = models.CharField(max_length=8, blank=True)
    name = models.CharField(max_length=60)
    category = models.CharField(max_length=10, choices=AccountCategory.choices)
    statement_line = models.CharField(max_length=40, blank=True)
    default_tax = models.CharField(max_length=20, default="out_of_scope")
    is_cash = models.BooleanField(default=False)
    is_bank = models.BooleanField(default=False)
    is_system = models.BooleanField("標準科目（削除不可）", default=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "勘定科目"
        ordering = ["taxpayer_id", "sort_order", "code", "id"]
        constraints = [models.UniqueConstraint(fields=["taxpayer", "key"], name="uniq_account_key_per_taxpayer")]

    def __str__(self) -> str:
        return self.name

    @property
    def debit_normal(self) -> bool:
        """借方が増加になる科目か（資産・費用・売上原価・事業主貸）。"""
        return self.category in (AccountCategory.ASSET, AccountCategory.EXPENSE, AccountCategory.COGS) or self.key == "owner_out"


class FiscalYearStatus(models.TextChoices):
    OPEN = "open", "進行中"
    CLOSING = "closing", "決算作業中"
    CLOSED = "closed", "締め済み"
    FILED = "filed", "申告済み（利用者の記録）"


class FiscalYear(TimestampedModel):
    """会計年度。個人事業主は暦年。締めた年度の仕訳は変更できない。"""

    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="fiscal_years")
    year = models.PositiveIntegerField("年度（終了日の属する年）")
    start_date = models.DateField()
    end_date = models.DateField()
    filing_type = models.CharField(max_length=10, default="white")  # white / blue_10 / blue_55 / blue_65
    status = models.CharField(max_length=10, choices=FiscalYearStatus.choices, default=FiscalYearStatus.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    filed_at = models.DateTimeField(null=True, blank=True)
    reopen_count = models.PositiveSmallIntegerField(default=0)
    # 締め後に元データが変わって仕訳を作り直せなかった記録 [{"sync_key","detected_at","message"}]
    drift_warnings = models.JSONField(default=list, blank=True)
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = verbose_name_plural = "会計年度"
        ordering = ["taxpayer_id", "-year"]
        constraints = [models.UniqueConstraint(fields=["taxpayer", "year"], name="uniq_fiscal_year")]

    @property
    def is_locked(self) -> bool:
        return self.status in (FiscalYearStatus.CLOSED, FiscalYearStatus.FILED)


class FiscalYearEvent(models.Model):
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name="events")
    action = models.CharField(max_length=20)  # close / reopen / file / closing_start
    reason = models.CharField(max_length=300, blank=True)
    performed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    performed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


# --- 仕訳・freee 連携 ---------------------------------------------------------------
class AccountMapping(TimestampedModel):
    """NOXIA の仕訳の科目キー → freee の勘定科目・税区分。未設定なら journals.ACCOUNTS の既定名で表示する。"""

    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="account_mappings")
    key = models.CharField(max_length=40)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL, related_name="mappings")
    account_name = models.CharField(max_length=60)
    freee_account_item_id = models.BigIntegerField(null=True, blank=True)
    tax_code = models.IntegerField("freee の税区分コード", null=True, blank=True)
    tax_name = models.CharField(max_length=60, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["taxpayer", "key"], name="uniq_account_mapping")]


class JournalEntryKind(models.TextChoices):
    AUTO = "auto", "自動（元データから）"
    MANUAL = "manual", "手入力"
    ADJUSTMENT = "adjustment", "決算整理"
    OPENING = "opening", "期首（開始残高）"
    CLOSING = "closing", "決算振替"


class Journal(TimestampedModel):
    """
    NOXIA の記録から作った仕訳。sync_key（事業者・元データの種類・ID）で一意にし、二重に作らない。
    元データが変わったら内容（content_hash）と revision が変わり、freee 側の更新が必要になる。
    lines（JSON）は freee 連携などの直列化、帳簿の集計は JournalLine（正規化した行）から行う。
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="+")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.PROTECT, related_name="journals")
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    fiscal_year = models.ForeignKey(FiscalYear, null=True, blank=True, on_delete=models.SET_NULL, related_name="journals")
    source_type = models.CharField(max_length=40)
    source_id = models.CharField(max_length=60)
    sync_key = models.CharField(max_length=140, unique=True)
    issue_date = models.DateField()
    description = models.CharField(max_length=200)
    lines = models.JSONField(default=list)  # [{"side": "debit"/"credit", "key", "amount", "tax": "sales_10"…, "memo"}]
    warnings = models.JSONField(default=list, blank=True)
    content_hash = models.CharField(max_length=64)
    revision = models.PositiveIntegerField(default=1)
    is_void = models.BooleanField("元データが取消済み", default=False)
    entry_kind = models.CharField(max_length=12, choices=JournalEntryKind.choices, default=JournalEntryKind.AUTO)
    status = models.CharField(max_length=10, default="auto")  # auto / confirmed / locked
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    freee_csv_exported_at = models.DateTimeField("freee 取込用 CSV に出力した日時", null=True, blank=True)
    freee_csv_exported_revision = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-issue_date", "-id"]
        indexes = [models.Index(fields=["taxpayer", "issue_date"]), models.Index(fields=["taxpayer", "entry_kind"])]


class JournalLine(models.Model):
    """仕訳行（正規化）。帳簿・決算・申告の集計はここから行う。journal.lines から sync_lines() で作る。"""

    journal = models.ForeignKey(Journal, on_delete=models.CASCADE, related_name="entry_lines")
    taxpayer = models.ForeignKey(TaxPayer, on_delete=models.CASCADE, related_name="journal_lines")
    date = models.DateField()
    line_no = models.PositiveSmallIntegerField()
    side = models.CharField(max_length=6)  # debit / credit
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tax_category = models.CharField(max_length=20, default="out_of_scope")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    store = models.ForeignKey(Store, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    employee = models.ForeignKey("staff.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    # 銀行口座（banking.BankAccount の ID。循環参照を避けるため FK にしない）
    bank_account_ref = models.PositiveBigIntegerField(null=True, blank=True)
    counterparty = models.CharField(max_length=100, blank=True)
    memo = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["journal_id", "line_no"]
        constraints = [models.UniqueConstraint(fields=["journal", "line_no"], name="uniq_journal_line_no")]
        indexes = [
            models.Index(fields=["taxpayer", "account", "date"]),
            models.Index(fields=["taxpayer", "date"]),
            models.Index(fields=["taxpayer", "store", "date"]),
            models.Index(fields=["taxpayer", "bank_account_ref", "date"]),
        ]

    @property
    def signed_amount(self) -> Decimal:
        """科目の正常残高の向きで符号を付けた金額（借方正常なら借方＋、貸方正常なら貸方＋）。"""
        sign = 1 if (self.side == "debit") == self.account.debit_normal else -1
        return self.amount * sign


class FreeeConnection(TimestampedModel):
    """事業者ごとの freee 会計との接続。アクセストークンはサーバー側だけで保持し、画面には返さない。"""

    taxpayer = models.OneToOneField(TaxPayer, on_delete=models.CASCADE, related_name="freee_connection")
    mode = models.CharField(max_length=10, default="mock")  # mock（模擬）/ live
    status = models.CharField(max_length=20, default="disconnected")  # disconnected / connected / error
    freee_company_id = models.BigIntegerField(null=True, blank=True)
    freee_company_name = models.CharField(max_length=100, blank=True)
    # トークンは暗号化して保存する（apps.core.db_fields）。画面には返さない
    access_token = EncryptedTextField(blank=True)
    refresh_token = EncryptedTextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    sync_from = models.DateField("この日以降の仕訳を送る", null=True, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    connected_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    last_error = models.CharField(max_length=300, blank=True)


class FreeeSyncStatus(models.TextChoices):
    PENDING = "pending", "未送信"
    SENDING = "sending", "送信中"
    SYNCED = "synced", "連携済み"
    FAILED = "failed", "失敗（再送できます）"
    NEEDS_UPDATE = "needs_update", "変更あり（更新が必要）"
    NEEDS_DELETE = "needs_delete", "取消あり（freee 側の削除が必要）"
    DELETED = "deleted", "freee 側も削除済み"
    UNKNOWN = "unknown", "結果不明（freee 側を確認してください）"


class FreeeSync(TimestampedModel):
    """仕訳1件ごとの freee への連携状態。"""

    journal = models.OneToOneField(Journal, on_delete=models.CASCADE, related_name="freee_sync")
    status = models.CharField(max_length=20, choices=FreeeSyncStatus.choices, default=FreeeSyncStatus.PENDING)
    freee_id = models.BigIntegerField("freee の振替伝票ID", null=True, blank=True)
    synced_hash = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=500, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status"])]


class FreeeSyncLog(models.Model):
    """freee との通信履歴（送った内容の要約と結果）。"""

    sync = models.ForeignKey(FreeeSync, on_delete=models.CASCADE, related_name="logs")
    action = models.CharField(max_length=20)  # create / update / delete
    ok = models.BooleanField()
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    message = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]
