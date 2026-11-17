"""
伝票のレイアウト（印字と画面のプレビューで同じものを使う）と、エプソンの ePOS-Print XML への変換。

行の種類:
  text  … 1 行の文字（align: left / center / right、size: 1 = 普通 / 2 = 縦横 2 倍、bold）
  pair  … 左に項目名、右に金額（収まらなければ 2 行に分ける）
  rule  … 区切り線
  feed  … 空行
  stamp … 収入印紙の枠（領収書）
"""
from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field
from xml.sax.saxutils import escape

EPOS_NS = "http://www.epson-pos.com/schemas/2011/03/epos-print"


@dataclass
class Row:
    kind: str
    left: str = ""
    right: str = ""
    align: str = "left"
    size: int = 1
    bold: bool = False


@dataclass
class Slip:
    kind: str
    title: str
    columns: int = 48
    rows: list[Row] = field(default_factory=list)

    # --- 組み立て ---
    def text(self, s: str, *, align: str = "left", size: int = 1, bold: bool = False) -> "Slip":
        self.rows.append(Row("text", left=s, align=align, size=size, bold=bold))
        return self

    def pair(self, left: str, right: str, *, size: int = 1, bold: bool = False) -> "Slip":
        self.rows.append(Row("pair", left=left, right=right, size=size, bold=bold))
        return self

    def rule(self) -> "Slip":
        self.rows.append(Row("rule"))
        return self

    def feed(self) -> "Slip":
        self.rows.append(Row("feed"))
        return self

    def stamp(self) -> "Slip":
        self.rows.append(Row("stamp"))
        return self

    def as_dict(self) -> dict:
        return {"kind": self.kind, "title": self.title, "columns": self.columns, "rows": [asdict(r) for r in self.rows]}


# --- 文字幅（全角 = 2 桁） --------------------------------------------------------
def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1 for ch in s)


def cut(s: str, max_width: int) -> str:
    out, w = [], 0
    for ch in s:
        cw = width(ch)
        if w + cw > max_width:
            break
        out.append(ch)
        w += cw
    return "".join(out)


def wrap(s: str, max_width: int) -> list[str]:
    """桁数で折り返す（単語の区切りは気にしない。伝票の 1 項目が長いときだけ使う）。"""
    if max_width <= 0:
        return [s]
    lines, cur, w = [], [], 0
    for ch in s:
        cw = width(ch)
        if w + cw > max_width and cur:
            lines.append("".join(cur))
            cur, w = [], 0
        cur.append(ch)
        w += cw
    if cur or not lines:
        lines.append("".join(cur))
    return lines


def pair_lines(left: str, right: str, cols: int) -> list[str]:
    """左右に振り分けた行。収まらなければ左を折り返して、最後の行の右に金額を置く。"""
    gap = 1
    if width(left) + gap + width(right) <= cols:
        return [left + " " * (cols - width(left) - width(right)) + right]
    lines = wrap(left, cols)
    last = lines[-1]
    if width(last) + gap + width(right) <= cols:
        lines[-1] = last + " " * (cols - width(last) - width(right)) + right
    else:
        lines.append(" " * max(0, cols - width(right)) + right)
    return lines


def plain_lines(slip: Slip) -> list[str]:
    """テキストにしたもの（テスト・ログ用。倍角の行は半分の桁で数える）。"""
    out: list[str] = []
    for r in slip.rows:
        cols = slip.columns // (2 if r.size == 2 else 1)
        if r.kind == "rule":
            out.append("-" * slip.columns)
        elif r.kind == "feed":
            out.append("")
        elif r.kind == "stamp":
            out.extend(["+--------+", "|収入印紙|", "+--------+"])
        elif r.kind == "pair":
            out.extend(pair_lines(r.left, r.right, cols))
        else:
            for ln in wrap(r.left, cols):
                pad = cols - width(ln)
                if r.align == "center":
                    ln = " " * (pad // 2) + ln
                elif r.align == "right":
                    ln = " " * pad + ln
                out.append(ln)
    return out


# --- ePOS-Print XML ------------------------------------------------------------------
def to_epos_xml(slip: Slip) -> str:
    """
    ePOS-Print XML（<epos-print> 要素）。TM Print Assistant に data-type=eposprintxml で渡す。
    日本語は <text lang="ja"/>。倍角は width/height=2。最後に用紙を送ってカットする。
    """
    out: list[str] = [f'<epos-print xmlns="{EPOS_NS}">', '<text lang="ja" smooth="true"/>']
    state = {"align": None, "size": None, "bold": None}

    def style(align: str, size: int, bold: bool):
        if state["align"] != align:
            out.append(f'<text align="{align}"/>')
            state["align"] = align
        if state["size"] != size:
            out.append(f'<text width="{size}" height="{size}"/>')
            state["size"] = size
        if state["bold"] != bold:
            out.append(f'<text em="{"true" if bold else "false"}"/>')
            state["bold"] = bold

    def line(s: str):
        # 日本語（lang="ja"）のプリンターは 0x5C を「¥」で印字する。エプソンのマニュアルも ¥ は「\」で書く前提なので、
        # 「¥」（U+00A5。Shift_JIS への変換で落ちることがある）は「\」（U+005C）にして送る。幅はどちらも半角 1 桁
        out.append(f"<text>{escape(s.replace(chr(0xA5), chr(0x5C)))}&#10;</text>")

    for r in slip.rows:
        cols = slip.columns // (2 if r.size == 2 else 1)
        if r.kind == "rule":
            style("left", 1, False)
            line("-" * slip.columns)
        elif r.kind == "feed":
            out.append('<feed line="1"/>')
        elif r.kind == "stamp":
            style("left", 1, False)
            for s in ("┌────┐", "│収入│", "│印紙│", "└────┘"):
                line(s)
        elif r.kind == "pair":
            style("left", r.size, r.bold)
            for s in pair_lines(r.left, r.right, cols):
                line(s)
        else:
            style(r.align, r.size, r.bold)
            for s in wrap(r.left, cols):
                line(s)
    out.append('<feed line="3"/>')
    out.append('<cut type="feed"/>')
    out.append("</epos-print>")
    return "".join(out)
