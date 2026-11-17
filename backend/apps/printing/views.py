"""
レシートプリンターの API。

- 設定: 店舗のメンバーなら読める（印刷ボタンを出すかどうかに使う）。変更は店舗設定の権限＋パスワード再確認。
- 伝票: 画面のプレビュー（slip）と、プリンターに渡すデータ（epos_xml）を返す。印刷そのものは iPad のエプソンのアプリが行う。
  プリンターを使わない店でも、概算伝票などは画面で見せられる（printable=false）。
"""
from __future__ import annotations

from django.db.models import Prefetch
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.permissions import CAP_CLOSING, CAP_FLOOR, CAP_SETTINGS_EDIT, StoreCaps
from apps.sales import receipts
from apps.sales.models import ReceiptIssue, ReceiptIssueMethod

from . import documents
from .models import PrinterConfig
from .serializers import PrinterConfigSerializer
from .slip import Slip, to_epos_xml

DOC_FLAGS = {"estimate": "print_estimate", "receipt": "print_receipt", "ryoshusho": "print_ryoshusho", "closing": "print_closing", "test": None}


def config_for(store) -> PrinterConfig:
    cfg, _ = PrinterConfig.objects.get_or_create(store=store)
    return cfg


def printable(cfg: PrinterConfig, kind: str) -> bool:
    flag = DOC_FLAGS.get(kind)
    return bool(cfg.enabled and (flag is None or getattr(cfg, flag)))


def slip_response(cfg: PrinterConfig, slip: Slip, **extra) -> Response:
    return Response({
        "slip": slip.as_dict(),
        "epos_xml": to_epos_xml(slip),
        "printable": printable(cfg, slip.kind),
        "paper_width": cfg.paper_width,
        **extra,
    })


class PrinterConfigView(APIView):
    def get_permissions(self):
        return [StoreCaps(write_caps=(CAP_SETTINGS_EDIT,))]

    def get(self, request):
        return Response(PrinterConfigSerializer(config_for(request.store)).data)

    def put(self, request):
        cfg = config_for(request.store)
        ser = PrinterConfigSerializer(cfg, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        audit(request, "printing.config.update", target=cfg, changes={k: v for k, v in ser.validated_data.items()})
        return Response(PrinterConfigSerializer(cfg).data)


class TestPrintView(APIView):
    def get_permissions(self):
        return [StoreCaps()]

    def get(self, request):
        cfg = config_for(request.store)
        return slip_response(cfg, documents.test_slip(cfg))


class EstimatePrintView(APIView):
    """概算伝票: いま会計したら／延長したら（延長の候補ごと）の金額。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, visit_id):
        from apps.sales import services as sales_services
        from apps.sales.models import CourseEntry, Visit

        visit = (Visit.objects.filter(id=visit_id, store=request.store).select_related("table")
                 .prefetch_related(Prefetch("course_entries", queryset=CourseEntry.objects.filter(is_active=True)), "guests").first())
        if visit is None:
            raise ApiError("not_found", "伝票が見つかりません。", status=404)
        if visit.status != "open":
            raise ApiError("visit_closed", "この伝票は会計済みです。", status=409)
        tiers = sales_services.estimate_tiers(visit)
        cfg = config_for(request.store)
        slip = documents.estimate_slip(cfg, visit, tiers, sales_services.visit_present_count(visit))
        return slip_response(cfg, slip, tiers=tiers)


def _get_checkout(request, checkout_id):
    from apps.sales.models import Checkout

    c = (Checkout.objects.filter(id=checkout_id, store=request.store).select_related("visit__table")
         .prefetch_related("lines", "payments").first())
    if c is None:
        raise ApiError("not_found", "会計が見つかりません。", status=404)
    return c


class ReceiptPrintView(APIView):
    """会計伝票（レシート）。会計で確定した金額をそのまま印字する。"""

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def get(self, request, checkout_id):
        cfg = config_for(request.store)
        return slip_response(cfg, documents.receipt_slip(cfg, _get_checkout(request, checkout_id)))


def _ryoshusho_issued(store, checkout) -> bool:
    """もう渡したか（印刷・手書きのどちらでも。取り消した記録は数えない）。売上・会計履歴の「発行済」と同じ記録を見る。"""
    return ReceiptIssue.objects.filter(store=store, checkout=checkout, cancelled_at__isnull=True).exists()


class RyoshushoPrintView(APIView):
    """
    領収書。GET は宛名なしの下見（記録しない）、POST で発行（宛名・但し書きを入れる。発行の記録を残し、2 回目からは「再発行」と印字）。
    領収額 = 現金＋カード（未収は含めない）。取消済みの会計・領収額 0 円は出さない。
    """

    def get_permissions(self):
        return [StoreCaps(CAP_FLOOR)]

    def _check(self, checkout):
        if checkout.status != "confirmed":
            raise ApiError("checkout_voided", "取消済みの会計の領収書は出せません。", status=409)
        if documents.ryoshusho_amounts(checkout)["amount"] <= 0:
            raise ApiError("validation_error", "領収する金額がありません（全額が後日のお支払いです）。")

    def get(self, request, checkout_id):
        checkout = _get_checkout(request, checkout_id)
        self._check(checkout)
        cfg = config_for(request.store)
        issued = _ryoshusho_issued(request.store, checkout)
        slip = documents.ryoshusho_slip(cfg, checkout, addressee=request.query_params.get("addressee", ""),
                                        proviso=request.query_params.get("proviso", ""), reissue=issued)
        return slip_response(cfg, slip, issued_before=issued, proviso_default=cfg.ryoshusho_proviso)

    @idempotent
    def post(self, request, checkout_id):
        checkout = _get_checkout(request, checkout_id)
        self._check(checkout)
        addressee = str(request.data.get("addressee") or "").strip()[:40]
        proviso = str(request.data.get("proviso") or "").strip()[:40]
        cfg = config_for(request.store)
        # 発行の記録（売上・会計履歴の「発行済」に出る）。再発行かどうかも記録と同時に決める
        issue = receipts.record_issue(request.store, request.user, checkout.id, addressee=addressee,
                                      proviso=proviso or cfg.ryoshusho_proviso, method=ReceiptIssueMethod.PRINTED)
        issued = issue.reissue
        slip = documents.ryoshusho_slip(cfg, checkout, addressee=addressee, proviso=proviso, reissue=issued)
        audit(request, "print.ryoshusho", target=checkout, changes={
            "number": checkout.number, "addressee": addressee, "proviso": proviso or cfg.ryoshusho_proviso,
            "amount": documents.ryoshusho_amounts(checkout)["amount"], "reissue": issued,
        })
        return slip_response(cfg, slip, issued_before=issued, proviso_default=cfg.ryoshusho_proviso)


class ClosingPrintView(APIView):
    """精算伝票（営業日の集計。締め前に出すと「締め前」と印字）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_CLOSING)]

    def get(self, request, day_id):
        from apps.cash import services as cash_services
        from apps.cash.models import BusinessDay

        day = BusinessDay.objects.filter(id=day_id, store=request.store).select_related("closed_by").first()
        if day is None:
            raise ApiError("not_found", "営業日が見つかりません。", status=404)
        cfg = config_for(request.store)
        return slip_response(cfg, documents.closing_slip(cfg, day, cash_services.day_summary(day)))
