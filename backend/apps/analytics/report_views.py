"""管理用の集計 API（読み取り専用）。集計の中身は reports.py。"""
from __future__ import annotations

from datetime import date

from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.business_date import current_business_date
from apps.core.exceptions import ApiError
from apps.core.permissions import CAP_SALES_VIEW, StoreCaps

from . import reports

MAX_RANGE_DAYS = 400


def parse_range(request) -> tuple[date, date]:
    """from / to（営業日）。省略時は当月 1 日〜今の営業日。"""
    today = current_business_date(request.store)
    f = parse_date(request.query_params.get("from") or "") or today.replace(day=1)
    t = parse_date(request.query_params.get("to") or "") or today
    if t < f:
        f, t = t, f
    if (t - f).days > MAX_RANGE_DAYS:
        raise ApiError("validation_error", f"期間は{MAX_RANGE_DAYS}日以内で指定してください。")
    return f, t


def flag(request, name: str) -> bool:
    return request.query_params.get(name) in ("1", "true", "True")


class SlipListView(APIView):
    """伝票一覧（期間、支払方法、値引きあり、取消済みで絞り込み）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request):
        f, t = parse_range(request)
        return Response(reports.slip_list(request.store, f, t, payment=request.query_params.get("payment") or "",
                                          discount_only=flag(request, "discount"), cancelled=flag(request, "cancelled")))


class SlipDetailView(APIView):
    """伝票詳細（明細、会計、キャスト別の売上小計）。別店舗の伝票は 404。"""

    def get_permissions(self):
        return [StoreCaps(CAP_SALES_VIEW)]

    def get(self, request, visit_id):
        data = reports.slip_detail(request.store, visit_id)
        if data is None:
            raise ApiError("not_found", "伝票が見つかりません。", status=404)
        return Response(data)
