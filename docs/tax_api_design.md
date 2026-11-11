# 会計・確定申告統合 API 設計案

状態: **Phase 0 の提案（未実装・承認待ち）**。2026-11-11 作成。
DB は `tax_database_design.md`、権限とモジュール構成は `tax_architecture.md`、既存 API は `ACCOUNTING_DESIGN.md` §4 を参照。

---

## 1. 共通ルール（既存の流儀を踏襲）

| 項目 | 内容 |
|---|---|
| 認証・店舗 | Cookie セッション＋CSRF。すべて `X-Store-Id` 必須（`apps/core/permissions.py: resolve_store_access` が所属を照合）。事業者（TaxPayer）は店舗の `StoreTaxProfile` から解決する。事業者全体の集計も「選んでいる店舗の事業者」が対象 |
| 権限 | `permission_classes = [StoreCaps("cap", write_caps=(...))]`。**画面で隠すだけでなく必ず API で検証**。新しいケイパビリティは §2 |
| 再認証 | `REAUTH_CAPS` に追加したものは更新系で再認証（10分）を要求。フロントは `withReauth(() => api(...))` で包む |
| 二重登録防止 | 更新系 POST は `@idempotent`（`Idempotency-Key`）。**ファイルアップロードにも付ける**（既存の `uploadFile` にキー引数を追加し、証憑 POST にも `@idempotent` を付ける） |
| 楽観ロック | 承認・編集が競合しうる資源（領収書・申告書）は `version` を body に含め、違えば 409 `version_conflict` |
| 金額 | JSON では文字列（`"12345.00"`）。既存の型（`frontend/src/lib/types.ts`）と同じ |
| エラー | `{error:{code,message,details}}`。`details` に項目ごとのエラー（フロントは今後 `ErrorBox` で表示） |
| 監査 | 承認・取消・照合確定・年度締め・計算・出力・ダウンロードは `core.audit.record`。申告関連はさらに `tax.TaxAuditLog` |
| ファイル配信 | 公開 URL を作らない。`GET …/file` は権限確認後にストリーム。プレビュー用に `?inline=1` を追加（既存は `as_attachment=True` のみ） |
| ページング | 一覧は `?page=&page_size=`（既定 50、最大 200）。既存 API はページングなしだが、銀行明細・領収書は件数が多いため導入 |

---

## 2. ケイパビリティの追加

`apps/core/permissions.py` に追加。既存の役割への割り当て（○＝持つ、◇＝店舗設定で許可したとき、再＝再認証）。

| cap | 内容 | オーナー | 会社管理者 | 店長 | スタッフ | 記帳代行 | 税理士等 | 運営者 |
|---|---|---|---|---|---|---|---|---|
| `expenses.submit` | 領収書・経費の登録（撮影・手入力・再提出） | ○ | ○ | ○ | ○ | ○ | – | ○ |
| `expenses.view_store` | 自店舗の登録状況の閲覧（金額・状態・差戻し理由のみ。科目・税務情報は返さない） | ○ | ○ | ○ | ○ | ○ | – | ○ |
| `expenses.approve` | 承認・差戻し・却下・訂正 | ○ | ○ | ◇ `StoreSettings.manager_can_approve_expenses` | – | ○ | – | ○ |
| `banking.view` | 口座・明細・照合の閲覧 | ○ | ○ | – | – | ○ | ○ | ○ |
| `banking.import` | CSV 取込・取消 | ○ | ○ | – | – | ○ | – | ○ |
| `banking.reconcile` | 照合の確定・消込解除・対象外の指定 | ○ | ○ | – | – | ○ | – | ○ |
| `books.adjust` | 手入力仕訳・決算整理仕訳・勘定科目の編集 | ○ | ○ | – | – | ○ | – | ○ |
| `yearend.prepare` | 棚卸・固定資産・減価償却・決算整理の作成（下書き） | ○ | ○ | – | – | ○ | – | ○ |
| `yearend.confirm`（再） | 年度の締め・再開 | ○ | – | – | – | – | – | – |
| `tax.return.view` | 申告書データ・入力ガイドの閲覧 | ○ | ○ | – | – | ○ | ○ | ○ |
| `tax.return.edit`（再） | 所得控除などの入力・計算の実行・版の作成・完成 | ○ | – | – | – | – | – | – |
| `tax.return.export`（再） | PDF/CSV/XLSX/XTX の出力・ダウンロード | ○ | – | – | – | – | – | – |
| `tax.config.manage`（再） | 年度別税制設定の編集・検証済みへの変更 | – | – | – | – | – | – | ○ |

