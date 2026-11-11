# NOXIA 会計・確定申告統合 実装状況（Phase 1〜8）

更新: 2026-11-11。設計は `tax_accounting_audit.md`（入口）と `tax_*.md` の各文書。この文書は「何が実装済みで、何が公式確認待ちか」を 1 枚にまとめたもの。

**本番環境には何も反映していない。** コードとマイグレーション、開発用 DB（`.local` の `yorumiru`）と検証用 DB（`yorumiru_verify`）への適用のみ。本番への適用（`migrate`・デプロイ）はユーザーの明示の承認後に行う。

---

## 1. フェーズ別の状態

| Phase | 内容 | 状態 | 主なコード |
|---|---|---|---|
| 0 調査・設計 | 既存調査、設計文書 11 本 | 完了 | `docs/tax_*.md` |
| 1 会計基盤 | 勘定科目 `Account`・仕訳行 `JournalLine`・年度 `FiscalYear`（締め／再開・乖離警告）、帳簿 9 種（総勘定元帳・現金出納帳・預金出納帳・売上帳・売掛帳・買掛帳・経費帳・スタッフ支払台帳・試算表）と CSV/XLSX、手入力・決算整理仕訳、フィールド暗号化、監査ログの追記専用化、DB キューの非同期ジョブ、cap 追加、再認証の「明示」区分、現金取消バグ修正、バックアップに media | 完了 | `apps/accounting/books.py` `ledgers.py` `exports.py` `uploads.py` `views_books.py`、`apps/core/crypto.py` `db_fields.py` `jobs.py` |
| 2 領収書・承認 | `expenses` アプリ: タブレットの撮影→読取→確認→支払方法→登録、AI 読取（mock 既定／Anthropic は opt-in・予算・縮小画像のみ）、承認・差戻し・却下・訂正・一括承認、重複検出（sha256・dHash・日付×金額×支払先）、店舗現金→`CashMovement`、その他→`Expense`／`Payable`、レジ出金との突合、店舗別の一覧 | 完了 | `apps/expenses/*`、`frontend/.../receipts/*`、`accounting/expenses` |
| 3 銀行・買掛 | `banking` アプリ: 口座、CSV 取込（文字コード・列対応の学習・プレビュー・確定）、照合候補と確定／解除、カード入金の自動作成、買掛金の支払 | 完了 | `apps/banking/*`、`accounting/bank` |
| 4 年間決算 | `closing` アプリ: 棚卸評価（最終仕入原価法）、固定資産・減価償却（定額法・少額・一括償却）、開業費、決算整理、年間損益、決算チェックリスト、年度締め | 完了（償却率表は目視確認待ち） | `apps/closing/*`、`accounting/assets` `accounting/yearend` |
| 5 入力ガイド | `tax` アプリの器（`IndividualTaxProfile`・`TaxYearConfiguration`・`TaxReturn`・`GuideProgress`）、白色／青色の作成コーナー転記ガイド（金額・コピー・チェック・進み具合・PDF） | 完了（項目名は「要確認」表示） | `apps/tax/guide.py` `data/guide_2026.json`、`accounting/tax-return/guide` |
| 6 申告書類 | 収支内訳書／青色申告決算書の集計、所得控除の入力、税額計算（令和8年分 JSON・純粋関数）、版管理（設定ハッシュ＋仕訳スナップショット）、未対応項目の検出で完成を止める、転記用 PDF・XLSX、監査ログのハッシュ連鎖 | 完了（税制値は公式確認待ちの項目あり） | `apps/tax/rules/engine.py` `compute.py` `statements.py` `exports/*`、`accounting/tax-return` |
| 7 XTX | 生成の枠組み（`fields.json` がある年分だけ生成）、`TAX_XTX_RELEASED_YEARS` で公開を制御。**令和8年分の仕様（確定版 XSD は 2026-11-25 掲載予定）が無いため生成不可・ボタンは押せない** | 枠のみ。**2026-11-12 不採用**（§7） | `apps/tax/exports/xtx.py` `data/xtx/README.md` |
| 8 総合テスト | 全テスト・型検査・ビルド・マイグレーション往復・E2E・文書 | 完了（結果は §3） | — |

