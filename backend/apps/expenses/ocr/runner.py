"""AI 読取ジョブ（core.jobs の "expenses.ocr"）。失敗しても登録は手入力で続けられる。"""
from __future__ import annotations

import logging

from django.db import transaction

from apps.core import jobs

from .providers import OcrError, get_provider

log = logging.getLogger("yorumiru.expenses.ocr")


@jobs.register("expenses.ocr")
def run_ocr(job):
    from .. import services
    from ..models import ExpenseReceipt, OcrStatus

    receipt = ExpenseReceipt.objects.select_related("evidence", "company", "taxpayer", "store").filter(id=job.payload.get("receipt_id")).first()
    if receipt is None:
        return {"skipped": "receipt not found"}
    if receipt.status not in ("ocr_running",) and receipt.ocr_status != OcrStatus.RUNNING:
        return {"skipped": f"status={receipt.status}"}
    provider = get_provider(receipt.company)
    if provider is None:
        services.mark_ocr_skipped(receipt, "AI 読取が無効のため手入力")
        return {"skipped": "ai disabled"}
    try:
        with receipt.evidence.file.open("rb") as fh:
            data = fh.read()
        # ファイル名・店舗名・個人名は渡さない。画像は縮小・再エンコードしたもの（EXIF なし）
        result = provider.read(services.image_for_ai(data, receipt.evidence.content_type), receipt.evidence.content_type,
                               accounts=services.account_candidates(receipt), today=None, sha256=receipt.evidence.sha256)
    except OcrError as e:
        if e.retryable and job.attempts < job.max_attempts:
            raise jobs.RetryableError(str(e))
        services.mark_ocr_failed(receipt, str(e))
        return {"failed": str(e)}
    except Exception as e:  # noqa: BLE001
        log.exception("ocr failed for receipt %s", receipt.id)
        services.mark_ocr_failed(receipt, (str(e) or e.__class__.__name__)[:200])
        return {"failed": str(e)[:200]}
    with transaction.atomic():
        services.apply_ocr_result(receipt, result)
    return {"ok": True, "provider": result.provider, "model": result.model, "cost_jpy": str(result.cost_jpy)}