- 既存の `accounting.view` は帳簿（元帳・各帳簿）の閲覧にも使う。`accounting.bookkeep` は従来どおり経費・証憑・仕訳作成。
- 税務判断（`tax.decide`、`yearend.confirm`、`tax.return.*`）は経営者本人だけ。記帳代行・税理士等・運営者には持たせない（税理士法 52 条への配慮。既存方針と同じ）。
- 店長の `expenses.approve` は `resolve_store_access` で店舗設定を見て動的に付与する。

---

## 3. エンドポイント一覧

### 3.1 帳簿基盤（`/api/accounting/…` 追加分）— Phase 1

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| GET `accounts` | 勘定科目一覧（category・statement_line・既定税区分） | accounting.view |
| POST `accounts` / PATCH `accounts/{id}` | 科目の追加・名称変更・無効化（`is_system` は削除不可） | books.adjust |
| POST `accounts/seed` | 標準科目の再投入（不足分だけ） | books.adjust |
| GET `fiscal-years` / POST `fiscal-years` | 年度一覧・作成（個人事業主は暦年） | accounting.view / yearend.prepare |
| GET `fiscal-years/{year}` | 状態・警告（締めていない営業日、仮勘定、未承認領収書、未照合明細、締め後の元データ変更） | accounting.view |
| POST `fiscal-years/{year}/close` / `reopen` | 年度の締め・再開（理由必須） | yearend.confirm（再） |
| GET `journals` | 既存。`?entry_kind=&status=&account=&store=&page=` を追加 | accounting.view |
| POST `journals/build` | 既存（締め済み年度は飛ばし、drift 警告を返す） | accounting.bookkeep |
| POST `journals/manual` | 手入力仕訳（行配列。貸借不一致は 400） | books.adjust |
| PATCH `journals/{id}` / POST `journals/{id}/void` | 手入力仕訳の訂正（revision）・取消。自動仕訳は不可 | books.adjust |
| GET `ledgers/general?account=&from=&to=` | 総勘定元帳（科目別・残高つき） | accounting.view |
| GET `ledgers/cash?store=&from=&to=` | 現金出納帳（店舗別。既存の `cash.CashMovement`/締めと突合した形） | accounting.view |
| GET `ledgers/bank?bank_account=&from=&to=` | 預金出納帳（口座別・残高つき。銀行明細の残高と比較） | accounting.view |
| GET `ledgers/sales?store=&from=&to=&group=day\|month` | 売上帳（現金・カード・ツケ、税率別、返金） | accounting.view |
| GET `ledgers/receivables?from=&to=` | 売掛帳（カード: 売上−入金=未入金残高、決済会社別。ツケ: 既存 Receivable） | accounting.view |
| GET `ledgers/payables?from=&to=` | 買掛帳（Payable と支払い） | accounting.view |
| GET `ledgers/purchases` / `ledgers/expenses?account=` | 仕入帳・経費帳（科目別） | accounting.view |
| GET `ledgers/staff-payments?employee=&from=&to=` | スタッフ支払台帳（支払・源泉・控除・差引） | tax.view |
| GET `ledgers/{name}.csv` / `.xlsx` | 各帳簿の出力 | accounting.view |
| GET `pl?from=&to=&by=store\|taxpayer` | 既存を拡張（店舗別と事業者合計、収支内訳書の行キーごと） | accounting.view |

