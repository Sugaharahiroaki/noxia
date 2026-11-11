"""
XTX（e-Tax 申告データ）の生成の枠組み。Phase 7。

- 国税庁の XML 構造設計書・帳票フィールド仕様・XML スキーマ（令和8年分の確定版は 2026-11-25 掲載予定）に基づく対応表
  apps/tax/data/xtx/<year>/fields.json（computed/summary のキー → XML 要素パス）が無ければ生成しない（推測で作らない）。
- 生成後は XSD（settings.TAX_XTX_SCHEMA_DIR/<year>/*.xsd）で検証し、必須項目・帳票間連動を確認する。
- 公式 e-Tax ソフトでの取込試験に合格した年度だけ公開（settings.TAX_XTX_RELEASED_YEARS）。それ以外は運営者だけ取得できる。
- 確定申告書等作成コーナーは他社の .xtx を取り込めない（公式 FAQ）。利用者には e-Tax ソフト（WEB 版／DL 版）で読み込む経路を案内する。
"""
from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings

from apps.core.exceptions import ApiError

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "xtx"


def mapping_for(year: int) -> dict | None:
    p = DATA_DIR / str(year) / "fields.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _resolve(path: str, ctx: dict):
    cur = ctx
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def generate(version) -> tuple[bytes, dict]:
    """XTX（XML）を生成し、検証結果を返す。対応表が無ければ ApiError(xtx_not_available)。"""
    from lxml import etree

    tr = version.tax_return
    year = tr.config.year
    mapping = mapping_for(year)
    if mapping is None:
        raise ApiError(
            "xtx_not_available",
            f"{year}年分の e-Tax 申告データ（XTX）は、国税庁の仕様（XML 構造設計書・スキーマ）の確定版に基づく対応表がまだ用意されていないため作成できません。"
            "確定申告書等作成コーナーへの転記（入力ガイド）をご利用ください。",
            status=409,
        )
    ctx = {"computed": {**version.computed, "deductions": {d["key"]: d["amount"] for d in version.computed.get("deductions", [])}},
           "summary": version.business_summary, "inputs": version.inputs}
    nsmap = mapping.get("nsmap") or {}
    root = etree.Element(mapping["root"], nsmap=nsmap or None)
    missing = []
    for field in mapping["fields"]:
        value = _resolve(field["source"], ctx)
        if value in (None, "") and field.get("required"):
            missing.append(field["path"])
            continue
        if value in (None, ""):
            continue
        node = root
        for tag in field["path"].split("/"):
            found = node.find(tag)
            if found is None:
                found = etree.SubElement(node, tag)
            node = found
        node.text = str(int(float(value))) if field.get("type") == "int" else str(value)
    xml = etree.tostring(root, xml_declaration=True, encoding=mapping.get("encoding", "UTF-8"), pretty_print=False)
    validation = {"schema": "skipped", "missing_required": missing, "spec_version": mapping.get("spec_version", ""), "ok": not missing}
    schema_dir = Path(getattr(settings, "TAX_XTX_SCHEMA_DIR", "") or "") / str(year)
    xsd = mapping.get("xsd")
    if xsd and (schema_dir / xsd).exists():
        try:
            schema = etree.XMLSchema(etree.parse(str(schema_dir / xsd)))
            ok = schema.validate(etree.fromstring(xml))
            validation["schema"] = "ok" if ok else "failed"
            validation["schema_errors"] = [str(e) for e in schema.error_log][:20]
            validation["ok"] = validation["ok"] and ok
        except Exception as e:  # noqa: BLE001
            validation["schema"] = "error"
            validation["schema_errors"] = [str(e)]
            validation["ok"] = False
    else:
        validation["schema"] = "missing"
        validation["ok"] = False
        validation["note"] = "XML スキーマ（XSD）が配置されていないため検証していません。"
    return xml, validation


def is_released(year: int, validation: dict) -> bool:
    return year in getattr(settings, "TAX_XTX_RELEASED_YEARS", []) and bool(validation.get("ok"))
