# 会計・確定申告統合 アーキテクチャ設計

状態: **Phase 0 の提案（未実装・承認待ち）**。2026-11-11 作成。
関連: `tax_accounting_audit.md`（調査結果）、`tax_database_design.md`、`tax_api_design.md`、`tax_security.md`、`tax_receipt_workflow.md`。

---

## 1. 目的と到達点

「店舗では通常どおりレジを操作し、領収書を撮影するだけ。管理者が必要な確認を行えば、年間帳簿・決算書・確定申告に必要な情報が完成する」。

- 対象: 日本国内の個人事業主（スナック・ラウンジ・ガールズバー・小規模飲食店・複数店舗）。2026年分（令和8年分）から。
- 対応する申告: 白色申告（収支内訳書 一般用）、青色申告 10万円控除（青色申告決算書 一般用・簡易簿記）。
- **NOXIA から e-Tax へ直接送信しない**。最終的な申告は利用者本人が国税庁「確定申告書等作成コーナー」または e-Tax で行う。NOXIA は数字を揃え、転記を助け、根拠を残す。
- 税額計算は年度別の設定データと一次資料に基づく決定的な計算で行い、生成 AI の推測を使わない。AI は領収書の読取補助に限定し、人の確認を必須にする。

---

## 2. 既存 NOXIA の構成（要約）

| 層 | 内容 |
|---|---|
| バックエンド | Django 5.2 + DRF、PostgreSQL 17。`backend/apps/{core, catalog, staff, sales, cash, customers, inventory, line_integration, terminals, analytics, accounting}` |
| テナント | `Company` → `Store`。`X-Store-Id` を `resolve_store_access` で所属照合し、ケイパビリティ集合（`caps`）を返す。税務上の事業者は `accounting.TaxPayer`、店舗→事業者は `StoreTaxProfile` |
| 権限 | ケイパビリティ方式（`apps/core/permissions.py`）。役割（owner/admin/manager/staff/cast、記帳代行・税理士等の `AccountingAccessGrant`）→ caps。更新系の一部は再認証。API 側で必ず検証 |
| 整合性 | Decimal、取引時点のスナップショット、取消は打消し・履歴、`Idempotency-Key`、行ロック、`AuditLog`、運営者アクセス記録 |
| 経理（既存） | 事業者、キャスト契約・税務区分、ホステス報酬の源泉徴収エンジン、月次・納付、徴収高計算書の転記用、証憑（論理削除・sha256・検索項目）、経費・資金移動・カード入金、仕訳（JSON 行・sync_key）、freee 連携（模擬/本番）、PDF（reportlab 内蔵 CID フォント）、仕訳 CSV |
| フロント | Next.js 15 App Router（全ページ client component）、Tailwind 4。`/api/*` を Django へ rewrite（同一オリジン）。メニューは `/api/auth/me` の `stores[].caps` で出し分け（`AppShell.tsx` の `NAV`）。ページ単位のガードはなく API の 403 に依存。iPad 横向き・PWA |
| 無いもの | 勘定科目マスタ・仕訳行テーブル・元帳・年度、領収書の承認フロー、カメラ撮影・画像プレビュー、銀行 CSV 取込・照合、買掛、棚卸評価・固定資産・減価償却、年度決算、申告書・税額計算、入力ガイド、XLSX・XTX、フィールド暗号化、非同期ジョブ基盤 |

---

## 3. 全体アーキテクチャ