### 3.2 領収書・経費（`/api/expenses/…`）— Phase 2

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| POST `receipts` (multipart) | 画像/PDF と `payment_method`・`memo` を送って登録。sha256・phash を計算し重複候補を返す。AI 有効なら `status=ocr_running` で非同期読取を開始、無効なら `pending` で手入力待ち。`Idempotency-Key` 必須 | expenses.submit |
| GET `receipts/{id}` | 状態・AI 結果・確認値・重複候補（**スタッフには科目・税区分・承認メモを返さない**） | expenses.view_store（自店舗）/ accounting.view |
| GET `receipts/{id}/file?inline=1` | 原本（プレビュー用はインライン） | 同上 |
| PATCH `receipts/{id}` | スタッフ確認値（日付・支払先・金額・税率内訳・登録番号・内容・支払方法・メモ）の保存。`version` 必須 | expenses.submit（自分が登録した pending/returned のみ）|
| POST `receipts/{id}/submit` | 「登録」（draft/returned → pending） | expenses.submit |
| POST `receipts/{id}/ocr/retry` | AI 読取の再実行（失敗時） | expenses.submit |
| POST `receipts/{id}/void` | 登録者による取消（pending まで） | expenses.submit |
| GET `receipts?store=&status=&from=&to=&page=` | 一覧。スタッフは自店舗のみ・項目を制限 | expenses.view_store / accounting.view |
| GET `receipts/pending-count` | ダッシュボード用の未処理件数（確認待ち・差戻し・AI失敗） | expenses.view_store / accounting.view |
| POST `receipts/{id}/approve` | 承認。body: 確定値（date, vendor, amount, tax_breakdown, account, tax_category, store, payment_method, note, `duplicate_override_reason`）。`store_cash` なら CashMovement を作成または既存出金に紐付け（`cash_movement_id`）、それ以外は Expense（または Payable）を作成。`version` 必須 | expenses.approve |
| POST `receipts/{id}/return` | 差戻し（理由必須。スタッフに通知表示） | expenses.approve |
| POST `receipts/{id}/reject` | 却下（理由必須。帳簿に入れない。原本は保存期間まで残す） | expenses.approve |
| POST `receipts/{id}/correct` | 承認後の訂正（取消＋新規の Expense、理由必須） | expenses.approve |
| GET `receipts/{id}/events` | 履歴 | accounting.view |
| GET `receipts/duplicates?from=&to=` | 重複候補の一覧（未解決） | expenses.approve |
| GET `register-check?store=&date=` | 店舗×日の POS 出金合計と承認済み領収書（store_cash）の突合（ミセミル register_check 相当） | accounting.view |
| GET/POST `payables`, PATCH `payables/{id}`, POST `payables/{id}/void` | 買掛金・未払金 | accounting.view / accounting.bookkeep |
| POST `payables/{id}/payments`, POST `payables/{id}/payments/{pid}/void` | 支払いの記録・取消 | accounting.bookkeep |
| GET `settings/ai` / PUT `settings/ai` | 会社の AI 設定（有効・予算・当月使用額。API キーは返さない） | accounting.settings |

AI 読取は Celery を入れずに **DB キュー＋管理コマンド／スレッド** で実装する（現状の構成に合わせる）。`status` を 2 秒ごとにポーリングするのは既存 `TerminalWaitDialog` と同じ型。

