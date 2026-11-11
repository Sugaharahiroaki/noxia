"""二重登録防止（Idempotency-Key・同時操作）のテスト。"""
from __future__ import annotations

import threading
import uuid
from decimal import Decimal

from django.db import connections
from django.test import TransactionTestCase
from rest_framework.test import APIClient, APITestCase

from apps.core.models import IdempotencyRecord
from apps.core.testing import client_for, standard_fixture
from apps.sales.models import Checkout, CourseEntry, OrderLine, Visit


def _open(c, fx):
    r = c.post("/api/visits", {"table_id": fx.tables[0].id, "entries": [{"guest_count": 1, "course_id": fx.hourly.id}]}, format="json",
               HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
    assert r.status_code == 201, r.content
    return r.json()


class IdempotencyTests(APITestCase):
    def setUp(self):
        self.fx = standard_fixture()
        self.c = client_for(self.fx.users["manager"], self.fx.store)

    def test_same_key_checkout_returns_replay_without_duplicate(self):
        v = _open(self.c, self.fx)
        key = str(uuid.uuid4())
        body = {"payments": [{"method": "cash", "amount": "2500", "tendered": "3000"}]}
        r1 = self.c.post(f"/api/visits/{v['id']}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r1.status_code, 201, r1.content)
        r2 = self.c.post(f"/api/visits/{v['id']}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r2.status_code, 201)
        self.assertEqual(r2["Idempotent-Replayed"], "true")
        self.assertEqual(r1.json(), r2.json())
        self.assertEqual(Checkout.objects.count(), 1)
        self.assertEqual(Checkout.objects.get().payments.count(), 1)
        rec = IdempotencyRecord.objects.get(key=key)
        self.assertEqual(rec.state, "done")
        self.assertEqual(rec.status_code, 201)
        # キーなしで再送すると、伝票は会計済みなので 409（重複請求にならない）
        r3 = self.c.post(f"/api/visits/{v['id']}/checkouts", body, format="json")
        self.assertEqual(r3.status_code, 409)
        self.assertEqual(Checkout.objects.count(), 1)

    def test_same_key_different_body_rejected(self):
        v = _open(self.c, self.fx)
        key = str(uuid.uuid4())
        r1 = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": 1}, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r1.status_code, 201)
        r2 = self.c.post(f"/api/visits/{v['id']}/lines", {"kind": "product", "product_id": self.fx.products["beer"].id, "quantity": 2}, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r2.status_code, 422)
        self.assertEqual(r2.json()["error"]["code"], "idempotency_mismatch")
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="product").count(), 1)

    def test_extend_twice_same_key_counts_once(self):
        v = _open(self.c, self.fx)
        entry = CourseEntry.objects.get(visit_id=v["id"])
        key = str(uuid.uuid4())
        for _ in range(3):
            r = self.c.post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", HTTP_IDEMPOTENCY_KEY=key)
            self.assertEqual(r.status_code, 200)
        entry.refresh_from_db()
        self.assertEqual(entry.extension_count, 1)
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="extension").count(), 1)
        self.assertEqual(Decimal(r.json()["quote"]["total"]), Decimal("5000"))

    def test_error_response_is_not_cached(self):
        v = _open(self.c, self.fx)
        key = str(uuid.uuid4())
        bad = {"payments": [{"method": "cash", "amount": "1"}]}
        r1 = self.c.post(f"/api/visits/{v['id']}/checkouts", bad, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r1.status_code, 400)
        self.assertFalse(IdempotencyRecord.objects.filter(key=key).exists())
        good = {"payments": [{"method": "cash", "amount": "2500"}]}
        # 同じキーでも内容が違うので新規として処理される（記録が消えているため）
        r2 = self.c.post(f"/api/visits/{v['id']}/checkouts", good, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(r2.status_code, 201)

    def test_key_is_per_user(self):
        v1 = _open(self.c, self.fx)
        c2 = client_for(self.fx.users["staff"], self.fx.store)
        v2 = _open(c2, self.fx)
        key = str(uuid.uuid4())
        body = {"payments": [{"method": "cash", "amount": "2500"}]}
        r1 = self.c.post(f"/api/visits/{v1['id']}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        r2 = c2.post(f"/api/visits/{v2['id']}/checkouts", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual((r1.status_code, r2.status_code), (201, 201))
        self.assertNotEqual(r1.json()["id"], r2.json()["id"])
        self.assertEqual(Checkout.objects.count(), 2)


class ConcurrentCheckoutTests(TransactionTestCase):
    """複数タブ・同時操作: 同じ伝票への会計確定が同時に走っても 1 件しか作られない。"""

    def setUp(self):
        self.fx = standard_fixture()

    def _run_parallel(self, make_request, n=2):
        results = [None] * n
        barrier = threading.Barrier(n)

        def worker(i):
            try:
                barrier.wait(timeout=10)
                results[i] = make_request(i)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return results

    def test_concurrent_confirm_different_keys(self):
        c = client_for(self.fx.users["manager"], self.fx.store)
        v = _open(c, self.fx)
        clients = [client_for(self.fx.users["manager"], self.fx.store), client_for(self.fx.users["staff"], self.fx.store)]

        def req(i):
            r = clients[i].post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "2500"}]}, format="json",
                                HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()))
            return r.status_code

        codes = sorted(self._run_parallel(req))
        self.assertEqual(codes[0], 201, codes)
        self.assertIn(codes[1], (400, 409), codes)
        self.assertEqual(Checkout.objects.filter(visit_id=v["id"]).count(), 1)
        self.assertEqual(Visit.objects.get(id=v["id"]).status, "closed")

    def test_concurrent_same_key(self):
        c = client_for(self.fx.users["manager"], self.fx.store)
        v = _open(c, self.fx)
        key = str(uuid.uuid4())
        clients = [client_for(self.fx.users["manager"], self.fx.store), client_for(self.fx.users["manager"], self.fx.store)]

        def req(i):
            r = clients[i].post(f"/api/visits/{v['id']}/checkouts", {"payments": [{"method": "cash", "amount": "2500"}]}, format="json",
                                HTTP_IDEMPOTENCY_KEY=key)
            return r.status_code

        codes = sorted(self._run_parallel(req))
        self.assertEqual(codes[0], 201, codes)
        self.assertIn(codes[1], (201, 409), codes)  # 完了済みなら再生、処理中なら 409
        self.assertEqual(Checkout.objects.filter(visit_id=v["id"]).count(), 1)
        self.assertEqual(IdempotencyRecord.objects.filter(key=key).count(), 1)

    def test_concurrent_extend(self):
        c = client_for(self.fx.users["manager"], self.fx.store)
        v = _open(c, self.fx)
        entry = CourseEntry.objects.get(visit_id=v["id"])
        clients = [client_for(self.fx.users["manager"], self.fx.store) for _ in range(3)]

        def req(i):
            return clients[i].post(f"/api/visits/{v['id']}/course-entries/{entry.id}/extend", {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4())).status_code

        codes = self._run_parallel(req, n=3)
        self.assertEqual(codes, [200, 200, 200])
        entry.refresh_from_db()
        # 異なるキーの 3 操作はそれぞれ有効（行ロックで直列化され、回数は 3）
        self.assertEqual(entry.extension_count, 3)
        self.assertEqual(OrderLine.objects.filter(visit_id=v["id"], line_type="extension").count(), 3)