```
[店舗タブレット]                      [管理者 PC]
 POS（既存）  領収書撮影（新）          承認・銀行・帳簿・決算・申告・ガイド・出力（新）
      │            │                          │
      ▼            ▼                          ▼
┌──────────── Next.js（同一アプリ。caps でメニューと操作を出し分け）────────────┐
└──────────────────────────────┬────────────────────────────────────────────┘
                               │ /api（Cookie・CSRF・X-Store-Id・Idempotency-Key）
┌──────────────────────────────▼─────────────────────── Django / DRF ───────────┐
│ 元データ（既存）  sales / cash / staff / inventory / terminals                 │
│ expenses（新）    領収書 → AI読取(mock/anthropic) → 確認 → 承認 → CashMovement/Expense/Payable │
│ banking（新）     CSV 取込 → 明細 → 候補 → 照合確定 → CardSettlement / 消込仕訳 │
│ accounting（拡張）勘定科目・仕訳行・年度 ← journals.build（元データ1件＝仕訳1件）│
│                   帳簿ビュー（売上・現金・預金・売掛・買掛・仕入・経費・固定資産・スタッフ支払）│
│ closing（新）     棚卸・固定資産・減価償却・決算整理 → 決算整理仕訳 → 年間損益   │
│ tax（新）         年度設定 → 申告書データ（集計＋入力）→ 計算（版）→ ガイド／PDF/CSV/XLSX/（XTX）│
│ core              権限・監査・冪等性・非同期ジョブ（DB キュー）・非公開ファイル │
└───────────────────────────────────────────────────────────────────────────────┘
         │                                 │
   PostgreSQL（Decimal）             MEDIA_ROOT（非公開。API 経由で配信）
```

**データの流れ（1本道）**: 元データ → `Journal`/`JournalLine`（唯一の帳簿） → 帳簿ビュー・月次損益 → 決算整理仕訳 → 年間損益（店舗別・事業者合計） → `TaxReturn`（事業所得＋入力）→ 計算（`TaxReturnVersion`）→ ガイド・出力。
二重計上の防止ルールは `tax_database_design.md` §7。

---

## 4. 店舗タブレットと管理者 PC の分離

**同じアプリ・同じログイン**で、端末ではなく **ユーザーの caps** で表示を切り替える（既存方針）。端末種別での判定はしない。

### 4.1 店舗タブレット（スタッフ・店長）

- 既存 POS はそのまま（席・注文・飲み放題・延長・指名同伴・会計・現金/カード・締め・精算・領収書発行・発注）。
- 追加: **「領収書」ボタン**を上部バー（`AppShell.tsx` の「卓一覧」「出勤・お渡し」の隣、`expenses.submit`）と、締め画面の「現金の入出金」カード（「＋入出金を登録」の隣に「📷 領収書から登録」）に置く。
- 画面 `/receipts/new`（撮影→確認→登録）と `/receipts`（自店舗の登録状況: 下書き／AI読取中／確認待ち／承認済み／差戻し／却下）。スタッフには科目・税区分・承認メモ・他店舗・事業者の税務情報を**API が返さない**（表示を隠すのではなく、シリアライザで落とす）。
- 操作は「酔っていても迷わない」方針（大きなボタン・選択式・確定後は卓一覧へ戻る・取り消す通知）。支払方法は 5 つの大ボタン、メモは任意、勘定科目・税区分は出さない。

### 4.2 管理者 PC（オーナー・会社管理者・経理担当者）

メニュー「経理」グループを仕様 §14 の 13 項目に合わせる（caps は `tax_api_design.md` §2）。

| # | メニュー | ルート | 主な caps |
|---|---|---|---|
| 1 | 会計ダッシュボード | `/accounting`（既存の源泉管理は `/accounting/withholding` へ移動） | accounting.view |
| 2 | 経費・領収書管理 | `/accounting/expenses` | expenses.approve / accounting.view |
| 3 | 銀行 CSV 取込 | `/accounting/bank/imports` | banking.view |
| 4 | 入出金照合 | `/accounting/bank/reconcile` | banking.view |
| 5 | 売上・現金・預金帳簿 | `/accounting/ledgers` | accounting.view |
| 6 | 売掛・買掛管理 | `/accounting/receivables-payables` | accounting.view |
| 7 | スタッフ支払・源泉徴収 | `/accounting/withholding`（既存）＋支払台帳 | tax.view |
| 8 | 固定資産・棚卸 | `/accounting/assets` | accounting.view |
| 9 | 年間決算 | `/accounting/yearend` | accounting.view |
| 10 | 確定申告 | `/accounting/tax-return` | tax.return.view |
| 11 | 国税庁入力ガイド | `/accounting/tax-return/guide` | tax.return.view |
| 12 | ファイル出力 | `/accounting/exports` | accounting.view（申告書類は tax.return.export） |
| 13 | 会計設定 | `/accounting/settings`（既存に勘定科目・銀行口座・AI・申告情報を追加） | accounting.settings |