### 3.3 銀行（`/api/banking/…`）— Phase 3

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| GET/POST `accounts`, PATCH `accounts/{id}` | 銀行口座（口座番号は下4桁だけ保存） | banking.view / banking.import |
| GET/POST `profiles`, PATCH `profiles/{id}` | 列対応プロファイル | banking.view / banking.import |
| POST `imports/preview` (multipart) | CSV を解析して先頭 20 行・推測した列対応・文字コード・重複見込み・残高の連続性を返す（保存しない） | banking.import |
| POST `imports` (multipart) | 取込（profile または column_map を指定）。結果は `status=parsed`（確認待ち） | banking.import |
| POST `imports/{id}/commit` / `rollback` | 確定／取消（確定前のみ） | banking.import |
| GET `imports?bank_account=` | 取込履歴 | banking.view |
| GET `transactions?bank_account=&status=&from=&to=&q=&page=` | 明細一覧（未照合・候補あり・照合済み・対象外） | banking.view |
| GET `transactions/{id}/candidates` | 照合候補（ルール名・差額・スコア） | banking.view |
| POST `transactions/{id}/reconcile` | 確定。body: `[{match_type, target_id, amount, fee_amount}]`（多対一・一対多可）。カード入金なら CardSettlement を作成、手数料仕訳を生成 | banking.reconcile |
| POST `transactions/{id}/ignore` | 対象外（家事費など。理由必須） | banking.reconcile |
| POST `transactions/{id}/transfer` | 自社口座間の振替として相手明細と結ぶ | banking.reconcile |
| POST `reconciliations/{id}/void` | 消込解除（理由必須。生成した仕訳も取消） | banking.reconcile |
| POST `scan?from=&to=` | 候補の再計算 | banking.reconcile |
| GET `summary?from=&to=` | 未照合件数・金額、口座残高と帳簿残高の差 | banking.view |

### 3.4 スタッフ支払・源泉（既存 `/api/accounting/withholding/…` の拡張）— Phase 3

| 変更 | 内容 |
|---|---|
| `contracts` の `tax_category` に `outsourcing_fee` | 一般業務委託費。源泉徴収の対象かは所得税法 204 条の該当性を経営者が確認（要確認のまま計算しない） |
| GET `withholding/annual?year=` | 年間のスタッフ別支払合計（支払調書の判定材料。50万円超の表示。提出は利用者） |
| GET `ledgers/staff-payments` | §3.1 |

### 3.5 年間決算（`/api/closing/…`）— Phase 4

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| GET `years/{year}/checklist` | 決算チェック（締め未了日・未承認領収書・未照合・仮勘定・棚卸未入力・償却未計算・青色承認） | accounting.view |
| GET/POST `inventory?year=&store=&kind=` | 期首・期末棚卸（既存 `StockTake` から下書き作成可） | accounting.view / yearend.prepare |
| POST `inventory/{id}/confirm` | 確定→決算整理仕訳 | yearend.prepare |
| GET/POST `fixed-assets`, PATCH `fixed-assets/{id}`, POST `fixed-assets/{id}/dispose` | 固定資産・開業費 | accounting.view / yearend.prepare |
| POST `depreciation/compute?year=` | 年度の償却を計算（根拠を返す。償却率はマスタの値） | yearend.prepare |
| GET `depreciation?year=` | 償却一覧（固定資産台帳） | accounting.view |
| GET/POST `adjustments?year=`, POST `adjustments/{id}/void` | 決算整理（未払金・未収金・前払・家事按分・貸倒） | accounting.view / yearend.prepare |
| GET `years/{year}/pl?by=store\|taxpayer` | 年間損益（決算整理後。収支内訳書の行キー） | accounting.view |
| GET `years/{year}/pl.xlsx` / `.pdf` | 年間損益・月次損益・決算整理一覧の出力 | accounting.view |