## 2. 変更の一覧

### 2.1 マイグレーション（全部で 9 本。往復検証済み）

| アプリ | ファイル | 内容 |
|---|---|---|
| core | `0003_payout_deductions` | （以前から未適用だったもの）日払いの任意控除 |
| core | `0004_storesettings_manager_approve_limit_and_more` | 店舗設定（領収書撮影・店長承認・上限）、`CompanySettings`（AI）、`BackgroundJob` |
| accounting | `0003_account_fiscalyear_fiscalyearevent_journalline_and_more` | 勘定科目・年度・仕訳行・`Evidence`／`Expense` の列追加・freee トークン暗号化 |
| accounting | `0004_cardsettlement_bank_account_ref` | カード入金と銀行口座 |
| cash | `0004_cashmovement_account` | レジ出金に勘定科目 |
| expenses / banking / closing / tax | `0001_initial` | 新アプリ |
| staff | `0003`・`0004` | （以前から未適用だったもの） |

適用状況: 開発用 DB `yorumiru` と検証用 DB `yorumiru_verify` に適用済み。`makemigrations --check` 差分なし。スクラッチ DB で `migrate` → `tax/closing/banking/expenses zero` → `accounting 0002`（cash 0004 も巻き戻る）→ `core 0002` → `migrate` の往復が成功（`.local/migration-roundtrip.log`）。

### 2.2 既存機能の変更

- `cash.services.day_summary`: 同じ営業日の現金会計の取消で理論現金が二重に減る不具合を修正（受け取った現金 − 返金）。
- `accounting.journals`: 現金経費の科目を `CashMovement.account`／承認済み領収書の科目から取る。年度ロック・乖離記録・`JournalLine` 同期。仕入・経費の 8%／10% 分割。
- `expenses.services`: 領収書の承認・訂正、買掛金の支払い・取消の直後に、その日の `journals.build` を実行する（Phase 8 の E2E で「承認したのに経費帳に出ない」のを見つけて修正）。「仕訳を作る」を押さなくても帳簿に出る。日締め・銀行照合は従来どおり（締め時／照合時に生成）。
- `core.permissions`: cap 追加（`expenses.submit/view_store/approve`、`banking.view/import/reconcile`、`books.adjust`、`yearend.prepare/confirm`、`tax.return.view/edit/export`、`tax.config.manage`）。税務判断系はオーナー本人のみ、`tax.config.manage` は運営者のみ。再認証に `explicit`（ログイン時の自動付与では通らない）を導入。
- `core.AuditLog`: `save`／`delete` を拒否、管理画面は読み取り専用。`prune_audit_log` コマンド。
- `accounting.views._read_upload` → `uploads.inspect_upload`: マジックバイト判定・再デコード・HEIC→JPEG・dHash・画質フラグ。
- `FreeeConnection` のトークンは `EncryptedTextField`（平文の既存値も読める）。
- `scripts/backup-db.ps1`: `MEDIA_ROOT` も zip。`scripts/dev-jobs.ps1`（`run_jobs --loop`）追加、`start-all.ps1` から起動。
- 設定（`.env.example`）: `FIELD_ENCRYPTION_KEY_V1`（本番必須）、`RECEIPT_AI_PROVIDER`（既定 mock）、`ANTHROPIC_API_KEY`、`RECEIPT_AI_MODEL`、`TAX_XTX_RELEASED_YEARS`（既定 空）。
- `requirements.txt`: cryptography / filetype / Pillow / pillow-heif / pypdf / openpyxl / lxml / anthropic / charset-normalizer。

### 2.3 画面（Next.js）