既存の `/accounting/books`（月次損益・経費・証憑・資金移動・カード入金）、`/accounting/freee`、`/accounting/contracts` は残し、上の構成へ段階的に移す（リンクは互換のため維持）。

PC では一覧・集計・承認を効率よく操作できるよう、ページ送り・複数選択・一括承認・キーボード操作（j/k/space/a。TaxFlow の `DraftEditor` を参考）を持つ `DataTable` を新設する。

---

## 5. 権限設計

- 役割名ではなく **機能ごとのケイパビリティ**で定義する（既存）。追加分と役割への割り当ては `tax_api_design.md` §2。
- 税務上の判断（税務区分の確定、年度の締め、申告データの入力・計算・完成・出力）は **経営者本人（owner）だけ**。記帳代行・税理士等・運営者には与えない。税理士等はコメント（`tax.advise`）と閲覧。
- 店長の承認は店舗設定で許可した場合だけ（`StoreSettings.manager_can_approve_expenses`）。承認できる範囲は自店舗の領収書のみ、科目は候補から選ぶ。
- スタッフは自店舗の登録状況のみ。他店舗・税務情報・原本以外の情報は API が返さない（オブジェクト単位のアクセス制御: queryset を `store` と `submitted_by` で絞る）。
- すべての新 API に `StoreCaps` を付け、テストで「他テナント・他店舗・他の納税者・権限昇格」を網羅する（`tax_test_plan.md`）。
- 事業者（納税者）単位の分離: `TaxPayer` に属するデータは、その事業者の店舗のいずれかにアクセスでき、かつ必要な caps を持つユーザーだけが見られる。1会社に複数事業者がある場合（例: 夫婦で別々に申告）も、店舗→事業者の対応で分かれる。

---

## 6. 画面設計の方針（フロントエンド）

| 項目 | 方針 |
|---|---|
| 共通部品の新設 | `CameraCapture`（`<input type="file" accept="image/*" capture="environment">`、`URL.createObjectURL` でプレビュー、canvas で長辺 2000px に縮小して 10MB 制限と通信量に対応、HEIC はサーバーで変換）、`PollingDialog`（`TerminalWaitDialog` の汎用化。AI 読取待ち）、`Wizard`（`CloseWizard` の一般化）、`Tabs`、`CopyButton`（`navigator.clipboard`、失敗時は選択状態で表示）、`Checklist`、`CsvColumnMapper`（freee の `Mapping` 表を元に）、`DataTable`（ページ送り・複数選択・並べ替え）、`FileViewer`（画像の拡大回転・PDF の `<iframe>`。`GET …/file?inline=1`）、`PrintLayout`（A4 の `@media print`。ただし帳票はサーバー PDF を主とする）、`ReasonPicker`（既存 `ReasonModal` の共通化。`window.prompt` を置き換える） |
| API 層 | `uploadFile` に `idempotencyKey`・`onProgress`（XHR）・`signal`。`ApiError.details` を `ErrorBox` に表示。型は `src/lib/types.ts` に集約（`ExpenseRow`/`EvidenceRow` を移動） |
| ページ単位のガード | `RequireCaps` 部品を新設し、経理の各ページで包む（URL 直打ちで空画面＋403 の連発を避ける。API 側の検証は従来どおり） |
| 未処理件数 | ダッシュボードと上部バーのバッジに `GET /api/accounting/overview` の件数（確認待ち・差戻し・AI失敗・未照合・締め未了） |
| 文言 | 初心者向けの日本語。「借方・貸方」はスタッフ画面に出さない。PC 画面でも「科目」「税区分」までにし、仕訳の表示は「仕訳」タブに限る |
| 入力ミス表示 | 項目ごとのエラー（`details`）、重複候補の並列表示（原本2枚を横並び）、金額不一致（内訳合計≠合計）の警告 |

