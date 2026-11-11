from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import Product
from apps.core.api import StoreScopedViewSet, current_store
from apps.core.audit import audit
from apps.core.exceptions import ApiError
from apps.core.idempotency import idempotent
from apps.core.money import D, ZERO
from apps.core.permissions import (
    CAP_EXPORT_INVENTORY, CAP_INVENTORY_MANAGE, CAP_INVENTORY_OPERATE, CAP_INVENTORY_VIEW, CAP_PURCHASING_CONFIRM, CAP_PURCHASING_DRAFT, StoreCaps,
)
from apps.sales.services import allocate_number

from . import services
from .models import (
    GoodsReceipt, GoodsReceiptLine, PurchaseOrder, PurchaseOrderLine, PurchaseOrderStatus, StockMovement, StockMovementKind, StockTake,
    StockTakeLine, Supplier, SupplierProduct,
)
from .serializers import (
    PurchaseOrderSerializer, StockMovementSerializer, StockTakeSerializer, SupplierProductSerializer, SupplierSerializer,
)


class SupplierViewSet(StoreScopedViewSet):
    model = Supplier
    serializer_class = SupplierSerializer
    caps_read = (CAP_INVENTORY_VIEW,)
    caps_write = (CAP_INVENTORY_MANAGE,)

    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.is_active = False
        obj.save(update_fields=["is_active"])
        return Response(status=204)


class SupplierProductViewSet(StoreScopedViewSet):
    model = SupplierProduct
    serializer_class = SupplierProductSerializer
    caps_read = (CAP_INVENTORY_VIEW,)
    caps_write = (CAP_INVENTORY_MANAGE,)

    def get_queryset(self):
        qs = super().get_queryset().select_related("supplier", "product")
        if self.request.query_params.get("supplier"):
            qs = qs.filter(supplier_id=self.request.query_params["supplier"])
        return qs


class StockLevelsView(APIView):
    """商品ごとの在庫（理論値は明示）。"""

    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW)]

    def get(self, request):
        store = request.store
        levels = services.stock_levels(store)
        rows = []
        for p in Product.objects.filter(store=store).exclude(stock_mode="none").select_related("category").order_by("category__sort_order", "sort_order", "id"):
            qty = levels.get(p.id, ZERO)
            low = p.reorder_point is not None and qty <= p.reorder_point
            rows.append({
                "product_id": p.id, "name": p.name, "category": p.category.name, "stock_mode": p.stock_mode, "is_active": p.is_active,
                "quantity_bottles": str(qty.quantize(Decimal("0.01"))),
                "is_theoretical": p.stock_mode == "shot",
                "volume_ml": str(p.volume_ml) if p.volume_ml else None, "shot_ml": str(p.shot_ml) if p.shot_ml else None,
                "shots_remaining_est": str((qty * D(p.volume_ml) / D(p.shot_ml)).quantize(Decimal("1"))) if p.stock_mode == "shot" and p.volume_ml and p.shot_ml else None,
                "reorder_point": str(p.reorder_point) if p.reorder_point is not None else None,
                "min_stock": str(p.min_stock) if p.min_stock is not None else None,
                "low_stock": low, "cost_price": str(p.cost_price) if p.cost_price is not None else None,
            })
        return Response(rows)