- 営業（タブレット）: `/receipts/new`（撮影→確認→支払方法→登録の 4 ステップ、取り消す付き通知）、`/receipts`（自店舗の状態一覧）、`/receipts/[id]`。上部バーに「📷 領収書」（`expenses.submit`）。
- 経理（PC）: `/accounting/dashboard`、`/accounting/expenses`（承認・一括・レジ出金突合・買掛・AI 設定）、`/accounting/bank`、`/accounting/ledgers`（帳簿 9 種・CSV/XLSX）、`/accounting/assets`、`/accounting/yearend`、`/accounting/tax-return`（集計・入力・計算過程・版・出力）、`/accounting/tax-return/guide`、`/accounting/exports`、`/accounting/settings`（AI・申告情報のタブ追加）。
- 共通部品: `common.tsx`（Tabs/CopyButton/ReasonPicker/RequireCaps/Checklist/Wizard/MonthPicker/YearPicker/FieldErrors）、`DataTable`、`FileViewer`、`CameraCapture`、`PollingDialog`、`uploadFile`（進捗・Idempotency-Key）。
- `AppShell` の上部バー: 「📷 領収書」（`expenses.submit`）を追加。1280px 未満ではアイコンだけにして折り返さない。

## 3. テスト結果（2026-11-11）

| 種別 | 結果 |
|---|---|
| バックエンド `manage.py test apps` | **374 件**（2026-11-12 00:50 の実行で 372 OK。残り 2 件は `apps/staff` の既存テストで、深夜 0〜6 時だけ暦日と営業日のずれで落ちる。昼間は 374 件 OK。Phase 0 時点 267 件） |
| `makemigrations --check` | 差分なし |
| マイグレーション往復 | 成功（§2.1） |
| フロント `npm run typecheck` | エラーなし |
| フロント `npm run build` | 成功（37 ページ生成） |
| E2E `taxreturn.mjs`（PC） | 13/13 OK |
| E2E `receipts.mjs`（PC） | 15/15 OK |
| E2E 回帰 `pos.mjs`・`accounting.mjs`・`deductions.mjs` | 19/19・16/16・11/11 OK |
| E2E `freee-csv.mjs`（申告方法の設定・メニューの出し分け・freee 取込用 CSV） | 9/9 OK（2026-11-12） |

### 3.1 E2E・ビルドの結果

2026-11-11 23 時台、検証用 DB（`yorumiru_verify`、各スクリプトの前に `seed_dev --reset`）で実行。Phase 8 の統合で見つけて直したもの:

- 領収書を承認しても仕訳が作られず、経費帳に出なかった → 承認・訂正・買掛金の支払い／取消の直後に `journals.build(その日)` を実行（ユニットテスト追加）。
- 確定申告ページの初回表示で `/api/tax/returns/<year>` が同時に 2 回来ると `TaxReturn` の作成が一意制約で 500 → `IntegrityError` を受けて既存を返す。
- 上部バーに「📷 領収書」を足した分、iPad 横（1180px）で「卓一覧」などが折り返して 2 行になっていた → 折り返し禁止、1280px 未満は「📷」「☰」だけ表示、店舗名は省略表示。
- 会計設定の「AI 読取」タブが API のフィールド名（`enabled`・`budget_jpy`・`spent_jpy`）と合っていなかった → 修正。
- 既存の回帰 E2E `accounting.mjs` は会計設定ページの見出しが「会計設定」に変わったので期待値を更新。`pos.mjs` は 2 卓目の入店で遷移を待つ 1 行を追加（卓一覧の 1 卓カードを誤って押していた）。

E2E の実行には `scripts\verify-env.ps1` を追加した（§3 末尾の手順）。

E2E の実行方法: `scripts\verify-env.ps1 -Reset`（検証用 DB `yorumiru_verify` を作り直して migrate・seed_dev・seed_tax_config → Django 8001・ジョブ・Next 3001 を起動）のあと、`.local\e2e` で `$env:BASE_URL="http://localhost:3001"; node <script>.mjs` を実行する。各スクリプトは seed 直後の状態を前提にするので、別のスクリプトを続けて動かすときは `seed_dev --reset`（`POSTGRES_DB=yorumiru_verify`）を挟む。終わったら `verify-env.ps1 -Stop`。スクリーンショットは `.local/e2e/screenshots/<script>/`。

