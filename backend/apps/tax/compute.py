"""
申告書データの計算（所得税及び復興特別所得税）。段取りは tax_calculation_rules.md §1。
未対応の所得・控除・ケースは unsupported_items に入れ、申告書を完成扱いにしない。計算過程は computed["trace"] に残す。
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.accounting.models import JournalLine
from apps.core.exceptions import ApiError
from apps.core.money import D, ZERO

from . import statements
from .models import TaxReturn, TaxReturnStatus, TaxReturnVersion, record_tax_audit
from .rules import engine_for

SUPPORTED_OTHER_INCOME = {"miscellaneous": "雑所得（業務・その他）"}

UNSUPPORTED_LABELS = {
    "income.real_estate": "不動産所得は未対応です。作成コーナーで直接入力するか税理士にご相談ください。",
    "income.capital_gain": "譲渡所得は未対応です。",
    "income.forest": "山林所得は未対応です。",
    "income.retirement": "退職所得は未対応です。",
    "income.separate": "分離課税の所得（株式・FX・土地建物など）は未対応です。",
    "income.other_kind": "対応していない所得の種類が入力されています。",
    "income.salary_over_20m": "給与収入 2,000 万円超は未対応です。",
    "deduction.housing_loan": "住宅借入金等特別控除（税額控除）は未対応です。",
    "deduction.casualty": "雑損控除は未対応です。",
    "blue.deduction_55_65": "青色申告特別控除 55 万円・65 万円は未対応です（10 万円のみ）。",
    "consumption_tax.taxable": "消費税の課税事業者です。消費税の申告は未対応のため税理士にご相談ください。",
    "loss.white_carryforward": "白色申告では純損失の繰越控除は使えません。",
    "year.partial_death_or_departure": "準確定申告（年の途中の死亡・出国）は未対応です。",
    "surtax.high_income": "基準所得金額 3.3 億円超の追加課税は未対応です。",
    "depreciation.declining_balance": "定率法の減価償却は未対応です（届出のある方は税理士へ）。",
}


def _journal_snapshot_hash(tr: TaxReturn) -> str:
    fy = tr.fiscal_year
    h = hashlib.sha256()
    qs = JournalLine.objects.filter(taxpayer=tr.taxpayer, journal__is_void=False, date__gte=fy.start_date, date__lte=fy.end_date).order_by("id")
    for row in qs.values_list("id", "date", "side", "account__key", "amount"):
        h.update(("|".join(str(x) for x in row) + "\n").encode())
    return h.hexdigest()


def detect_unsupported(tr: TaxReturn, inputs: dict, cfg: dict) -> tuple[list[dict], list[str]]:
    unsupported: list[dict] = []
    warnings: list[str] = []
    prof = getattr(tr.taxpayer, "individual_profile", None)

    def add(code):
        unsupported.append({"code": code, "message": UNSUPPORTED_LABELS.get(code, code)})

    for oi in inputs.get("other_incomes") or []:
        kind = oi.get("kind")
        if kind in ("real_estate", "capital_gain", "forest", "retirement", "separate"):
            add(f"income.{kind}")
        elif kind not in SUPPORTED_OTHER_INCOME:
            add("income.other_kind")
    if D((inputs.get("salary") or {}).get("revenue") or 0) > D(cfg["deductions"].get("salary_income_max_supported", 20000000)):
        add("income.salary_over_20m")
    if D(inputs.get("housing_loan_credit") or 0) > 0:
        add("deduction.housing_loan")
    if D(inputs.get("casualty_loss") or 0) > 0:
        add("deduction.casualty")
    if tr.filing_type in ("blue_55", "blue_65"):
        add("blue.deduction_55_65")
    if prof is not None and prof.consumption_tax_status != "exempt":
        add("consumption_tax.taxable")
    if tr.filing_type == "white" and D(inputs.get("loss_carryforward") or 0) > 0:
        add("loss.white_carryforward")
    if inputs.get("is_partial_year_return"):
        add("year.partial_death_or_departure")
    if tr.filing_type.startswith("blue") and (prof is None or not prof.blue_for_year(tr.fiscal_year.year)):
        warnings.append("青色申告の承認が登録されていません。承認を受けていない場合は白色申告になります（青色申告特別控除は適用していません）。")
    if prof is None:
        warnings.append("申告情報（屋号・開業日・青色承認・消費税の区分）が未登録です。会計設定で登録してください。")
    if tr.fiscal_year.status not in ("closed", "filed"):
        warnings.append("年度がまだ締められていません。数字が変わる可能性があります。年間決算で締めてから最終の計算をしてください。")
    return unsupported, warnings


@transaction.atomic
def compute_return(tr: TaxReturn, user, *, reason: str = "計算") -> TaxReturnVersion:
    """集計と税額計算を実行し、版を作る。"""
    cfg = tr.config.as_dict()
    eng = engine_for(tr.config.year)
    inputs = tr.inputs
    prof = getattr(tr.taxpayer, "individual_profile", None)
    unsupported, warnings = detect_unsupported(tr, inputs, cfg)
    trace: list[dict] = []

    def step(key, label, value, basis="", **extra):
        trace.append({"key": key, "label": label, "value": str(value), "basis": basis, **extra})
        return value

    # 1. 事業所得
    family = D(inputs.get("family_employee") or 0)
    summary = statements.business_summary(tr.taxpayer, tr.fiscal_year, family_employee=family, filing_type=tr.filing_type)
    if summary["has_suspense"]:
        warnings.append(f"仮勘定（仮払金・仮受金）が {summary['suspense_total']} 円残っています。損益が確定していません。")
    before_blue = D(summary["values"]["income_before_blue"])
    blue_ok = tr.filing_type == "blue_10" and prof is not None and prof.blue_for_year(tr.fiscal_year.year)
    blue = eng.blue_deduction(before_blue, "blue_10" if blue_ok else "white", cfg)
    business_income = before_blue - blue
    step("business.revenue", "売上（収入）金額", summary["values"]["revenue_total"], "収支内訳書④／決算書①")
    step("business.cogs", "売上原価", summary["values"]["cogs"], "期首棚卸＋仕入−期末棚卸")
    step("business.expenses", "必要経費", summary["values"]["expenses_total"])
    step("business.family_employee", "専従者控除／専従者給与", family, "入力")
    step("business.blue_deduction", "青色申告特別控除", blue, "deductions.blue_special（所得が上限）" if blue_ok else "適用なし")
    step("business.income", "事業所得", business_income)
    rows = statements.statement_rows(summary, cfg, tr.filing_type if blue_ok or tr.filing_type == "white" else "white", blue, business_income)

    # 2. 他の所得
    sal_in = inputs.get("salary") or {}
    salary_inc, sal_basis = eng.salary_income(sal_in.get("revenue") or 0, cfg)
    step("salary.income", "給与所得", salary_inc, "deductions.salary_income", detail=sal_basis)
    other_total = sum((D(o.get("amount") or 0) for o in inputs.get("other_incomes") or [] if o.get("kind") in SUPPORTED_OTHER_INCOME), ZERO)
    step("other.income", "その他の所得（雑所得）", other_total, "入力")

    # 3. 合計所得金額（損益通算: 事業の赤字は他の所得と通算）
    total_income = business_income + salary_inc + other_total
    step("total_income", "合計所得金額（損益通算後）", total_income)
    carry = ZERO
    if tr.filing_type.startswith("blue") and D(inputs.get("loss_carryforward") or 0) > 0 and total_income > 0:
        carry = min(D(inputs["loss_carryforward"]), total_income)
    sogo = total_income - carry
    step("loss_carryforward", "純損失の繰越控除", carry, "deductions.loss_carryforward（青色）")
    step("gross_income", "総所得金額", sogo)

    # 4. 所得控除
    ded: list[dict] = []

    def dd(key, label, amount, basis="", **extra):
        ded.append({"key": key, "label": label, "amount": str(D(amount)), "basis": basis, **extra})

    dd("social_insurance", "社会保険料控除", inputs.get("social_insurance") or 0, "入力（全額）")
    dd("small_business", "小規模企業共済等掛金控除", inputs.get("small_business_mutual") or 0, "入力（全額）")
    li_amt, li_detail = eng.life_insurance_deduction(inputs.get("life_insurance"), cfg)
    dd("life_insurance", "生命保険料控除", li_amt, "deductions.life_insurance", detail=li_detail)
    dd("earthquake", "地震保険料控除", eng.earthquake_deduction(inputs.get("earthquake_insurance"), cfg), "deductions.earthquake")
    dd("medical", "医療費控除", eng.medical_deduction(inputs.get("medical"), sogo, cfg), "deductions.medical")
    dd("donation", "寄附金控除", eng.donation_deduction(inputs.get("donation"), sogo, cfg), "deductions.donation")
    sp_amt, sp_kind = eng.spouse_deduction(total_income, inputs.get("spouse"), cfg)
    dd("spouse", "配偶者控除／配偶者特別控除", sp_amt, f"deductions.spouse ({sp_kind})", kind=sp_kind)
    dep_amt, dep_detail = eng.dependents_deduction(inputs.get("dependents"), cfg)
    dd("dependents", "扶養控除・特定親族特別控除", dep_amt, "deductions.dependents", detail=dep_detail)
    fl_amt, fl_detail = eng.personal_flags_deduction(total_income, inputs.get("personal"), cfg)
    dd("personal", "障害者・寡婦・ひとり親・勤労学生控除", fl_amt, "deductions.disability 等", detail=fl_detail)
    basic = eng.basic_deduction(total_income, cfg)
    dd("basic", "基礎控除", basic, "deductions.basic（合計所得金額で判定）")
    deductions_total = sum((D(d["amount"]) for d in ded), ZERO)
    step("deductions_total", "所得控除の合計", deductions_total)

    # 5. 課税所得・税額
    taxable = eng.floor_to(max(ZERO, sogo - deductions_total), int(cfg["income_tax"]["taxable_round_unit"]))
    step("taxable_income", "課税される所得金額", taxable, "1,000円未満切捨て（通則法118）")
    tax, bracket = eng.income_tax(taxable, cfg)
    step("income_tax", "算出税額", tax, f"速算表 率 {bracket.get('rate')} 控除 {bracket.get('minus')}")
    credits = ZERO
    step("tax_credits", "税額控除", credits, "対応なし")
    base_tax = max(ZERO, tax - credits)
    step("base_tax", "基準所得税額", base_tax)
    recon = eng.surtax(base_tax, cfg)
    step("reconstruction_tax", "復興特別所得税", recon, f"×{cfg['surtaxes'].get('reconstruction_rate')}（1円未満切捨て）")
    defense = eng.defense_surtax(base_tax, cfg)
    if defense:
        step("defense_tax", "防衛特別所得税", defense)
    total_tax = base_tax + recon + defense
    step("total_tax", "所得税及び復興特別所得税の額", total_tax)
    withheld = D(inputs.get("withheld_tax") or 0) + D(sal_in.get("withheld") or 0)
    estimated = D(inputs.get("estimated_tax_paid") or 0)
    step("withheld", "源泉徴収税額", withheld, "入力")
    step("estimated_paid", "予定納税額", estimated, "入力")
    diff = total_tax - withheld - estimated
    due = eng.floor_to(diff, int(cfg["income_tax"]["final_round_unit"])) if diff > 0 else ZERO
    refund = -diff if diff < 0 else ZERO
    step("tax_due", "納付する税額", due, "100円未満切捨て（通則法119）")
    step("refund", "還付される税額", refund)
    if taxable > D(cfg["income_tax"].get("high_income_surtax_threshold", 330000000)):
        unsupported.append({"code": "surtax.high_income", "message": UNSUPPORTED_LABELS["surtax.high_income"]})

    computed = {
        "computed_at": timezone.now().isoformat(), "config_year": tr.config.year, "config_hash": tr.config.content_hash,
        "filing_type_applied": "blue_10" if blue_ok else "white",
        "statement_rows": rows, "deductions": ded, "trace": trace,
        "values": {t["key"]: t["value"] for t in trace},
        "notice": "NOXIA の集計と入力に基づく計算です。申告は国税庁の確定申告書等作成コーナーまたは e-Tax でご本人が行ってください。未対応の項目がある場合は完成になりません。",
    }
    tr.business_summary = summary
    tr.computed = computed
    tr.unsupported_items = unsupported
    tr.warnings = warnings
    if tr.status in (TaxReturnStatus.COMPLETE, TaxReturnStatus.EXPORTED):
        tr.status = TaxReturnStatus.IN_REVIEW if unsupported else TaxReturnStatus.DRAFT
    elif unsupported:
        tr.status = TaxReturnStatus.IN_REVIEW
    elif tr.status == TaxReturnStatus.IN_REVIEW:
        tr.status = TaxReturnStatus.DRAFT
    tr.updated_by = user
    tr.version += 1
    tr.save()
    last = tr.versions.order_by("-version_no").first()
    ver = TaxReturnVersion.objects.create(
        tax_return=tr, version_no=(last.version_no + 1) if last else 1, created_by=user, reason=reason[:100], inputs_enc=tr.inputs_enc,
        business_summary=summary, computed=computed, unsupported_items=unsupported, warnings=warnings, config_hash=tr.config.content_hash,
        journal_snapshot_hash=_journal_snapshot_hash(tr),
    )
    record_tax_audit(tr.taxpayer, "compute", user=user, tax_return=tr,
                     detail={"version_no": ver.version_no, "tax_due": str(due), "refund": str(refund), "unsupported": len(unsupported)})
    return ver


def complete_return(tr: TaxReturn, user) -> TaxReturn:
    if not tr.computed:
        raise ApiError("not_computed", "先に「計算」を実行してください。", status=409)
    if tr.unsupported_items:
        raise ApiError("unsupported", "未対応の項目があるため完成にできません。", status=409, details=tr.unsupported_items)
    tr.status = TaxReturnStatus.COMPLETE
    tr.completed_at = timezone.now()
    tr.updated_by = user
    tr.save(update_fields=["status", "completed_at", "updated_by", "updated_at"])
    record_tax_audit(tr.taxpayer, "complete", user=user, tax_return=tr)
    return tr


def reopen_return(tr: TaxReturn, user, reason: str) -> TaxReturn:
    if tr.status == TaxReturnStatus.LOCKED:
        raise ApiError("locked", "ロック済みです。", status=409)
    tr.status = TaxReturnStatus.DRAFT
    tr.completed_at = None
    tr.updated_by = user
    tr.save(update_fields=["status", "completed_at", "updated_by", "updated_at"])
    record_tax_audit(tr.taxpayer, "reopen", user=user, tax_return=tr, detail={"reason": reason[:200]})
    return tr


def mark_filed(tr: TaxReturn, user, *, note: str = "", version_id: int | None = None) -> TaxReturn:
    if tr.status not in (TaxReturnStatus.COMPLETE, TaxReturnStatus.EXPORTED):
        raise ApiError("not_complete", "完成した申告書だけ「申告済み」にできます。", status=409)
    ver = tr.versions.filter(id=version_id).first() if version_id else tr.versions.first()
    if ver is None:
        raise ApiError("not_computed", "版がありません。", status=409)
    tr.versions.update(is_final=False)
    ver.is_final = True
    ver.save(update_fields=["is_final"])
    tr.status = TaxReturnStatus.FILED
    tr.filed_at = timezone.now()
    tr.filed_note = note[:200]
    tr.updated_by = user
    tr.save(update_fields=["status", "filed_at", "filed_note", "updated_by", "updated_at"])
    record_tax_audit(tr.taxpayer, "mark_filed", user=user, tax_return=tr, detail={"version_no": ver.version_no})
    return tr
