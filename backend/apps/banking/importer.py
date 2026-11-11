"""
銀行 CSV の解析: 文字コード判定・区切り文字・見出し行の自動検出・列の推測・日付と金額の正規化・行ハッシュ・残高の連続性。

- 推測はあくまで候補。画面で人が列対応を確認し、口座ごとのプロファイルとして保存して使い回す。
- 金額は Decimal。全角数字・カンマ・円・¥・△▲−・( ) をそろえる。
- 日付は YYYY/MM/DD・YYYY-MM-DD・YYYY年M月D日・YYYYMMDD・令和N年M月D日／R6.1.5／M/D（年なし。年は参照年、未来なら前年）。
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from apps.accounting.uploads import detect_encoding
from apps.core.exceptions import ApiError

MAX_ROWS = 5000
COLUMNS = ("date", "description", "withdrawal", "deposit", "balance", "counterparty", "amount_signed")
COLUMN_LABELS = {"date": "日付", "description": "摘要", "withdrawal": "出金", "deposit": "入金", "balance": "残高", "counterparty": "取引先", "amount_signed": "金額（±）"}

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "date": ("取引日", "日付", "年月日", "日時", "取扱日", "お取引日", "入出金日", "起算日", "date"),
    "withdrawal": ("お引出", "お引き出し", "引出", "出金", "お支払", "支払金額", "支出", "withdrawal", "debit"),
    "deposit": ("お預入", "お預り", "預入", "入金", "受入", "収入", "deposit", "credit"),
    "balance": ("残高", "差引残高", "balance"),
    "description": ("摘要", "内容", "取引内容", "お取引内容", "メモ", "備考", "明細", "description", "memo"),
    "counterparty": ("振込依頼人", "相手", "取引先", "お相手", "依頼人", "名義", "payee", "payer"),
    "amount_signed": ("取引金額", "金額", "amount"),
}
_ERA = {"令和": 2018, "R": 2018, "平成": 1988, "H": 1988, "昭和": 1925, "S": 1925}
_NEG = ("△", "▲", "−", "－", "-", "ー")


@dataclass
class ParsedRow:
    row_no: int
    date: date
    description: str
    counterparty: str
    amount_in: Decimal
    amount_out: Decimal
    balance: Decimal | None
    raw: list[str]
    row_hash: str = ""


@dataclass
class ParseResult:
    encoding: str
    delimiter: str
    header_row: int | None
    header: list[str]
    column_map: dict
    guessed: bool
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    preview: list[list[str]] = field(default_factory=list)
    rows_total: int = 0
    descending: bool = False

    @property
    def period(self) -> tuple[date | None, date | None]:
        if not self.rows:
            return None, None
        ds = [r.date for r in self.rows]
        return min(ds), max(ds)


def nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


def parse_amount(value) -> Decimal | None:
    """'¥12,345' '12,345円' '△1,000' '(500)' '－1,000' → Decimal。空・読めないものは None。"""
    if value is None:
        return None
    s = nfkc(str(value))
    if s in ("", "-", "−", "―", "—", "nan"):
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg = True
        s = s[1:-1]
    s = s.replace(",", "").replace("円", "").replace("¥", "").replace("￥", "").replace(" ", "")
    if s[:1] in _NEG:
        neg = True
        s = s[1:]
    if s.endswith("-"):
        neg = True
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


_DATE_PATTERNS = [
    re.compile(r"^(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})日?"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),
]
_ERA_PATTERN = re.compile(r"^(令和|平成|昭和|R|H|S)\s*(\d{1,2}|元)[./年](\d{1,2})[./月](\d{1,2})日?")
_MD_PATTERN = re.compile(r"^(\d{1,2})[/\-.月](\d{1,2})日?$")


def parse_date(value, *, reference: date | None = None, carry_year: int | None = None) -> date | None:
    """日付の正規化。年なし（M/D）は carry_year（直前の行の年）か参照年。参照日より 75 日以上先なら前年。"""
    if value is None:
        return None
    s = nfkc(str(value)).replace(" ", "")
    if not s:
        return None
    s = s.split("T")[0]
    m = _DATE_PATTERNS[0].match(s) or _DATE_PATTERNS[1].match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _ERA_PATTERN.match(s)
    if m:
        era, y, mo, d = m.groups()
        yy = 1 if y == "元" else int(y)
        try:
            return date(_ERA[era] + yy, int(mo), int(d))
        except ValueError:
            return None
    m = _MD_PATTERN.match(s)
    if m:
        ref = reference or date.today()
        year = carry_year or ref.year
        try:
            d = date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
        if d > ref + timedelta(days=75):
            d = d.replace(year=d.year - 1)
        return d
    return None


def looks_like_date(s: str) -> bool:
    return parse_date(s, reference=date(2000, 1, 1), carry_year=2000) is not None and bool(re.search(r"\d", s or ""))


def detect_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:30])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        return "\t" if sample.count("\t") > sample.count(",") else ","


def read_table(data: bytes, *, encoding: str | None = None, delimiter: str | None = None) -> tuple[list[list[str]], str, str]:
    enc = encoding if encoding and encoding != "auto" else detect_encoding(data)
    try:
        text = data.decode(enc, errors="strict")
    except (UnicodeDecodeError, LookupError):
        enc = detect_encoding(data)
        text = data.decode(enc, errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    delim = delimiter if delimiter and delimiter != "auto" else detect_delimiter(text)
    rows = [[nfkc(c) for c in r] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) > MAX_ROWS + 20:
        raise ApiError("validation_error", f"行数が多すぎます（{MAX_ROWS} 行まで。期間を分けて取り込んでください）。")
    return rows, enc, delim


def _header_score(row: list[str]) -> int:
    joined = " ".join(row).lower()
    score = 0
    for keys in _KEYWORDS.values():
        if any(k.lower() in joined for k in keys):
            score += 1
    if any(looks_like_date(c) for c in row):
        score -= 3
    if sum(1 for c in row if parse_amount(c) is not None and re.fullmatch(r"[\d,.\-−△▲¥￥円()]+", c or "x")) >= 2:
        score -= 2
    return score


def detect_header_row(rows: list[list[str]]) -> int | None:
    """先頭 15 行から、日付・金額の見出し語を含みデータ行らしくない行を見出しとみなす。無ければ None。"""
    best, best_score = None, 1
    for i, r in enumerate(rows[:15]):
        s = _header_score(r)
        if s > best_score:
            best, best_score = i, s
    return best


def header_signature(header: list[str]) -> str:
    return hashlib.sha256("|".join(nfkc(h) for h in header).encode()).hexdigest()


def guess_columns(header: list[str], data_rows: list[list[str]]) -> dict:
    """見出し語と中身の統計から列を推測する。見つからないものは None。"""
    cmap: dict[str, int | None] = {c: None for c in COLUMNS}
    used: set[int] = set()
    lower = [nfkc(h).lower() for h in header]
    # 分割列（出金・入金）を先に決め、「金額」に横取りされないようにする
    for col in ("date", "withdrawal", "deposit", "balance", "description", "counterparty", "amount_signed"):
        for i, h in enumerate(lower):
            if i in used or not h:
                continue
            if any(k.lower() in h for k in _KEYWORDS[col]):
                if col == "amount_signed" and (cmap["withdrawal"] is not None or cmap["deposit"] is not None):
                    break
                if col in ("withdrawal", "deposit") and "残高" in h:
                    continue
                cmap[col] = i
                used.add(i)
                break
    ncols = max((len(r) for r in data_rows[:50]), default=len(header))
    if cmap["date"] is None:
        for i in range(ncols):
            vals = [r[i] for r in data_rows[:50] if i < len(r) and r[i]]
            if vals and sum(1 for v in vals if looks_like_date(v)) >= len(vals) * 0.8 and i not in used:
                cmap["date"] = i
                used.add(i)
                break
    if cmap["withdrawal"] is None and cmap["deposit"] is None and cmap["amount_signed"] is None:
        numeric = []
        for i in range(ncols):
            if i in used:
                continue
            vals = [r[i] for r in data_rows[:50] if i < len(r) and r[i]]
            nums = [parse_amount(v) for v in vals]
            if vals and sum(1 for n in nums if n is not None) >= len(vals) * 0.8:
                numeric.append((i, sum(1 for n in nums if n is not None and n < 0), len(vals)))
        if len(numeric) >= 3:
            cmap["withdrawal"], cmap["deposit"], cmap["balance"] = numeric[0][0], numeric[1][0], numeric[2][0]
        elif len(numeric) == 2:
            cmap["withdrawal"], cmap["deposit"] = numeric[0][0], numeric[1][0]
        elif len(numeric) == 1:
            cmap["amount_signed"] = numeric[0][0]
    if cmap["description"] is None:
        for i in range(ncols):
            if i in used or i in (cmap["date"], cmap["withdrawal"], cmap["deposit"], cmap["balance"], cmap["amount_signed"]):
                continue
            vals = [r[i] for r in data_rows[:50] if i < len(r) and r[i]]
            if vals and sum(1 for v in vals if parse_amount(v) is None and not looks_like_date(v)) >= len(vals) * 0.8:
                cmap["description"] = i
                break
    return cmap


def _cell(row: list[str], idx) -> str:
    if idx is None:
        return ""
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return ""
    return row[i] if 0 <= i < len(row) else ""


def extract_counterparty(description: str) -> str:
    """摘要から相手名らしい部分を切り出す（振込 カ）ノクシア → カ）ノクシア）。"""
    s = nfkc(description)
    s = re.sub(r"^(振込|フリコミ|ﾌﾘｺﾐ|振替|口座振替|カード|デビット|ATM|入金|出金)\s*", "", s, flags=re.I)
    return s[:100]


def row_hash(bank_account_id: int, r: ParsedRow, seq: int) -> str:
    key = f"{bank_account_id}|{r.date.isoformat()}|{r.description}|{r.amount_in}|{r.amount_out}|{'' if r.balance is None else r.balance}|{seq}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def parse_statement(data: bytes, *, bank_account_id: int, column_map: dict | None = None, encoding: str | None = None, delimiter: str | None = None,
                    header_rows: int | None = None, footer_rows: int = 0, reference: date | None = None, amount_rules: dict | None = None) -> ParseResult:
    """CSV を解析して明細行にする。column_map が無ければ推測する（guessed=True）。"""
    rows, enc, delim = read_table(data, encoding=encoding, delimiter=delimiter)
    if not rows:
        raise ApiError("validation_error", "CSV に行がありません。")
    if header_rows:
        hdr_idx = header_rows - 1
    else:
        hdr_idx = detect_header_row(rows)
    header = rows[hdr_idx] if hdr_idx is not None and hdr_idx < len(rows) else []
    body = rows[(hdr_idx + 1 if hdr_idx is not None else 0):]
    if footer_rows:
        body = body[: max(0, len(body) - footer_rows)]
    guessed = False
    cmap = dict(column_map or {})
    if not cmap or cmap.get("date") is None or (cmap.get("withdrawal") is None and cmap.get("deposit") is None and cmap.get("amount_signed") is None):
        cmap = guess_columns(header, body)
        guessed = True
    res = ParseResult(encoding=enc, delimiter=delim, header_row=hdr_idx, header=header, column_map=cmap, guessed=guessed,
                      preview=[r[:12] for r in rows[:20]], rows_total=len(body))
    if cmap.get("date") is None:
        res.errors.append({"row": None, "message": "日付の列が見つかりません。列の対応を指定してください。"})
        return res
    if cmap.get("withdrawal") is None and cmap.get("deposit") is None and cmap.get("amount_signed") is None:
        res.errors.append({"row": None, "message": "入金・出金（または金額）の列が見つかりません。列の対応を指定してください。"})
        return res
    neg_is_withdrawal = (amount_rules or {}).get("negative_is_withdrawal", True)
    parsed: list[ParsedRow] = []
    carry: int | None = None
    ref = reference or date.today()
    for n, r in enumerate(body, 1):
        if len(parsed) >= MAX_ROWS:
            res.errors.append({"row": n, "message": f"{MAX_ROWS} 行を超えた分は取り込みません。"})
            break
        if not any(c for c in r):
            continue
        d = parse_date(_cell(r, cmap.get("date")), reference=ref, carry_year=carry)
        if d is None:
            if all(parse_amount(_cell(r, cmap.get(k))) is None for k in ("withdrawal", "deposit", "amount_signed", "balance")):
                continue  # 合計行などの飾り
            res.errors.append({"row": n, "message": f"日付を読めません: {_cell(r, cmap.get('date'))!r}"})
            continue
        carry = d.year
        amt_in = amt_out = Decimal(0)
        if cmap.get("amount_signed") is not None and cmap.get("withdrawal") is None and cmap.get("deposit") is None:
            a = parse_amount(_cell(r, cmap.get("amount_signed")))
            if a is None:
                res.errors.append({"row": n, "message": "金額を読めません。"})
                continue
            if (a < 0) == bool(neg_is_withdrawal):
                amt_out = abs(a)
            else:
                amt_in = abs(a)
        else:
            w = parse_amount(_cell(r, cmap.get("withdrawal"))) if cmap.get("withdrawal") is not None else None
            p = parse_amount(_cell(r, cmap.get("deposit"))) if cmap.get("deposit") is not None else None
            if w is None and p is None:
                res.errors.append({"row": n, "message": "入金・出金が空です。"})
                continue
            amt_out = abs(w) if w else Decimal(0)
            amt_in = abs(p) if p else Decimal(0)
        if amt_in and amt_out:
            res.errors.append({"row": n, "message": "入金と出金の両方に金額があります。"})
            continue
        if not amt_in and not amt_out:
            continue
        bal = parse_amount(_cell(r, cmap.get("balance"))) if cmap.get("balance") is not None else None
        desc = _cell(r, cmap.get("description"))
        cp = _cell(r, cmap.get("counterparty")) or extract_counterparty(desc)
        parsed.append(ParsedRow(row_no=n, date=d, description=desc[:200], counterparty=cp[:100], amount_in=amt_in, amount_out=amt_out, balance=bal, raw=r[:30]))
    # 新しい順の明細は古い順に並べ直す（残高の連続性の確認のため）
    if len(parsed) >= 2 and parsed[0].date > parsed[-1].date:
        res.descending = True
        parsed.reverse()
    seen: dict[str, int] = {}
    for p in parsed:
        base = f"{p.date}|{p.description}|{p.amount_in}|{p.amount_out}|{p.balance}"
        seq = seen.get(base, 0)
        seen[base] = seq + 1
        p.row_hash = row_hash(bank_account_id, p, seq)
    res.rows = parsed
    return res


def balance_continuity(rows: list[ParsedRow], previous_closing: Decimal | None) -> tuple[str, str, Decimal | None, Decimal | None]:
    """残高列があるとき、最初の行の「取引前残高」が前回の最終残高と一致するか。戻り値 (ok/mismatch/unknown, 説明, opening, closing)。"""
    with_bal = [r for r in rows if r.balance is not None]
    if not with_bal:
        return "unknown", "残高の列が無いため連続性は確認できません。", None, None
    first = with_bal[0]
    opening = first.balance - first.amount_in + first.amount_out
    closing = with_bal[-1].balance
    # ファイル内の連続性も確かめる
    prev = None
    for r in with_bal:
        if prev is not None and r.balance != prev + r.amount_in - r.amount_out:
            return "mismatch", f"{r.date} の残高がその前の行とつながりません（取込漏れ・重複の可能性）。", opening, closing
        prev = r.balance
    if previous_closing is None:
        return "ok", "前回の残高が無いため、ファイル内の連続性だけ確認しました。", opening, closing
    if opening != previous_closing:
        return "mismatch", f"前回の最終残高 {previous_closing:,.0f} 円と今回の開始残高 {opening:,.0f} 円が一致しません。間の期間が抜けているか、重複しています。", opening, closing
    return "ok", "前回の最終残高とつながっています。", opening, closing