## 4. 公式確認が済むまで「要確認」のままにしているもの

| # | 事項 | いまの扱い | 確認したら直す場所 |
|---|---|---|---|
| 1 | 令和8年分の税制値（速算表・基礎控除の区分・各控除の上限・青色 10 万円控除の要件） | `apps/tax/data/year_2026.json` は `draft`。画面に「公式確認中の項目あり」 | JSON を更新し `seed_tax_config` → 設定ハッシュが変わるので再計算が促される |
| 2 | 収支内訳書・青色申告決算書・確定申告書の令和8年分様式（行名・欄番号） | 令和5年分以降用の行で `statement_line` を定義 | `apps/accounting/books.py STATEMENT_LINE_LABELS`、`apps/tax/statements.py` |
| 3 | 作成コーナー令和8年分の入力順序・項目名 | `guide_2026.json` の `official_label` は null → 画面は「要確認」 | `official_label` と `verified` を埋める |
| 4 | XTX の令和8年分仕様（XSD・帳票間連動） | **不採用（2026-11-12）。** 電子申告は各オーナーの freee で行うため作らない。枠（`apps/tax/exports/xtx.py`）は残すが着手しない | — |
| 5 | 減価償却の償却率表 | `apps/closing/data/depreciation_rates.json` は 1÷耐用年数で生成。国税庁の表と目視で突き合わせる | JSON を差し替え |
| 6 | 公式様式 PDF への重ね刷りの可否 | 転記用（独自レイアウト）PDF のみ | `apps/tax/exports/pdf.py` |
| 7 | 消費税（課税事業者の場合） | 未対応として検出し、完成を止める | `apps/tax/compute.py detect_unsupported` |

## 5. 既知の制限

- 所得税の確定申告支援は **個人事業主（白色・青色 10 万円控除）** のみ。法人税・消費税の申告、青色 55／65 万円控除（複式簿記の貸借対照表）は対象外として検出・表示する。
- AI 読取は補助。金額・科目・税区分は必ず人が確認して承認する。Anthropic への送信は縮小した画像だけで、店舗名・氏名・ファイル名は送らない。
- マイナンバーは収集・保存しない（入力欄もない。12 桁の数字は入力検証で弾く）。
- e-Tax への送信・作成コーナーへの自動入力は行わない（転記ガイドと PDF/XLSX のみ）。
- 実機 iPad Safari のカメラ撮影は未検証（Chromium でファイル選択の経路のみ確認）。

## 6. 本番に反映するときの手順（承認後）

1. `scripts/backup-db.ps1` で DB と media を取る。
2. `.env` に `FIELD_ENCRYPTION_KEY_V1`（32 バイトの base64）を設定。未設定だと本番（`DEBUG=0`）は起動時に失敗する（fail-closed）。
3. `pip install -r requirements.txt`、`manage.py migrate`、`manage.py seed_tax_config`。
4. ジョブ実行を常駐させる（systemd か cron で `manage.py run_jobs`）。
5. 会社設定で AI 読取をオンにする場合だけ `RECEIPT_AI_PROVIDER=anthropic` と `ANTHROPIC_API_KEY`。
6. XTX は `TAX_XTX_RELEASED_YEARS` を空のままにする（取込試験の合格まで）。

## 7. 2026-11-12 の方針変更: 申告は freee 経由、NOXIA は仕訳連携まで

ユーザーの決定（Obsidian `01_Projects/NOXIA/decisions.md` 2026-11-12）。

