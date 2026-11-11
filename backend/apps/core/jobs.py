"""
非同期ジョブ（DB キュー）。Celery を使わず、`manage.py run_jobs` が定期的に取り出して実行する。

- enqueue(kind, payload, company=None, run_after=None) -> BackgroundJob
- register(kind)(handler): handler(job) -> dict | None。例外は失敗として記録し、retryable なら指数バックオフで再試行。
- run_pending(limit) は行ロック（skip_locked）で取り出すため、複数プロセスで動かしても二重実行しない。
"""
from __future__ import annotations

import logging
import traceback
from datetime import timedelta
from typing import Callable

from django.db import transaction
from django.utils import timezone

from .models import BackgroundJob

log = logging.getLogger("yorumiru.jobs")

_HANDLERS: dict[str, Callable] = {}


class RetryableError(Exception):
    """一時的なエラー（通信など）。再試行する。"""


def register(kind: str):
    def deco(fn: Callable):
        _HANDLERS[kind] = fn
        return fn

    return deco


def enqueue(kind: str, payload: dict | None = None, *, company=None, run_after=None, max_attempts: int = 3, created_by=None) -> BackgroundJob:
    return BackgroundJob.objects.create(
        kind=kind, payload=payload or {}, company=company, run_after=run_after or timezone.now(), max_attempts=max_attempts, created_by=created_by,
    )


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempts, 60))


def run_one(job: BackgroundJob) -> BackgroundJob:
    handler = _HANDLERS.get(job.kind)
    job.status = BackgroundJob.RUNNING
    job.started_at = timezone.now()
    job.attempts += 1
    job.save(update_fields=["status", "started_at", "attempts", "updated_at"])
    if handler is None:
        job.status, job.error = BackgroundJob.FAILED, f"ジョブの種類 {job.kind} の処理がありません"
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "error", "finished_at", "updated_at"])
        return job
    try:
        result = handler(job)
    except RetryableError as e:
        if job.attempts < job.max_attempts:
            job.status, job.error, job.run_after = BackgroundJob.QUEUED, str(e)[:500], timezone.now() + _backoff(job.attempts)
        else:
            job.status, job.error, job.finished_at = BackgroundJob.FAILED, str(e)[:500], timezone.now()
        job.save(update_fields=["status", "error", "run_after", "finished_at", "updated_at"])
        return job
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job.id)
        job.status, job.error, job.finished_at = BackgroundJob.FAILED, (str(e) or e.__class__.__name__)[:500], timezone.now()
        job.result = {"traceback": traceback.format_exc()[-2000:]}
        job.save(update_fields=["status", "error", "finished_at", "result", "updated_at"])
        return job
    job.status, job.result, job.error, job.finished_at = BackgroundJob.DONE, result or {}, "", timezone.now()
    job.save(update_fields=["status", "result", "error", "finished_at", "updated_at"])
    return job


def run_pending(limit: int = 20) -> int:
    """実行待ちのジョブを順に実行する。戻り値は実行した件数。"""
    count = 0
    for _ in range(limit):
        with transaction.atomic():
            job = (
                BackgroundJob.objects.select_for_update(skip_locked=True)
                .filter(status=BackgroundJob.QUEUED, run_after__lte=timezone.now())
                .order_by("run_after", "id")
                .first()
            )
            if job is None:
                break
            job.status = BackgroundJob.RUNNING
            job.save(update_fields=["status", "updated_at"])
        run_one(job)
        count += 1
    return count


def run_now(job: BackgroundJob) -> BackgroundJob:
    """テスト・同期実行用。"""
    return run_one(job)