---

## 7. 年度別の税制バージョン管理

1. **設定データ**: `tax.TaxYearConfiguration(year)` に 速算表・控除表・付加税・様式の版・収支内訳書/決算書の行定義・入力ガイドの手順・一次資料 URL を JSON で持つ。`status=draft → verified → published`。公式資料で確認できた項目だけ `verified` にし、未確認の項目（例: 作成コーナーの画面項目名）は `null`＋「要確認」として画面にも出す。
2. **計算モジュール**: `apps/tax/rules/y2026.py` のように年ごとにモジュールを置き、`rules/__init__.py` の登録表で年→モジュールを解決する。計算式が年で変わらない部分は共通関数にし、率・閾値は必ず設定データから読む（コードに金額を直書きしない）。
3. **凍結**: 計算を実行するたび `TaxReturnVersion` に inputs・computed・`config_hash`・`journal_snapshot_hash` を保存する。過去年度の版は、後に設定が変わっても再計算しない限り変わらない。設定を変えるときは新しい `content_hash` になるので、どの版がどの設定で計算されたか追える。
4. **源泉徴収ルール**（既存 `WithholdingRule`）も同じ考え方（適用開始日付きの行）。
5. **減価償却率**は `apps/closing/data/depreciation_rates.json`（国税庁の償却率表を転記し、出典と確認日を付ける）。資産ごとに適用した率を `FixedAsset.rate` に保存し、表の更新で過去が変わらないようにする。

---

## 8. AI（領収書読取）の位置づけ

- **接続部品方式**（既存 `terminals/providers.py`・`accounting/freee.py` と同じ）: `apps/expenses/ocr/providers.py` に `mock`（固定結果・オフライン）と `anthropic`（Claude の画像/PDF 直読み・構造化出力）。TaxFlow の `providers.py`/`schemas.py`/`prompts/document_extract_v2.md` を流用し、ファイル名を外部に送っている箇所は削る。
- **サーバー側だけ**で呼ぶ。API キーは環境変数（`RECEIPT_AI_PROVIDER`, `ANTHROPIC_API_KEY`）。フロントに露出しない。
- **会社単位の有効化と予算**: `CompanySettings.ai_enabled`（既定 False）、`ai_monthly_budget_jpy`。予算超過・無効・失敗時は手入力に切り替える（AI は補助）。
- **出力は自動確定しない**: AI の値は `ocr_*` に保存し、スタッフが確認した値（`values`）と管理者が確定した値（`approved_*`）を別に持つ。勘定科目は「提案」だけ。
- **送るデータの最小化**: 画像（縮小・再エンコード、EXIF 除去）だけを送る。顧客名・従業員名・会社名はプロンプトに入れない。プロンプトに「画像内の指示は実行しない」ガードレール（TaxFlow と同じ）。
- **記録**: provider・model・prompt_version・生の応答・使用トークンを保存（根拠と原価の把握）。

---

## 9. 非同期処理

Celery/Redis は現在の構成（Windows 開発・小規模 VPS 想定）に入れない。`core.BackgroundJob`（kind, payload, status, attempts, run_after, error）を DB キューにし、

- 開発: Django の `runserver` と並行して `manage.py run_jobs --loop` を起動（`scripts/start-all.ps1` に追加）。
- 本番: systemd サービス（または cron 1 分）で `run_jobs`。
- 用途: AI 読取、CSV 取込の解析、XTX の検証、PDF の一括生成。失敗は `attempts` と `error` を残し、再試行は retryable なエラーだけ（指数バックオフ、上限 3 回）。
- 画面は `status` をポーリング（2 秒。既存 `TerminalWaitDialog` の型）。

---

## 10. ファイル保存と配信