class StockMovementsView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW, write_caps=(CAP_INVENTORY_OPERATE,))]

    def get(self, request):
        qs = StockMovement.objects.filter(store=request.store).select_related("product", "performed_by")
        if request.query_params.get("product"):
            qs = qs.filter(product_id=request.query_params["product"])
        return Response(StockMovementSerializer(qs[:300], many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        """入荷(receive, 発注外)・廃棄(waste)・調整(adjust)・キープ移管(keep_out) {product_id, kind, quantity, reason}"""
        store = request.store
        product = Product.objects.filter(store=store, id=request.data.get("product_id")).first()
        if product is None:
            raise ApiError("validation_error", "この店舗の商品を指定してください。")
        kind = request.data.get("kind")
        if kind not in (StockMovementKind.RECEIVE, StockMovementKind.WASTE, StockMovementKind.ADJUST, StockMovementKind.KEEP_OUT):
            raise ApiError("validation_error", "在庫操作の種別が不正です。")
        qty = D(request.data.get("quantity") or 0)
        if qty == 0:
            raise ApiError("validation_error", "数量を入力してください。")
        if kind in (StockMovementKind.WASTE, StockMovementKind.KEEP_OUT):
            qty = -abs(qty)
        elif kind == StockMovementKind.RECEIVE:
            qty = abs(qty)
        reason = (request.data.get("reason") or "").strip()
        if kind != StockMovementKind.RECEIVE and not reason:
            raise ApiError("validation_error", "理由を入力してください。")
        mv = StockMovement.objects.create(store=store, product=product, kind=kind, quantity=qty, reason=reason[:200], occurred_at=timezone.now(), performed_by=request.user)
        audit(request, f"stock.{kind}", target=mv, changes={"product": product.name, "quantity": qty, "reason": reason})
        return Response(StockMovementSerializer(mv).data, status=201)


class StockTakesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW, write_caps=(CAP_INVENTORY_OPERATE,))]

    def get(self, request):
        return Response(StockTakeSerializer(StockTake.objects.filter(store=request.store).prefetch_related("lines__product")[:50], many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        """
        棚卸し: {lines: [{product_id, counted_full_bottles, counted_open_ml, note}], note, apply: bool}
        実在庫を本換算し、理論在庫との差を調整として記録する（apply=true のとき）。
        """
        store = request.store
        levels = services.stock_levels(store)
        now = timezone.now()
        take = StockTake.objects.create(store=store, taken_at=now, performed_by=request.user, note=(request.data.get("note") or "")[:200])
        for ln in request.data.get("lines") or []:
            product = Product.objects.filter(store=store, id=ln.get("product_id")).first()
            if product is None or product.stock_mode == "none":
                raise ApiError("validation_error", "在庫管理対象の商品を指定してください。")
            full = D(ln.get("counted_full_bottles") or 0)
            open_ml = D(ln.get("counted_open_ml") or 0)
            counted = full
            if open_ml and product.volume_ml:
                counted += (open_ml / D(product.volume_ml)).quantize(Decimal("0.0001"))
            theoretical = levels.get(product.id, ZERO)
            StockTakeLine.objects.create(stock_take=take, product=product, theoretical_qty=theoretical, counted_full_bottles=full, counted_open_ml=open_ml,
                                         counted_qty=counted, difference=counted - theoretical, note=(ln.get("note") or "")[:200])
        if request.data.get("apply"):
            for line in take.lines.all():
                if line.difference != 0:
                    StockMovement.objects.create(store=store, product=line.product, kind=StockMovementKind.ADJUST, quantity=line.difference,
                                                 reference=f"棚卸し#{take.id}", reason="棚卸調整", occurred_at=now, performed_by=request.user)
            take.status = "applied"
            take.applied_at = now
            take.save(update_fields=["status", "applied_at"])
        audit(request, "stock.take", target=take, changes={"applied": bool(request.data.get("apply")), "lines": take.lines.count()})
        return Response(StockTakeSerializer(StockTake.objects.prefetch_related("lines__product").get(pk=take.pk)).data, status=201)


class ReorderCandidatesView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW)]

    def get(self, request):
        store = request.store
        levels = services.stock_levels(store)
        rows = []
        for p in Product.objects.filter(store=store, is_active=True).exclude(stock_mode="none").filter(reorder_point__isnull=False):
            qty = levels.get(p.id, ZERO)
            if qty <= p.reorder_point:
                sp = SupplierProduct.objects.filter(product=p).order_by("-is_preferred", "purchase_price").select_related("supplier").first()
                target = p.min_stock if p.min_stock is not None else p.reorder_point
                shortfall = max(D(target) - qty, ZERO)
                rows.append({
                    "product_id": p.id, "name": p.name, "quantity_bottles": str(qty.quantize(Decimal("0.01"))), "reorder_point": str(p.reorder_point),
                    "suggested_bottles": str(shortfall.quantize(Decimal("1"), rounding="ROUND_CEILING")),
                    "supplier_id": sp.supplier_id if sp else None, "supplier_name": sp.supplier.name if sp else None,
                    "supplier_product_id": sp.id if sp else None, "order_unit_name": sp.order_unit_name if sp else "本",
                    "units_per_order_unit": str(sp.units_per_order_unit) if sp else "1", "purchase_price": str(sp.purchase_price) if sp else None,
                })
        return Response(rows)


def _po_qs(store):
    return PurchaseOrder.objects.filter(store=store).select_related("supplier").prefetch_related("lines__product", "lines__supplier_product", "receipts__lines")