### 3.6 確定申告（`/api/tax/…`）— Phase 5〜7

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| GET/PUT `profile` | 個人事業主の申告情報（屋号・開業日・青色承認・消費税区分・登録番号） | tax.return.view / tax.return.edit（再） |
| GET `years` | 対応年度と設定の検証状態（`TaxYearConfiguration.status`） | tax.return.view |
| GET `years/{year}/config` | 年度の税制設定（率・控除表・様式版・ガイド定義・一次資料）。運営者以外は閲覧のみ | tax.return.view |
| PUT `years/{year}/config` | 設定の更新（運営者） | tax.config.manage（再） |
| GET `returns/{year}` | 申告書データ（inputs・business_summary・computed・unsupported・warnings・status・version） | tax.return.view |
| PUT `returns/{year}/inputs` | 事業所得以外の入力（給与・他の所得・所得控除・源泉徴収税額・予定納税）。`version` 必須 | tax.return.edit（再） |
| POST `returns/{year}/compute` | 集計と税額計算を実行し `TaxReturnVersion` を作る。未対応項目があれば `unsupported_items` を返し `complete` にしない | tax.return.edit（再） |
| GET `returns/{year}/versions`, GET `versions/{id}` | 版一覧・内容（計算過程） | tax.return.view |
| POST `returns/{year}/complete` / `reopen` | 完成（未対応 0・警告の確認済み）／差し戻し | tax.return.edit（再） |
| POST `returns/{year}/mark-filed` | 利用者が国税庁で申告したことの記録（受付番号は任意） | tax.return.edit（再） |
| GET `returns/{year}/guide?filing=white\|blue` | 入力ガイド（年度の `guide` 定義＋計算値。`official_label` が未確認の項目は「要確認」表示） | tax.return.view |
| PUT `returns/{year}/guide/progress` | チェック・コピー済みの保存 | tax.return.view（本人） |
| GET `returns/{year}/guide.pdf` | A4 ガイド PDF | tax.return.export（再） |
| POST `returns/{year}/exports` | 出力の生成（format 指定）。XTX は `is_released=false` の間は運営者のみ | tax.return.export（再） |
| GET `exports/{id}` / GET `exports/{id}/file` | 出力の状態・検証結果／ダウンロード（監査ログ） | tax.return.export（再） |
| GET `audit?year=` | 申告関連の監査ログ | tax.return.view |

### 3.7 既存 API の変更

| 既存 | 変更 |
|---|---|
| `POST /api/cash/movements`（kind=expense） | 任意の `receipt_id`（登録済み領収書との紐付け）と `account_key` を受け付ける。締め画面の「現金経費」から直接「領収書を撮る」に進めるようにする |
| `GET /api/cash/current` | 当日の「領収書なしの現金経費」件数を返す（締め時の注意表示） |
| `GET /api/accounting/overview` | 未処理件数（確認待ち領収書・未照合明細・AI失敗・差戻し）と年度の状態を追加 |
| `POST /api/accounting/evidences` | `@idempotent` を付ける。`origin`・`captured_at` を受け付ける。`GET …/file?inline=1` を追加 |
| `GET /api/auth/me` | `stores[].caps` に新 cap が入る（変更不要） |
| `GET /api/analytics/*` | 変更なし（ダッシュボードは overview の件数を使う） |

---

## 4. 代表的なリクエスト／レスポンス

### 4.1 領収書の登録（タブレット）

```
POST /api/expenses/receipts           (multipart, Idempotency-Key: <uuid>)
  file=<image/jpeg>  payment_method=store_cash  memo=氷・レモン
→ 201
{ "id": 123, "status": "ocr_running", "evidence": {"id": 456, "sha256": "...", "phash": "..."},
  "duplicate_candidates": [], "poll_after_ms": 2000 }

GET /api/expenses/receipts/123
→ 200
{ "id": 123, "status": "pending", "ocr": {"status": "done", "provider": "anthropic", "model": "...",
    "date": "2026-11-10", "vendor": "○○マート", "total": "3240", "tax_breakdown": [{"rate": "8", "taxable_incl": "1080", "tax": "80"}, {"rate": "10", "taxable_incl": "2160", "tax": "196"}],
    "invoice_number": "T1234567890123", "summary": "氷・レモン・紙ナプキン", "missing_fields": [], "warnings": []},
  "values": { "receipt_date": "2026-11-10", "vendor": "○○マート", "total_amount": "3240", ... },
  "payment_method": "store_cash", "version": 2 }
```

スタッフ向けの応答には `account`・`tax_category`・`approved_*`・`duplicate_candidates` の詳細を含めない（`ExpenseReceiptStaffSerializer`）。

### 4.2 承認（PC）