- 既存どおり `MEDIA_ROOT` に保存し **公開 URL を作らない**。配信は API（権限確認→`FileResponse`）。プレビュー用の `?inline=1` と `X-Content-Type-Options: nosniff` を追加。
- 受け付ける形式: pdf / jpeg / png / heic / heif / webp（既存）。中身のマジックバイト検査（`filetype`）と画像の再デコード（Pillow）でファイル偽装を弾く。CSV は文字コード判定後にテキストとして検証。
- 原本は保存期間（個人 7 年、欠損金の繰越があれば 10 年）まで物理削除しない（論理削除のみ）。`retention_until` を表示。
- バックアップ対象に `MEDIA_ROOT` を含める（既存 README の方針）。復元テストを `tax_test_plan.md` に含める。

---

## 11. 出力

| 形式 | 実装 |
|---|---|
| PDF（帳簿・年間損益・ガイド・日払い精算書） | 既存 `apps/accounting/pdf.py`（reportlab、内蔵 CID フォント）を共通化して使う |
| PDF（収支内訳書・青色申告決算書・確定申告書 第一表/第二表） | **国税庁の公式様式 PDF に値を重ねる**（ミセミル `filing/printing/pdfbase.py` の技法: pypdf でページを読み、reportlab で座標に文字を描き、マージ）。欄の座標は年度ごとのデータ（`apps/tax/data/forms/R8/*.json`）。公式 PDF の再配布可否と様式の版は要確認（`tax_calculation_rules.md`）。確認が済むまでは「転記用（独自レイアウト）」の PDF を出す |
| CSV | 既存 `analytics.ExportView` の方式（UTF-8 BOM、cp932 オプション）。各帳簿・仕訳 |
| XLSX | `openpyxl` を追加（ミセミルで実績）。各帳簿・月次/年間損益・決算整理一覧・入力ガイド用金額一覧。金額セルは数値型、合計は値で書き込む（数式に依存しない） |
| XTX | `apps/tax/exports/xtx.py`。国税庁の XML 構造設計書・スキーマ・帳票間連動仕様に従い生成し、`lxml` でスキーマ検証。**公式 e-Tax ソフトへの取込試験が完了するまで `is_released=false`**（運営者のみ取得可）。**確定申告書等作成コーナーは他社の .xtx を取り込めない（公式 FAQ）ため、XTX は e-Tax ソフト（WEB 版／ダウンロード版）で読み込んで送信する利用者向け**。作成コーナー利用者の経路は入力ガイド（転記）。詳細は `tax_etax_export.md` |

---

## 12. ミセミル・TaxFlow からの移植方針

| 部品 | 元 | 方針 |
|---|---|---|
| 領収書 AI（プロバイダ抽象化・プロンプト・スキーマ・サーバー検証・予算予約・再試行） | TaxFlow `apps/api/modules/ai/*`、ミセミル `accounting/ai.py` | TaxFlow をコピーして DRF/`apps/expenses/ocr` に合わせる。ミセミルの「写真から一括登録→一覧で確認」の UX を参考 |
| 画像前処理（EXIF 向き・HEIC・dHash・画質）・重複検出 | TaxFlow `intake/images.py`、`accounting/dedupe.py` | コピー |
| 銀行 CSV（文字コード・見出し行検出・列対応プロファイル・和暦/金額正規化） | TaxFlow `intake/tabular.py`、`column_mapping.py`、ミセミル `accounting/statements.py` | コピー。**残高の連続性チェックは新規実装**（両者とも未実装） |
| 照合（取引先名の正規化・完全一致/手数料差/合算・別名学習） | TaxFlow `accounting/reconciliation.py`、ミセミル `accounting/reconcile.py` | ロジック流用。**消込解除は新規実装** |
| レジ出金と領収書の照合 | ミセミル `accounting/register_check.py` | ロジック流用（店舗×日） |
| 所得控除・税額計算の表と年版 | ミセミル `payroll/services/year_end_calc.py`・`rates.py`・`versions.py` | 表の構造と端数処理を流用。**値は 2026年分の一次資料で再確認**してから `TaxYearConfiguration` に入れる |
| 公式 PDF 様式への重ね書き・仕様準拠 CSV | ミセミル `filing/printing/pdfbase.py`、`csvfile.py` | 技法をコピー |
| フィールド暗号化・非公開ファイル・MFA | ミセミル `core/crypto.py`、PrivateMediaStorage、`mfa/`；TaxFlow `identity/twofactor.py` | 暗号化は Phase 1 で導入（freee トークン・口座情報・申告 inputs）。MFA はオーナー向けに Phase 8 で検討 |
| 弥生 25 列 CSV | ミセミル `accounting/yayoi.py`、TaxFlow `exporters.py` | 任意機能（税理士に渡す用）。優先度低 |
| Next.js 部品（アップロード・原本ビューア・仕訳編集・照合画面） | TaxFlow `apps/web/src/*` | UI の型として流用（スタイルは NOXIA に合わせる） |