class PurchaseOrdersView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW, write_caps=(CAP_PURCHASING_DRAFT,))]

    def get(self, request):
        qs = _po_qs(request.store)
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        return Response(PurchaseOrderSerializer(qs[:100], many=True).data)

    @idempotent
    @transaction.atomic
    def post(self, request):
        """下書き作成 {supplier_id, lines: [{product_id, order_units, supplier_product_id?, unit_price?}], note, expected_on}"""
        store = request.store
        supplier = Supplier.objects.filter(store=store, id=request.data.get("supplier_id"), is_active=True).first()
        if supplier is None:
            raise ApiError("validation_error", "仕入先を選択してください。")
        lines = request.data.get("lines") or []
        if not lines:
            raise ApiError("validation_error", "発注明細を1件以上入力してください。")
        po = PurchaseOrder.objects.create(store=store, supplier=supplier, number=allocate_number(store, "next_po_number"), created_by=request.user,
                                          note=(request.data.get("note") or "")[:300], expected_on=parse_date(request.data.get("expected_on") or "") or None)
        _save_po_lines(store, po, lines)
        audit(request, "po.create", target=po, changes={"supplier": supplier.name})
        return Response(PurchaseOrderSerializer(_po_qs(store).get(pk=po.pk)).data, status=201)


def _save_po_lines(store, po, lines):
    po.lines.all().delete()
    for ln in lines:
        product = Product.objects.filter(store=store, id=ln.get("product_id")).first()
        if product is None:
            raise ApiError("validation_error", "この店舗の商品を指定してください。")
        units = D(ln.get("order_units") or 0)
        if units <= 0:
            raise ApiError("validation_error", f"{product.name} の発注数は1以上で入力してください。")
        sp = None
        if ln.get("supplier_product_id"):
            sp = SupplierProduct.objects.filter(id=ln["supplier_product_id"], store=store, supplier=po.supplier, product=product).first()
        if sp is None:
            sp = SupplierProduct.objects.filter(store=store, supplier=po.supplier, product=product).first()
        PurchaseOrderLine.objects.create(
            purchase_order=po, product=product, supplier_product=sp, order_units=units,
            units_per_order_unit=sp.units_per_order_unit if sp else D(ln.get("units_per_order_unit") or 1),
            unit_price=D(ln["unit_price"]) if ln.get("unit_price") is not None else (sp.purchase_price if sp else ZERO),
            note=(ln.get("note") or "")[:200],
        )


class PurchaseOrderDetailView(APIView):
    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW, write_caps=(CAP_PURCHASING_DRAFT,))]

    def get(self, request, po_id):
        po = _po_qs(request.store).filter(id=po_id).first()
        if po is None:
            raise ApiError("not_found", "発注が見つかりません。", status=404)
        return Response(PurchaseOrderSerializer(po).data)

    @transaction.atomic
    def patch(self, request, po_id):
        po = PurchaseOrder.objects.select_for_update().filter(id=po_id, store=request.store).first()
        if po is None:
            raise ApiError("not_found", "発注が見つかりません。", status=404)
        if po.status != PurchaseOrderStatus.DRAFT:
            raise ApiError("invalid_state", "下書き以外の発注は編集できません。", status=409)
        if "lines" in request.data:
            _save_po_lines(request.store, po, request.data["lines"] or [])
        if "note" in request.data:
            po.note = (request.data.get("note") or "")[:300]
        if "expected_on" in request.data:
            po.expected_on = parse_date(request.data.get("expected_on") or "") or None
        po.save()
        audit(request, "po.update", target=po)
        return Response(PurchaseOrderSerializer(_po_qs(request.store).get(pk=po.pk)).data)


class PurchaseOrderTransitionView(APIView):
    """状態遷移: confirm（オーナー確認）/ mark-ordered（仕入先へ連絡済み）/ cancel"""

    def get_permissions(self):
        action = self.kwargs.get("action")
        if action == "confirm":
            return [StoreCaps(CAP_PURCHASING_CONFIRM)]
        return [StoreCaps(CAP_PURCHASING_DRAFT)]

    @idempotent
    @transaction.atomic
    def post(self, request, po_id, action):
        po = PurchaseOrder.objects.select_for_update().filter(id=po_id, store=request.store).first()
        if po is None:
            raise ApiError("not_found", "発注が見つかりません。", status=404)
        now = timezone.now()
        if action == "confirm":
            if po.status != PurchaseOrderStatus.DRAFT:
                raise ApiError("invalid_state", "下書きの発注だけ確認できます。", status=409)
            if not po.lines.exists():
                raise ApiError("validation_error", "発注明細がありません。")
            po.status = PurchaseOrderStatus.CONFIRMED
            po.confirmed_by = request.user
            po.confirmed_at = now
        elif action == "mark-ordered":
            if po.status != PurchaseOrderStatus.CONFIRMED:
                raise ApiError("invalid_state", "オーナー確認済みの発注だけ「発注済み」にできます。自動送信は行いません。", status=409)
            via = (request.data.get("ordered_via") or "").strip()
            if not via:
                raise ApiError("validation_error", "どの手段で仕入先に連絡したか（電話・FAX・メール等）を入力してください。")
            po.status = PurchaseOrderStatus.ORDERED
            po.ordered_by = request.user
            po.ordered_at = now
            po.ordered_via = via[:60]
        elif action == "cancel":
            if po.status in (PurchaseOrderStatus.RECEIVED, PurchaseOrderStatus.CANCELLED):
                raise ApiError("invalid_state", "この発注は取消できません。", status=409)
            if po.receipts.exists():
                raise ApiError("invalid_state", "入荷済みの発注は取消できません。", status=409)
            po.status = PurchaseOrderStatus.CANCELLED
        else:
            raise ApiError("validation_error", "操作が不正です。")
        po.save()
        audit(request, f"po.{action}", target=po)
        return Response(PurchaseOrderSerializer(_po_qs(request.store).get(pk=po.pk)).data)