```
POST /api/expenses/receipts/123/approve   (Idempotency-Key)
{ "version": 2, "date": "2026-11-10", "vendor": "○○マート", "amount": "3240",
  "tax_breakdown": [...], "account": 17, "tax_category": "purchase_10", "store": 1,
  "payment_method": "store_cash", "cash_movement_id": null, "note": "", "duplicate_override_reason": "" }
→ 200 { "status": "approved", "cash_movement": {"id": 789, "business_day": "2026-11-10"}, "journal_effect": "daily:2026-11-10" }
→ 409 { "error": {"code": "duplicate_suspected", "message": "同じ領収書の可能性があります。", "details": {"candidates": [...]}} }   # override_reason が無いとき
→ 409 { "error": {"code": "business_day_closed", "message": "この営業日は締め済みです。締め後の修正として登録しますか。", "details": {...}} }
```

`store_cash` で営業日が締め済みのときは、既存の「締め後の修正」（`closing.edit_closed`・ClosingRevision）に乗せるか、`paid_from=office_cash` の Expense として登録するかを承認者が選ぶ（自動で決めない）。

### 4.3 税額計算

```
POST /api/tax/returns/2026/compute
→ 200
{ "version": {"id": 31, "version_no": 3, "config_hash": "..."},
  "computed": {
    "business_income": {"revenue": "12,000,000", "cogs": "...", "expenses": {...}, "blue_deduction": "100000", "amount": "..."},
    "total_income": "...", "deductions": [{"key": "basic", "label": "基礎控除", "amount": "...", "basis": "config.deductions.basic[...]"}, ...],
    "taxable_income": "...（1,000円未満切捨て）", "income_tax": "...", "reconstruction_tax": "...", "credits": [...],
    "withheld": "...", "estimated_paid": "...", "due": "...", "refund": "0" },
  "unsupported_items": [], "warnings": ["青色申告の承認状況が未設定です"] }
```

未対応の所得・控除が `inputs` に含まれる場合は `unsupported_items` に理由を入れ、`status` は `in_review` のまま。ガイド・出力は「未完成」と表示する。

---

## 5. シリアライザ／ビューの配置

| アプリ | ファイル |
|---|---|
| accounting | `views_books.py`（accounts, fiscal-years, ledgers）、`ledgers.py`（集計）、`serializers_books.py` |
| expenses | `models.py`, `services.py`（登録・承認・CashMovement/Expense 生成）、`ocr/providers.py`（mock / anthropic）、`ocr/schema.py`、`ocr/runner.py`（キュー処理）、`duplicates.py`、`register_check.py`、`views.py`, `serializers.py` |
| banking | `models.py`, `importer.py`（文字コード・列対応・正規化）、`matching.py`（候補）、`services.py`（確定・解除・仕訳）、`views.py` |
| closing | `models.py`, `inventory.py`, `depreciation.py`（償却率はデータ `data/depreciation_rates_*.json`）、`adjustments.py`, `views.py` |
| tax | `models.py`, `rules/__init__.py`（年→計算モジュールの登録）、`rules/y2026.py`、`compute.py`（計算過程の組み立て）、`statements.py`（収支内訳書・決算書の行の集計）、`guide.py`、`exports/{pdf,csv,xlsx,xtx}.py`、`views.py` |

---

## 6. フロントエンドの対応（要約。詳細は `tax_architecture.md` §6）

- `src/lib/api.ts`: `uploadFile` に `idempotencyKey`・`onProgress`（XHR）・`signal` を追加。`ApiError.details` を `ErrorBox` で表示。
- `src/components/AppShell.tsx`: 上部バーに「領収書」（`expenses.submit`）、NAV「経理」に 会計ダッシュボード／経費・領収書／銀行／帳簿／売掛・買掛／スタッフ支払／固定資産・棚卸／年間決算／確定申告／入力ガイド／ファイル出力／会計設定。
- 新規の共通部品: `CameraCapture`（`<input type=file accept=image/* capture=environment>`＋プレビュー＋縮小）、`PollingDialog`（TerminalWaitDialog の汎用化）、`Wizard`、`Tabs`、`CopyButton`、`Checklist`、`CsvColumnMapper`（freee の Mapping 表を流用）、`DataTable`（ページ送り・複数選択）、`FileViewer`（画像/PDF）、`PrintLayout`（A4）。