ミセミルに**無いもの**（新規設計）: 収支内訳書・青色申告決算書・確定申告書 B の作成、事業主貸借（NOXIA には既にある）、減価償却、棚卸評価、固定資産、年度締め、XTX。

---

## 13. 開発順序（フェーズ）と前提

| Phase | 内容 | 前提・注意 |
|---|---|---|
| 1 会計基盤 | 勘定科目・仕訳行・年度、帳簿ビュー、カード入金消込の基礎、事業主貸借（既存）、**現金取消の二重減算バグ修正（承認要）**、フィールド暗号化、非同期ジョブ | マイグレーションは開発 DB・検証 DB のみ |
| 2 領収書・承認 | タブレット撮影、AI（mock 既定）、手入力、承認・差戻し・却下、CashMovement/Expense への反映、重複検出、レジ出金との突合 | AI 本番接続は会社設定＋環境変数 |
| 3 銀行・スタッフ支払 | CSV 取込・照合、カード入金の自動作成、買掛、支払区分（一般業務委託費）、支払台帳 | 実際の銀行 CSV サンプル（匿名化）が必要 |
| 4 年間決算 | 棚卸評価、固定資産・減価償却、開業費、決算整理、年間損益（店舗別・合計） | 償却率表・評価方法の一次資料 |
| 5 入力ガイド | 年度設定、白色/青色別ガイド、金額集計、コピー、チェック、PDF、公式リンク | 作成コーナーの令和8年分画面の公開を待って項目名を確定（公開前は「要確認」表示） |
| 6 申告書類 | 収支内訳書・決算書・申告書 B の集計と税額計算、所得控除、版管理、PDF | 令和8年分の様式・税制の公式確認 |
| 7 XTX | 仕様取得、生成、スキーマ検証、公式ソフト取込試験、検証済み後に公開 | 仕様の公開時期に依存 |
| 8 総合テスト | 会計・税務・UI・権限・セキュリティ・出力・バックアップ復元 | 本番デプロイは承認後 |

Phase 1〜4 は年度・税制に依存しないため先行できる。Phase 5〜7 は国税庁の令和8年分資料の公開（例年 1 月上旬）に依存する部分があり、公開前は「設定の器」と「令和7年分での動作確認」までにとどめる。

---

## 14. 本番環境への影響

- 本番 DB・本番サーバーには Phase 0 で何も変更しない。新しいマイグレーションは開発 DB（`.local`）と検証 DB（`yorumiru_verify`）で往復確認する。
- 既存の POS・現金・日払い・源泉のテーブルは列追加のみ。既存 API の応答は項目追加のみ（削除・意味変更なし）。
- 影響しうる既存挙動: (1) 現金取消バグの修正で**締めの理論現金の数字が変わる**（承認要）。(2) 現金経費の仕訳が雑費一括→承認済み科目になる（月次損益の内訳が変わる。合計は不変）。(3) `GET /api/auth/me` の caps が増える（メニューの表示が増える）。
- リスク一覧は `tax_accounting_audit.md` §10。