- 確定申告（決算書・申告書の作成、電子申告）は各オーナーが自分の freee アカウントで行う。NOXIA は店舗で起きること（売上・現金・日払い・領収書の撮影と承認・レジ出金との突合・スタッフ支払と源泉）までを担い、承認済みの仕訳を freee に渡すところまでが責任範囲。
- NOXIA 独自の e-Tax 送信（電子署名・送受信）と XTX 生成は作らない。銀行口座連携・固定資産・棚卸・決算書・申告書・税理士との共有は freee 側。
- 確定申告画面と作成コーナー転記ガイドは、freee を契約しないオーナー向けの無料の道として残す。freee 利用者にどう見せるか（出さない／参考表示）は未決定。
- 次にやること: freee API の利用条件（運営者が複数の顧客事業所につなぐ形。アプリストア公開・審査の要否。`TAX_OPEN_QUESTIONS.md` B-20）と料金プランの確認、Phase 1 以降に増えた勘定科目（領収書・買掛・銀行・決算整理）の freee 科目対応（`AccountMapping`）の点検、1 事業者での本番接続試験（`FREEE_LIVE_ENABLED`）、freee に渡す仕訳の範囲と期首残高の扱いの決定。
- §4 の公式確認待ちのうち、XTX（#4）は不要。税制値・様式・作成コーナー項目名（#1〜#3）は転記ガイドを残す場合にだけ確認する。

### 7.1 2026-11-12 実装: 申告方法の設定と freee 取込用 CSV

- `TaxPayer.filing_method`（未設定／freee／作成コーナー転記。会計設定の事業者フォーム。マイグレーション accounting 0005）。`/api/auth/me` の `stores[].taxpayer.filing_method` で画面に渡す。
- 申告方法が **freee** の事業者では、メニューの「銀行 CSV 取込・入出金照合」「固定資産・棚卸」「年間決算」「確定申告」「国税庁 入力ガイド」を出さない（`AppShell.tsx` の `freeeHandles`）。URL を直接開いたときは「freee で申告の設定です」の案内を出して参考表示にする（`FreeeHandlesNotice`）。
- **freee 取込用 CSV**（`GET /api/accounting/freee.csv?from&to&only_new=1&encoding=sjis&mark=1`）: freee の「取引入力 → 振替伝票 → インポート → 他社会計ソフトインポート」向け。列は 伝票番号・取引日（yyyy/mm/dd）・借方勘定科目・借方税区分・借方金額・貸方勘定科目・貸方税区分・貸方金額・摘要・NOXIA仕訳ID。借方と貸方の行数が違う仕訳は同じ伝票番号で複数行。税区分名は freee の名前（課税売上10%・課税売上8%（軽）・課対仕入10%・課対仕入8%（軽）・非課売上／非課仕入・対象外）。金額は税込で税額は freee 側で計算させる。Shift-JIS（Windows 向け）と UTF-8 BOM を選べる。
- 出力した仕訳には `freee_csv_exported_at` と出力時の `revision` の印を付け、`only_new=1` は「未出力」または「出力後に revision が進んだ」仕訳だけを返す（二重取込の防止）。印を付けるには `accounting.freee` 権限が要る（`mark=0` で印なし出力）。状況は `GET /api/accounting/freee/csv-status`。
- 画面: `/accounting/freee` の「freee 取込用 CSV（振替伝票）」カード（月・未出力だけ・Shift-JIS・件数・最後の出力日時・freee での列の割り当て手順）。
- freee の取込画面の列名・税区分名は公式ヘルプが自動取得を拒否するため（HTTP 403）、一般に公開されている解説と既存実装に基づく。**初回は freee の取込画面で列と税区分の対応付けを目で確認する**（`tax_user_manual.md` §17）。
- テスト: `apps/accounting/tests/test_freee_csv.py` 5 件（列・複合仕訳・税区分名・未出力と印・Shift-JIS・申告方法の保存とセッション・権限）。E2E `.local/e2e/freee-csv.mjs` 9/9 OK。typecheck・本番ビルド OK。
- 深夜 0〜6 時は暦日と営業日がずれる（営業日の切替 06:00）ため、`apps/expenses/tests/test_receipts.py` の「今日」を営業日基準に直した。`apps/staff` の既存テスト 2 件（`test_backs.test_effective_dates`・`test_payroll.test_closed_period_snapshot_unaffected_by_later_wage_change`）も同じ理由で深夜だけ落ちる（未修正。昼間は通る）。