class PurchaseOrderReceiveView(APIView):
    """入荷登録（部分入荷対応） {lines: [{po_line_id, bottles}], note}"""

    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_OPERATE)]

    @idempotent
    @transaction.atomic
    def post(self, request, po_id):
        po = PurchaseOrder.objects.select_for_update().filter(id=po_id, store=request.store).first()
        if po is None:
            raise ApiError("not_found", "発注が見つかりません。", status=404)
        if po.status not in (PurchaseOrderStatus.ORDERED, PurchaseOrderStatus.PARTIAL, PurchaseOrderStatus.CONFIRMED):
            raise ApiError("invalid_state", "発注済み（または確認済み）の発注だけ入荷登録できます。", status=409)
        now = timezone.now()
        receipt = GoodsReceipt.objects.create(purchase_order=po, store=request.store, received_at=now, received_by=request.user, note=(request.data.get("note") or "")[:200])
        any_line = False
        for ln in request.data.get("lines") or []:
            pol = po.lines.filter(id=ln.get("po_line_id")).select_related("product").first()
            if pol is None:
                raise ApiError("validation_error", "発注明細が見つかりません。")
            bottles = D(ln.get("bottles") or 0)
            if bottles <= 0:
                continue
            any_line = True
            GoodsReceiptLine.objects.create(receipt=receipt, po_line=pol, bottles=bottles)
            pol.received_bottles += bottles
            pol.save(update_fields=["received_bottles"])
            StockMovement.objects.create(store=request.store, product=pol.product, kind=StockMovementKind.RECEIVE, quantity=bottles,
                                         reference=f"発注#{po.number}", occurred_at=now, performed_by=request.user)
        if not any_line:
            raise ApiError("validation_error", "入荷数量を入力してください。")
        complete = all(l.received_bottles >= l.expected_bottles for l in po.lines.all())
        po.status = PurchaseOrderStatus.RECEIVED if complete else PurchaseOrderStatus.PARTIAL
        po.save(update_fields=["status", "updated_at"])
        audit(request, "po.receive", target=po, changes={"complete": complete})
        return Response(PurchaseOrderSerializer(_po_qs(request.store).get(pk=po.pk)).data, status=201)


class PurchaseOrderExportView(APIView):
    """発注内容のコピー用テキストと CSV。"""

    def get_permissions(self):
        return [StoreCaps(CAP_INVENTORY_VIEW)]

    def get(self, request, po_id):
        po = _po_qs(request.store).filter(id=po_id).first()
        if po is None:
            raise ApiError("not_found", "発注が見つかりません。", status=404)
        fmt = request.query_params.get("format", "text")
        if fmt == "csv":
            if CAP_EXPORT_INVENTORY not in request.store_access.caps:
                raise ApiError("forbidden", "CSV出力の権限がありません。", status=403)
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["発注番号", "仕入先", "商品", "発注数", "発注単位", "入荷本数/単位", "仕入単価", "金額"])
            for l in po.lines.all():
                w.writerow([po.number, po.supplier.name, l.product.name, l.order_units, l.supplier_product.order_unit_name if l.supplier_product else "本",
                            l.units_per_order_unit, l.unit_price, l.order_units * l.unit_price])
            resp = HttpResponse("﻿" + buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="po_{po.number}.csv"'
            audit(request, "export.po_csv", target=po)
            return resp
        def fmt(d: Decimal) -> str:
            d = D(d).normalize()
            return format(d, "f") if d != d.to_integral() else str(int(d))

        lines = [f"【発注書】{request.store.name} → {po.supplier.name}", f"発注番号: {po.number}", ""]
        for l in po.lines.all():
            unit = l.supplier_product.order_unit_name if l.supplier_product else "本"
            lines.append(f"・{l.product.name} × {fmt(l.order_units)}{unit}（{fmt(l.expected_bottles)}本）")
        if po.expected_on:
            lines.append(f"\n希望納品日: {po.expected_on.isoformat()}")
        if po.note:
            lines.append(f"備考: {po.note}")
        return Response({"text": "\n".join(lines)})
