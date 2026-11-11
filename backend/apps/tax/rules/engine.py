"""
所得税の計算エンジン（純粋関数・Decimal）。率と金額は config（TaxYearConfiguration の JSON）から読む。
段取りは tax_calculation_rules.md §1: 所得 → 所得控除 → 課税所得（1,000円未満切捨て）→ 速算表 → 税額控除 → 復興特別所得税（別行・1円未満切捨て）
→ 源泉徴収税額・予定納税 → 申告納税額（100円未満切捨て）。各段の値と根拠キーを trace に残す。
ミセミル payroll/services/year_end_calc.py の表の持ち方・端数処理を参考にし、確定申告向けに拡張している。
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

ZERO = Decimal(0)


def D(v) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None or v == "":
        return ZERO
    return Decimal(str(v))


def floor_to(v: Decimal, unit: int) -> Decimal:
    v = D(v)
    if unit <= 1:
        return v.quantize(Decimal(1), rounding=ROUND_FLOOR) if v >= 0 else -((-v).quantize(Decimal(1), rounding=ROUND_FLOOR))
    u = Decimal(unit)
    return (v / u).quantize(Decimal(1), rounding=ROUND_FLOOR) * u if v >= 0 else -(((-v) / u).quantize(Decimal(1), rounding=ROUND_FLOOR) * u)


def ceil_yen(v: Decimal) -> Decimal:
    return D(v).quantize(Decimal(1), rounding=ROUND_CEILING)


def floor_yen(v: Decimal) -> Decimal:
    return D(v).quantize(Decimal(1), rounding=ROUND_FLOOR)


def _lookup(table: list, key: Decimal):
    """[[上限, 値], ...]（上限 null は無限大）から key に当たる値。"""
    for upto, value in table:
        if upto is None or key <= D(upto):
            return value
    return table[-1][1]


# --- 所得 -------------------------------------------------------------------------------------
def salary_income(revenue, cfg: dict) -> tuple[Decimal, dict]:
    """給与所得（収入 − 給与所得控除）。別表第五の方式（4,000円刻み）を rows で表す。"""
    rev = D(revenue)
    rows = cfg["deductions"]["salary_income"]["rows"]
    if rev <= 0:
        return ZERO, {"revenue": "0", "deduction": "0", "rule": "zero"}
    for row in rows:
        upto = row.get("upto")
        if upto is None or rev < D(upto):
            t = row["type"]
            if t == "zero":
                inc = ZERO
            elif t == "minus":
                inc = rev - D(row["value"])
            elif t == "fixed":
                inc = D(row["value"])
            elif t == "step":
                stepped = floor_to(rev / 4, 1000) * 4
                inc = floor_yen(stepped * D(row["rate"]) - D(row["minus"]))
            else:  # linear
                inc = floor_yen(rev * D(row["rate"]) - D(row["minus"]))
            inc = max(ZERO, inc)
            return inc, {"revenue": str(rev), "deduction": str(rev - inc), "rule": t, "row": row}
    return rev, {"revenue": str(rev), "deduction": "0", "rule": "none"}


# --- 所得控除 ---------------------------------------------------------------------------------
def basic_deduction(total_income, cfg: dict) -> Decimal:
    return D(_lookup(cfg["deductions"]["basic"]["table"], D(total_income)))


def _tier(total_income, tiers: list) -> int | None:
    ti = D(total_income)
    for i, t in enumerate(tiers):
        if ti <= D(t):
            return i
    return None


def spouse_deduction(total_income, spouse: dict | None, cfg: dict) -> tuple[Decimal, str]:
    """配偶者控除／配偶者特別控除。spouse = {"has": bool, "income": 合計所得, "age70": bool}。戻り値 (額, 種類)。"""
    if not spouse or not spouse.get("has"):
        return ZERO, "none"
    sp = cfg["deductions"]["spouse"]
    tier = _tier(total_income, sp["tiers"])
    if tier is None:
        return ZERO, "over_income"
    s_inc = D(spouse.get("income") or 0)
    if s_inc <= D(sp["income_limit"]):
        return D((sp["elderly"] if spouse.get("age70") else sp["general"])[tier]), "spouse"
    for upto, amounts in cfg["deductions"]["spouse_special"]["rows"]:
        if s_inc <= D(upto):
            return D(amounts[tier]), "spouse_special"
    return ZERO, "spouse_over"


def dependents_deduction(dependents: dict | None, cfg: dict) -> tuple[Decimal, list]:
    """扶養控除。dependents = {"general": n, "specific": n, "elderly": n, "elderly_cohabiting": n, "specific_relatives": [{"income": x}]}"""
    if not dependents:
        return ZERO, []
    dp = cfg["deductions"]["dependents"]
    total = ZERO
    detail = []
    for k in ("general", "specific", "elderly", "elderly_cohabiting"):
        n = int(dependents.get(k) or 0)
        if n:
            amt = D(dp[k]) * n
            total += amt
            detail.append({"kind": k, "count": n, "amount": str(amt)})
    srs = cfg["deductions"].get("specific_relative_special")
    for rel in dependents.get("specific_relatives") or []:
        inc = D(rel.get("income") or 0)
        if srs and inc > D(srs["income_floor"]):
            amt = ZERO
            for upto, value in srs["rows"]:
                if inc <= D(upto):
                    amt = D(value)
                    break
            total += amt
            detail.append({"kind": "specific_relative_special", "income": str(inc), "amount": str(amt)})
    return total, detail


def personal_flags_deduction(total_income, flags: dict | None, cfg: dict) -> tuple[Decimal, list]:
    """障害者・寡婦・ひとり親・勤労学生。flags = {"disability_general": n, "disability_special": n, "disability_special_cohabiting": n, "widow": bool, "single_parent": bool, "working_student": bool}"""
    if not flags:
        return ZERO, []
    d = cfg["deductions"]
    total = ZERO
    detail = []
    dis = d["disability"]
    for k, key in (("disability_general", "general"), ("disability_special", "special"), ("disability_special_cohabiting", "special_cohabiting")):
        n = int(flags.get(k) or 0)
        if n:
            amt = D(dis[key]) * n
            total += amt
            detail.append({"kind": k, "count": n, "amount": str(amt), "verified": dis.get("verified", False)})
    ti = D(total_income)
    if flags.get("single_parent") and ti <= D(d["single_parent"]["income_limit"]):
        total += D(d["single_parent"]["amount"])
        detail.append({"kind": "single_parent", "amount": str(d["single_parent"]["amount"]), "verified": d["single_parent"].get("verified", False)})
    elif flags.get("widow") and ti <= D(d["widow"]["income_limit"]):
        total += D(d["widow"]["amount"])
        detail.append({"kind": "widow", "amount": str(d["widow"]["amount"]), "verified": d["widow"].get("verified", False)})
    if flags.get("working_student") and ti <= D(d["working_student"]["income_limit"]):
        total += D(d["working_student"]["amount"])
        detail.append({"kind": "working_student", "amount": str(d["working_student"]["amount"])})
    return total, detail


def _stepped(amount: Decimal, table: list, round_mode: str) -> Decimal:
    for upto, rate, add in table:
        if upto is None:
            return D(add)
        if amount <= D(upto):
            v = amount * D(rate) + D(add)
            return ceil_yen(v) if round_mode == "ceil" else floor_yen(v)
    return ZERO


def life_insurance_deduction(li: dict | None, cfg: dict) -> tuple[Decimal, dict]:
    """生命保険料控除。li = {"new_general", "old_general", "care", "new_pension", "old_pension", "has_under23": bool}（支払額）。"""
    if not li:
        return ZERO, {}
    c = cfg["deductions"]["life_insurance"]
    rnd = c.get("round", "ceil")
    new_tbl, old_tbl = c["new"], c["old"]
    detail = {}

    def category(new_amt, old_amt, new_table, cap):
        n = _stepped(D(new_amt), new_table, rnd) if D(new_amt) > 0 else ZERO
        o = _stepped(D(old_amt), old_tbl, rnd) if D(old_amt) > 0 else ZERO
        both = min(n + o, D(cap))
        return max(n, o, both)

    general_table = c["new_general_under23"] if li.get("has_under23") else new_tbl
    general_cap = c["per_category_cap_under23"] if li.get("has_under23") else c["per_category_cap"]
    general = category(li.get("new_general") or 0, li.get("old_general") or 0, general_table, general_cap)
    if li.get("has_under23"):
        general = min(general, D(c["per_category_cap_under23"]))
    care = _stepped(D(li.get("care") or 0), new_tbl, rnd) if D(li.get("care") or 0) > 0 else ZERO
    pension = category(li.get("new_pension") or 0, li.get("old_pension") or 0, new_tbl, c["per_category_cap"])
    total = min(general + care + pension, D(c["total_cap"]))
    detail = {"general": str(general), "care": str(care), "pension": str(pension), "total": str(total)}
    return total, detail


def earthquake_deduction(eq: dict | None, cfg: dict) -> Decimal:
    """地震保険料控除。eq = {"earthquake": 支払額, "old_long_term": 旧長期損害保険料}"""
    if not eq:
        return ZERO
    c = cfg["deductions"]["earthquake"]
    e = min(D(eq.get("earthquake") or 0), D(c["cap"]))
    o = _stepped(D(eq.get("old_long_term") or 0), c["old_long_term"], "floor") if D(eq.get("old_long_term") or 0) > 0 else ZERO
    return min(e + o, D(c["cap"]))


def medical_deduction(med: dict | None, total_income_etc, cfg: dict) -> Decimal:
    """医療費控除 = (支払 − 補填) − min(10万, 総所得金額等×5%)。上限 200 万。"""
    if not med:
        return ZERO
    c = cfg["deductions"]["medical"]
    paid = D(med.get("paid") or 0) - D(med.get("reimbursed") or 0)
    threshold = min(D(c["threshold"]), floor_yen(D(total_income_etc) * D(c["threshold_rate"])))
    return max(ZERO, min(paid - threshold, D(c["cap"])))


def donation_deduction(amount, total_income_etc, cfg: dict) -> Decimal:
    c = cfg["deductions"]["donation"]
    a = D(amount or 0)
    if a <= 0:
        return ZERO
    capped = min(a, floor_yen(D(total_income_etc) * D(c["rate_cap"])))
    return max(ZERO, capped - D(c["floor"]))


def blue_deduction(business_income, filing_type: str, cfg: dict) -> Decimal:
    """青色申告特別控除（10万円）。所得が上限。赤字なら 0。"""
    if filing_type != "blue_10":
        return ZERO
    return max(ZERO, min(D(cfg["deductions"]["blue_special"]["amount"]), D(business_income)))


# --- 税額 -------------------------------------------------------------------------------------
def income_tax(taxable, cfg: dict) -> tuple[Decimal, dict]:
    t = D(taxable)
    if t <= 0:
        return ZERO, {"rate": "0", "minus": "0"}
    for upto, rate, minus in cfg["income_tax"]["brackets"]:
        if upto is None or t <= D(upto):
            return floor_yen(t * D(rate) - D(minus)), {"rate": rate, "minus": str(minus)}
    return ZERO, {}


def surtax(base_tax, cfg: dict) -> Decimal:
    rate = D(cfg["surtaxes"].get("reconstruction_rate") or 0)
    return floor_yen(D(base_tax) * rate)


def defense_surtax(base_tax, cfg: dict) -> Decimal:
    rate = cfg["surtaxes"].get("defense_rate")
    if rate in (None, ""):
        return ZERO
    return floor_yen(D(base_tax) * D(rate))
