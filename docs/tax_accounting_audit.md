# NOXIA 会計・確定申告統合 Phase 0 調査報告

作成: 2026-11-11。対象: `C:\Users\hsuga\yorumiru`（NOXIA、Django 5.2 + DRF / Next.js 15 / PostgreSQL 17）。
参照した兄弟プロジェクト: ミセミル（`C:\Users\hsuga\survey_project`、VPS 本番と同じソース）、TaxFlow（`C:\Users\hsuga\taxflow`）。
**この段階では本番データ・本番環境・開発用 DB に一切変更を加えていない**（コードの読み取りと `docs/` への文書追加のみ）。

関連文書（今回作成）: `tax_architecture.md`、`tax_database_design.md`、`tax_api_design.md`、`tax_receipt_workflow.md`、`tax_security.md`、`tax_test_plan.md`、`tax_user_manual.md`（骨子）、`tax_calculation_rules.md`、`tax_etax_input_guide.md`、`tax_etax_export.md`。

---

## 0. 要約

- NOXIA には **POS・現金・日払い・ホステス報酬の源泉徴収・証憑・経費・簡易な仕訳（JSON 行）・freee 連携** がすでにあり、売上と現金の元データは会計に十分な粒度で揃っている。
- 足りないのは **複式簿記の基盤（勘定科目・仕訳行・年度）、領収書の撮影→AI 読取→承認フロー、銀行 CSV 取込と照合、買掛、棚卸評価・固定資産・減価償却、年度決算、所得税の計算と申告書類、国税庁入力ガイド、XLSX/XTX 出力、暗号化、非同期ジョブ** である。
- ミセミルに「確定申告がほぼ実装済み」というものは **無い**（確定申告書・収支内訳書・青色申告決算書・減価償却・棚卸・事業主貸借・XTX はミセミルにも無い）。ただし **流用価値が高い部品** がある: 年末調整の計算エンジン（所得控除・速算表・端数処理、純粋関数）と令和7〜9年分のパラメータ JSON、領収書 AI 読取（Claude strict tool-use・2 段構え）、カード明細の解析と照合、公式様式 PDF への重ね刷り、フィールド暗号化・追記専用の監査ログ・非公開ファイル配信。TaxFlow からは AI プロバイダ抽象化・プロンプトとスキーマ・予算管理・dHash 重複検出・CSV 列対応の学習・照合・電帳法チェックリストが流用できる。
- 既存コードの要修正点として、**現金会計の取消で理論現金が二重に減るバグ**（締めの数字が変わるため承認要）、freee/Square トークンの平文保存、監査ログが管理画面から編集可、アップロード種別のクライアント申告、カード手数料の税区分固定などを確認した。
- 開発は 8 フェーズ。Phase 1〜4（会計基盤・領収書・銀行・決算）は税制に依存せず先行できる。Phase 5〜7（入力ガイド・申告書類・XTX）は国税庁の **令和8年分** 資料の公開（例年 1 月上旬）に依存する部分があり、公開前は「年度設定の器」と「令和7年分での検証」までにとどめる。

---

## 1. 既存機能で**そのまま利用できる**もの

| 領域 | 既存の実装 | 新機能での使い方 |
|---|---|---|
| テナント・権限 | `Company`→`Store`、`X-Store-Id` の所属照合、ケイパビリティ 49 個、再認証、`AccountingAccessGrant`（記帳代行・税理士等）、`OperatorAccessLog` | そのまま。新 cap を追加するだけ（`tax_api_design.md` §2） |
| 事業者 | `accounting.TaxPayer`（法人／個人事業主、納税地、所轄税務署、整理番号、決算月）、`StoreTaxProfile` | 納税者＝TaxPayer。個人事業主の申告情報は `tax.IndividualTaxProfile` を 1 対 1 で追加 |
| 売上の元データ | `Checkout`（税率別内訳 `tax_breakdown`・端数・サービス料のスナップショット）、`Payment`（現金／カード／ツケ）、`Refund`、`Receivable`/`ReceivableCollection` | 売上帳・売掛帳・日次仕訳の元。変更不要 |
| 現金の元データ | `BusinessDay`（締めスナップショット・理論現金・差額）、`CashMovement`（経費・日払い・その他・途中回収・釣銭補充）、`BankDeposit`、`ClosingRevision` | 現金出納帳・レジ出金と領収書の突合。`CashMovement` に科目 FK を追加するだけ |
| スタッフ支払 | `EmployeePayment`（手渡し額・源泉・控除・内訳）、`WithholdingRecord`（計算根拠・ルール版）、`DeductionUsage`、`SalaryWithholdingEntry`、`WithholdingAdjustment`、`WithholdingMonth`（スナップショット・納期限）、`TaxRemittance` | スタッフ支払台帳・源泉徴収管理はほぼ完成。支払区分「一般業務委託費」の追加のみ |
| 仕訳 | `Journal`（`sync_key` 一意・`content_hash`・revision・`is_void`）、`journals.build`／`daily_store_lines`（売上・返金・ツケ・現金経費・日払い・現金過不足・銀行入金・納付・経費・資金移動・カード入金） | **仕訳ヘッダと生成ロジックは再利用**。行を `JournalLine` に正規化して帳簿の元にする |
| 事業主貸借 | `OwnerFundTransfer`（投入＝事業主借／引出＝事業主貸、法人は役員借入金） | そのまま（仕様 §5-4 を満たす） |
| 証憑・経費 | `Evidence`（sha256・取引日・金額・取引先・保存期限・論理削除）、`Expense`（科目キー・税率・登録番号・支払元・訂正履歴）、`CardSettlement`（売上・手数料・入金） | 承認済み領収書の着地点。列追加のみ |
| freee 連携 | OAuth・模擬／本番・二重送信防止・科目対応 | そのまま（任意機能） |
| 整合性の部品 | `Idempotency-Key`（`@idempotent`）、`select_for_update` の行ロック、`AuditLog`、`money.D`、Decimal 徹底、`business_date_for` | 新 API で同じ部品を使う |
| 出力 | `analytics.ExportView`（CSV・BOM・監査）、`accounting/pdf.py`（reportlab・内蔵 CID フォント・A4） | 帳簿 CSV と転記用 PDF の基盤 |
| 在庫・仕入 | `Supplier`、`SupplierProduct`（仕入単価）、`StockTake`/`StockTakeLine`（数量）、`PurchaseOrder`/`GoodsReceipt`、`Product.cost_price` | 期末棚卸の下書き・買掛金の仕入先。評価額の計算は新規 |
| フロント基盤 | `api()`（CSRF・Idempotency・エラー形式）、`withReauth`、`useLoad`、`Modal`/`ConfirmDialog`/`NumberPad`/`Toast`（取り消す通知）/`Stat`/`Field`、`TerminalWaitDialog`（ポーリング）、`CloseWizard`（ステップ式）、`AppShell` の caps によるメニュー | 新画面の部品。カメラ・プレビュー等は新規 |
| 開発・検証環境 | ポータブル PostgreSQL、`seed_dev`、267 件のテスト、Playwright E2E、検証 DB 手順 | 同じ手順で各フェーズを検証 |

## 2. **改修が必要な**既存機能

| # | 既存機能 | 改修内容 | 理由 | フェーズ |
|---|---|---|---|---|
| 1 | `cash.services.day_summary` | 同じ営業日に現金会計を取り消すと理論現金が二重に減る（`Payment` を refunded で売上から外し、さらに `Refund` も引く）。「その日に受け取った現金（取消済み含む）− 返金」に修正 | 現金出納帳・現金過不足の仕訳の元になる。**締めの数字が変わるため承認を得てから修正** | 1 |
| 2 | `accounting.Journal` | `JournalLine` テーブルと勘定科目 FK を追加し、`lines` JSON は freee 用の派生に。`fiscal_year`・`entry_kind`・`status`・ロックを追加 | 帳簿・決算・申告の集計に正規化構造が必要（仕様 §15 JournalEntry/JournalLine） | 1 |
| 3 | `journals.daily_store_lines` | 現金経費を「雑費・課税仕入 10% 固定」から `CashMovement.account` の科目へ。カード手数料の税区分を固定「課税仕入 10%」から設定値へ（一般に非課税の信用供与。要確認）。`_rate_tax` が 8% 以外をすべて 10% にする点（サービス料 0% グループ）を税区分「対象外」に分ける | 科目別の経費帳・収支内訳書の行、消費税区分の誤り防止 | 1 |
| 4 | `journals.build` | 締めた年度を飛ばす、再開した営業日の古い仕訳を stale 警告、`StoreTaxProfile` 付け替え時の sync_key 二重化を検出、同時実行の IntegrityError を 409 に | 年度ロックと二重計上防止 | 1 |
| 5 | `CashMovement` | `account` FK、承認済み領収書との 1 対 1（`ExpenseReceipt.cash_movement`）。締め画面の「現金経費」から領収書撮影へ導線 | 店舗現金の経費を 1 本の経路に（CashMovement と Expense(office_cash) の二重登録を防ぐ） | 1–2 |
| 6 | `Evidence` | `origin`（電子取引／紙）・`phash`・`captured_at`・画質・ページ数。`GET …/file?inline=1`。一覧のページング（現状 500 件打ち切り） | 電帳法の区別・重複検出・プレビュー・検索要件 | 2 |
| 7 | `Expense` | `account` FK・`tax_breakdown`（8%/10% 混在）・`bank_transaction` FK | 領収書の内訳と銀行照合 | 1–3 |
| 8 | `CardSettlement` | `bank_transaction` FK・対象期間 | 銀行明細からの自動作成と未入金残高 | 3 |
| 9 | `TaxCategory` | `outsourcing_fee`（一般業務委託費） | 仕様 §8 の支払区分 3 種 | 3 |
| 10 | `core.permissions` | cap 追加（expenses.*, banking.*, books.adjust, yearend.*, tax.return.*, tax.config.manage）。店長の承認は店舗設定で動的付与。税務系の再認証はログイン時の自動付与を使わない | 仕様 §4 | 1–2 |
| 11 | `core.AuditLog` | save/delete の拒否、admin 読み取り専用、meta のフィルタ、保存期間 | 監査可能性（現状は管理画面から編集・削除できる） | 1 |
| 12 | `FreeeConnection`・`TerminalConfig` | トークンの暗号化（ミセミル `core/crypto.py` 移植） | 機密データの暗号化（仕様 §16） | 1 |
| 13 | `accounting/views._read_upload` | マジックバイト判定・画像の再デコード・HEIC 変換・EXIF 除去 | 安全なファイルアップロード | 2 |
| 14 | `frontend/src/lib/api.ts uploadFile` | Idempotency-Key・進捗・中断。`ApiError.details` の表示 | 二重登録防止・入力ミスの表示 | 2 |
| 15 | `AppShell.tsx` NAV | 「経理」グループを 13 項目に再編、上部バーに「領収書」 | 仕様 §14 | 2〜 |
| 16 | `scripts/backup-db.ps1` | `MEDIA_ROOT` を含める。復元検証コマンド | 原本の保全（現状は DB のみ） | 1 |
| 17 | `TaxRemittance`（レジ出金） | 出金店舗が事業者の店舗か検証 | テナント分離の穴 | 1 |
| 18 | `analytics` と帳簿の差 | ダッシュボードは取消済み会計を除外、仕訳は「元の日に売上・取消日に返金」。差の説明を照合画面に出す | 数字の不一致の問い合わせ防止 | 1 |

## 3. **新規追加する**機能

| アプリ | 機能 | 主なモデル（`tax_database_design.md`） |
|---|---|---|
| `accounting`（拡張） | 勘定科目マスタ（個人事業主向け標準科目を seed、収支内訳書・決算書の行に対応付け）、仕訳行、会計年度と締め、手入力・決算整理仕訳、帳簿ビュー（売上・現金・預金・売掛・買掛・仕入・経費・固定資産台帳・スタッフ支払台帳）と CSV/XLSX | Account, JournalLine, FiscalYear, FiscalYearEvent |
| `expenses`（新） | タブレットの領収書撮影→アップロード→AI 読取（mock/anthropic）→スタッフ確認→支払方法→登録→管理者承認・差戻し・却下・訂正、重複検出（sha256・dHash・日付金額取引先）、レジ出金との突合、買掛金・未払金と支払い、AI 設定（有効化・予算） | ExpenseReceipt, ExpenseReceiptEvent, Payable, PayablePayment |
| `banking`（新） | 銀行口座、CSV 取込（文字コード・列対応プロファイル・和暦/金額正規化・重複・残高連続性）、明細、照合候補（金額一致・手数料差・合算・名寄せ）、確定・解除・対象外・振替、カード入金の自動作成 | BankAccount, BankImportProfile, BankStatementImport, BankTransaction, BankReconciliation |
| `closing`（新） | 期首・期末棚卸（StockTake から下書き、最終仕入原価法）、固定資産・開業費（定額法・一括償却・少額・30 万円特例（青色）・任意償却）、年度ごとの償却、決算整理（未払・未収・前払・家事按分・貸倒）、年間損益（店舗別・事業者合計）、決算チェックリスト | InventoryClosing(+Line), FixedAsset, DepreciationEntry, ClosingAdjustment |
| `tax`（新） | 個人事業主の申告情報、年度別の税制・様式・ガイド設定、申告書データ（事業所得の集計＋入力）、所得控除・税額計算（計算過程を保存）、版管理、未対応ケースの検出、国税庁入力ガイド（白色／青色、コピー、チェック、進捗、PDF、公式リンク）、出力（収支内訳書・青色申告決算書・確定申告書の転記用 PDF、XLSX、XTX（検証完了まで非公開））、申告関連監査ログ | IndividualTaxProfile, TaxYearConfiguration, TaxReturn, TaxReturnVersion, TaxExport, GuideProgress, TaxAuditLog |
| `core`（拡張） | 会社設定（AI 有効化・予算）、店舗設定（領収書ボタン・店長承認）、フィールド暗号化、非同期ジョブ（DB キュー＋`run_jobs`）、追記専用監査ログ | CompanySettings, BackgroundJob |
| フロント | タブレット: `/receipts/new`・`/receipts`、上部バー「領収書」、締め画面の導線。PC: 会計ダッシュボード／経費・領収書／銀行取込／照合／帳簿／売掛・買掛／スタッフ支払／固定資産・棚卸／年間決算／確定申告／入力ガイド／ファイル出力／会計設定。共通部品: CameraCapture, PollingDialog, Wizard, Tabs, CopyButton, Checklist, CsvColumnMapper, DataTable, FileViewer, ReasonPicker, RequireCaps | — |

## 4. DB 構造の変更案（要約）

詳細は `tax_database_design.md`。要点:

- 既存テーブルは **列追加のみ**（Journal・Expense・Evidence・CardSettlement・AccountMapping・CashMovement・StoreSettings・Company）。既存 API の応答は項目追加のみ。
- 新テーブルは 5 アプリに約 25 個。仕様 §15 の概念モデルとの対応表は同文書 §5（EXISTS: Receivable・StaffPaymentTaxRecord・TaxpayerProfile（基盤）／PARTIAL: AccountingTransaction・JournalEntry・JournalLine・ExpenseReceipt・TaxExport・TaxAuditLog／MISSING: ExpenseApproval・Bank*・Payable・FixedAsset・InventoryClosing・TaxYearConfiguration・TaxReturn・TaxReturnVersion）。
- 二重計上防止マトリクス（同文書 §7）: 元データごとに仕訳の経路を 1 本に固定。銀行明細は「照合するだけ」で売上・経費にしない。
- マイグレーション順（同文書 §6）: accounting 0003/0004 → cash 0004 → core 0004 → expenses 0001 → banking 0001 → closing 0001 → tax 0001/0002。本番 DB には適用しない。
- 既存の未適用マイグレーション（core 0003・staff 0003/0004・accounting 0001/0002）の状態は、今回 DB に接続できなかったため **未確認**（`showmigrations` がタイムアウト）。`scripts/dev-backend.ps1` は起動時に migrate するため、起動済みなら適用されている可能性がある。Phase 1 の最初に確認する。

## 5. 店舗タブレットと管理者 PC の画面構成

同じアプリで **ログインユーザーの caps** により表示を切り替える（端末種別では判定しない）。詳細は `tax_architecture.md` §4・§6。

**店舗タブレット**（スタッフ・店長）: 既存 POS はそのまま。追加は上部バー「領収書」（`AppShell.tsx:117-129` の「卓一覧」「出勤・お渡し」の隣）、締め画面「現金の入出金」カードの「📷 領収書から登録」、`/receipts/new`（撮影→確認→支払方法→登録の 4 ステップ、`btn-xl`・大きな金額・自動で戻る・取り消す通知）、`/receipts`（自店舗の状態一覧。科目・税務情報は API が返さない）。

**管理者 PC**（オーナー・会社管理者・経理担当・許可された店長）: メニュー「経理」を仕様 §14 の 13 項目に再編（`tax_architecture.md` §4.2 の表）。既存 `/accounting`（源泉管理）は `/accounting/withholding` に移し、`/accounting` を会計ダッシュボードにする。一覧・承認・照合は `DataTable`（ページ送り・複数選択・一括操作・キーボード）で効率化。

フロント調査で判明した不足（`tax_architecture.md` §6）: カメラ撮影（`capture` 属性）・画像プレビュー・縮小・PDF ビューア・CSV 取込と列対応 UI・コピー部品・チェックリスト・ウィザード／タブの共通部品・ページ送り・複数選択・A4 印刷 CSS・項目別エラー表示・ページ単位の権限ガードは **すべて未実装**。TaxFlow の `intake/page.tsx`・`DraftEditor.tsx`・`DocumentViewer.tsx`・`reconcile/page.tsx` を UI の型として流用する。

## 6. 権限管理の実装方針

- 既存のケイパビリティ方式を踏襲し、**役割名ではなく機能ごとの cap** を追加（`tax_api_design.md` §2 の表）。すべての新 API に `StoreCaps` を付け、画面は `can()` で出し分けるだけ（認可は API）。
- 税務判断（`tax.decide`・`yearend.confirm`・`tax.return.edit/export`）は **オーナー本人のみ**。会社管理者・記帳代行・税理士等・運営者には与えない（税理士法 52 条への配慮、既存方針）。税理士等は閲覧とコメント。
- スタッフ: `expenses.submit`・`expenses.view_store`（自店舗・自分の登録のみ。シリアライザで税務項目を落とす）。店長: 同＋店舗設定で許可した場合だけ `expenses.approve`（自店舗）。記帳代行: 承認・銀行・帳簿・決算準備まで。
- 再認証: 申告関連はログイン直後の自動付与を使わず、明示の再認証のみ。
- オブジェクト単位: 対象を `taxpayer`/`store` で絞り、他事業者は 404。納税者単位の分離を `TaxpayerScopedQuerysetMixin` で統一。
- 現状の穴（納付記録の店舗検証、監査ログの可変性、ログイン直後の再認証）は Phase 1 で修正（`tax_security.md` §10）。

## 7. 会計・税務計算の設計

### 7.1 帳簿エンジン
- 元データ → `Journal`/`JournalLine`（唯一の帳簿）→ 帳簿ビュー → 決算整理 → 年間損益 → 申告。帳簿は仕訳行の絞り込み（別テーブルを持たない）。
- 税込経理（現状どおり）。税区分（課税売上 10/8、課税仕入 10/8、非課税、対象外）を行に持ち、消費税額は参考値。2026 年分は免税事業者を想定し、課税事業者は「未対応」として申告書を完成にしない（インボイス登録済みで課税の個人事業主は税理士へ）。
- カード売上は売上日に売掛金、入金日に預金＋手数料で消込（仕様 §5-2 の例を満たす）。二重売上計上はマトリクスとテストで禁止。
- 現金: 既存の締めと連携。現金経費は `CashMovement` 1 件に領収書を紐付け、別に `Expense` を作らない。
- 事業主貸借: 既存どおり売上・経費にしない。

### 7.2 税額計算（Phase 6）
- **年度別の設定データ** `TaxYearConfiguration(2026)` に速算表・基礎控除（所得区分別）・配偶者（特別）控除表・扶養控除・特定親族特別控除・生命保険料／地震保険料控除・医療費・寄附金・社会保険料・小規模企業共済・青色申告特別控除（10 万円の要件）・復興特別所得税（2.1%）・端数処理を持つ。コードに金額を書かない。
- **エンジンはミセミル `payroll/services/year_end_calc.py`（純粋関数）を移植**し、確定申告向けに拡張: 事業所得（収支内訳書／決算書の集計）と他の所得の受け口、給与所得控除の上限撤廃、青色申告特別控除（所得を上限、要件判定）、医療費・寄附金・雑損、所得金額調整控除の年金型、**復興特別所得税を別行で 1 円未満切捨て→合計を 100 円未満切捨て**（年末調整の「×1.021→100 円切捨て」とは段取りが違う）、予定納税、純損失の繰越（青色）。
- **ミセミルの令和8年分パラメータ（`year_end_2026.json`、出典「令和8年分 年末調整のしかた」）は有力な初期値**だが、`tax_calculation_rules.md` の一次資料照合を経てから `verified` にする。令和8年分の基礎控除は令和7年度改正の時限加算を含む（所得 4,890,000 以下 1,040,000 など）と記録されている。
- 計算過程（各段の値・根拠キー・端数処理）を `computed` に残し、版（`TaxReturnVersion`）に設定ハッシュと仕訳ハッシュを保存。未対応の所得・控除・ケースは `unsupported_items` に入れ、**完成扱いにしない**（仕様 §11）。
- 防衛特別所得税は令和9年分（2027）から。2026 年分は非該当。定額減税は令和8年分に無し（ミセミル資料と一致、要公式確認）。

### 7.3 決算（Phase 4）
- 棚卸: 最終仕入原価法（届出なしの個人事業主の法定評価方法）を既定。既存 `StockTake` の数量×単価（`SupplierProduct.purchase_price`／`Product.cost_price`）で下書き。
- 減価償却: 国税庁の償却率表を `apps/closing/data/depreciation_rates.json` に転記（出典・確認日付き）。定額法を既定、定率法は届出が要る旨を表示。一括償却資産（20 万円未満・3 年）、10 万円未満の即時経費、30 万円未満の特例（青色のみ・年 300 万円）、開業費は任意償却。月割は取得月を含む。
- 複数店舗: `JournalLine.store` で店舗別損益、申告は事業者単位 1 件。

### 7.4 AI の位置づけ
領収書の読取補助に限定。出力は自動確定せず人が確認。勘定科目は提案のみ。税額計算に AI を使わない（`tax_receipt_workflow.md` §4）。

## 8. 国税庁の最新仕様で確認が必要な事項

公式資料での確認結果と未確認事項の一覧は `tax_calculation_rules.md`（税制・様式）、`tax_etax_input_guide.md`（作成コーナーの入力順序・項目名）、`tax_etax_export.md`（XTX 仕様）に分けて記載した。

**2026-11-11 時点で公式に確認できたこと**（出典 URL は各文書）:
- 令和8年度税制改正により、令和8年分の基礎控除は合計所得 489 万円以下 **104 万円**・655 万円以下 **67 万円**・2,350 万円以下 **62 万円**（令和8年12月1日施行、令和8年分以後適用）。給与所得控除の最低保障 74 万円、配偶者・扶養親族の所得要件 62 万円以下。ミセミルの令和8年分 JSON と一致。
- 復興特別所得税は令和8年分 2.1%。防衛特別所得税は **令和9年分から**。定額減税は令和6年分のみ。
- 令和8年分の確定申告書（案）第一表・第二表は公表済み。**収支内訳書・青色申告決算書は「令和5年分以降用」が現行で、令和8年分の改訂有無と手引きは未公表**。
- 作成コーナー令和8年分の公開日は未発表（e-Tax 受付開始は 2027-01-04 予定）。**作成コーナーは他社ソフトの .xtx を取り込めない**（.data のみ）ため、XTX は e-Tax ソフト（WEB 版／DL 版）経由となり、作成コーナー利用者には入力ガイド（転記）が唯一の経路。
- e-Tax 仕様（所得税 XML 構造設計書）はドラフトが 2026-10-30 公開、確定版と XSD は 2026-11-25 掲載予定。
- 税理士法: 国税庁 Q&A 問 6-3「非税理士が申告書等の作成ソフトを開発又は販売することは税理士業務に該当しない」。個別の課税標準計算に関する相談への回答は税務相談に該当しうる。
- 消費税の 2 割特例は令和8年分が最終適用年。令和9・10年分は 3 割特例。

要点:

| # | 事項 | 現状の扱い | 確認先 |
|---|---|---|---|
| 1 | 令和8年分の所得税の計算要素（速算表、基礎控除の区分と額、配偶者・扶養・特定親族特別控除、生保・地震・医療費・寄附金の上限、青色 10 万円控除の要件、復興特別所得税 2.1%、定額減税なし、防衛特別所得税の開始年分） | ミセミルの令和8年分 JSON と既存 `TAX_OPEN_QUESTIONS.md` を初期値。`TaxYearConfiguration` は `draft` | 国税庁「令和8年分 所得税の確定申告の手引き」「年末調整のしかた」、e-Gov 法令検索（所得税法・措置法・改正法） |
| 2 | 令和8年分の様式（収支内訳書 一般用、青色申告決算書 一般用、確定申告書 第一表・第二表）の公開時期と行名・欄番号 | 令和7年分の様式で行定義を作り、令和8年分の公開後に差し替え | 国税庁「確定申告書等の様式・手引き」 |
| 3 | 確定申告書等作成コーナー（令和8年分）の公開日、事業所得（決算書・収支内訳書）の入力順序と項目名、保存データ（.data）の仕組み、XTX 取込の可否 | ガイド項目は `official_label=null`・`verified=false` で「要確認」表示。公開後に実画面で確定 | https://www.keisan.nta.go.jp/ とその操作マニュアル |
| 4 | XTX（e-Tax 申告データ）の XML 構造設計書・スキーマ・帳票間連動仕様の令和8年分版、第三者ソフトのデータを作成コーナー／e-Tax ソフトに取り込む条件、利用規約 | 実装しない（Phase 7 で仕様取得→生成→検証→公式ソフト取込試験→公開） | e-Tax「仕様公開」ページ |
| 5 | 公式様式 PDF への重ね刷り（配布 PDF の利用可否） | 確認が済むまで「転記用（独自レイアウト）」PDF | 国税庁サイトの利用規約 |
| 6 | 青色申告承認申請の期限（開業 2 か月以内／3/15）と未承認時の扱い | 警告表示のみ | タックスアンサー |
| 7 | 電子帳簿保存法（電子取引データ保存の真実性・可視性、スキャナ保存の 200dpi・カラー・入力期間、検索要件の緩和条件） | 要件ごとに対応状況を表示。「完全対応」と表示しない。事務処理規程のひな形提供 | 国税庁 電帳法ページ・一問一答 |
| 8 | 消費税（免税判定・インボイス・2 割特例の期間） | 2026 年分は免税を想定。課税なら未対応 | 国税庁 インボイス制度ページ |
| 9 | 源泉徴収（令和8年分税額表、ホステス報酬 No.2807、支払調書の提出範囲 50 万円超） | 既存の実装と `TAX_OPEN_QUESTIONS.md` のとおり | 国税庁 |
| 10 | 税理士法 52 条と自計化支援ソフトの範囲（運営者・記帳代行ができること） | 既存方針（記帳・照合・出力まで。税務判断・申告書作成・e-Tax 送信は本人） | 税理士法・日税連の指針・弁護士 |
| 11 | 減価償却資産の償却率表・耐用年数表（令和8年分）、少額・一括償却・30 万円特例の要件 | データ化して出典を付ける | 国税庁 |
| 12 | 棚卸資産の評価方法（届出なしは最終仕入原価法） | 既定に採用 | 国税庁 |
| 13 | 既存 `TAX_OPEN_QUESTIONS.md` B-1〜21（源泉の控除額の当て方、雇用か業務委託かの判定、消費税の扱い、支払調書と個人番号、記帳代行の業務範囲、電帳法、保存期間、freee の条件、現金取消バグ） | 継続 | 税理士・社労士・弁護士・所轄税務署 |

## 9. フェーズ別の実装計画

| Phase | 内容 | 成果物・テスト | 前提 |
|---|---|---|---|
| 1 会計基盤 | Account・JournalLine・FiscalYear、`journals` の正規化と税区分修正、帳簿ビュー 9 種と CSV/XLSX、手入力・決算整理仕訳の API、カード入金消込の基礎、暗号化（freee/Square トークン）、監査ログの追記専用化、非同期ジョブ基盤、バックアップに media、**現金取消バグ修正（承認要）**、cap 追加 | マイグレーション 4 本、テスト約 60 件、E2E 回帰 | 開発 DB の migrate 状態確認、バグ修正の承認 |
| 2 領収書・承認 | `expenses` アプリ、タブレット撮影〜登録、AI（mock 既定／anthropic）、承認・差戻し・却下・訂正、重複検出、CashMovement/Expense/Payable への反映、レジ出金突合、PC 承認画面と一括承認、Evidence の拡張、アップロード検証 | テスト約 60 件、E2E `receipt.mjs`・`approve.mjs` | AI 本番接続は会社設定＋環境変数で opt-in |
| 3 銀行・スタッフ支払 | `banking` アプリ、CSV 取込・照合・解除、カード入金の自動作成、買掛金、一般業務委託費、支払台帳・年間集計 | テスト約 50 件、E2E `bank.mjs` | 匿名化した銀行 CSV サンプル（2〜3 行形式） |
| 4 年間決算 | `closing` アプリ、棚卸評価、固定資産・償却、開業費、決算整理、年間損益、決算チェックリスト、年度締め | テスト約 40 件、E2E `yearend.mjs` | 償却率表・評価方法の一次資料 |
| 5 入力ガイド | `tax` アプリの器（IndividualTaxProfile・TaxYearConfiguration・TaxReturn・GuideProgress）、白色／青色のガイド、金額集計、コピー、チェック、PDF、公式リンク | テスト約 30 件、E2E `taxreturn.mjs`（ガイド部分） | 作成コーナー令和8年分の公開（1 月上旬）前は「要確認」表示 |
| 6 申告書類 | 収支内訳書・青色申告決算書・確定申告書の集計、所得控除、税額計算（ミセミルのエンジン移植＋拡張）、版管理、未対応検出、転記用 PDF、公式様式への重ね刷り（確認後） | ゴールデンテスト（設例）、テスト約 60 件 | 令和8年分の税制・様式の公式確認 |
| 7 XTX | 仕様取得、XML 生成、スキーマ検証、帳票間連動、公式 e-Tax ソフトでの取込試験、合格後に公開 | 検証記録を `tax_etax_export.md` に | 仕様公開時期に依存 |
| 8 総合テスト | 会計・税務・UI・権限・セキュリティ・出力・バックアップ復元、本番チェックリスト | 全件成功報告、ユーザー承認後に本番デプロイ | — |

各フェーズの終了時に「変更内容・マイグレーション・テスト結果」を報告し、承認を得てから次へ進む。本番デプロイは明示の承認後。

## 10. 本番環境への影響とリスク

### 10.1 本番への影響
- Phase 0: なし。
- Phase 1 以降: 既存テーブルは列追加のみ、既存 API は項目追加のみ。影響しうる既存挙動は (a) 現金取消バグ修正で締めの理論現金が変わる、(b) 現金経費の仕訳科目が雑費一括→承認科目（月次損益の内訳が変わる。合計は不変）、(c) `GET /api/auth/me` の caps 増加でメニューが増える、(d) 暗号化のデータ移行（トークンの再保存。失敗時は再接続が必要）。
- デプロイ時: `migrate` は列追加と新テーブルのみで短時間。暗号鍵の環境変数が無いと起動しない設定にするため、**鍵の配布を先に行う**。

### 10.2 リスク一覧

| # | リスク | 影響 | 対策 |
|---|---|---|---|
| 1 | 国税庁の令和8年分資料（様式・作成コーナー・XTX 仕様）が 2027 年 1 月まで公開されない | Phase 5〜7 の最終確定が遅れる | 年度設定を器として作り、令和7年分で動作検証。未確認項目は「要確認」表示。公開後に差し替える手順を用意 |
| 2 | 税制パラメータの誤り（基礎控除の時限加算など） | 税額の誤計算 | 一次資料 URL と確認日を設定に残し、国税庁の設例でゴールデンテスト。税理士の確認を経てから `verified` |
| 3 | 現金取消バグ修正で過去の締めの数字が変わる | 利用者の混乱 | 修正前に承認。修正後の差分一覧を出す。過去の締めスナップショットは書き換えない |
| 4 | 二重計上（領収書と現金経費、銀行明細と売上） | 帳簿・申告の誤り | 経路を 1 本に固定（マトリクス）、一貫性テスト、照合画面での差分表示 |
| 5 | AI 読取の誤りをそのまま承認 | 経費の誤計上 | 自動確定しない、原本と並べて確認、内訳不一致の警告、一括承認は警告なしのみ |
| 6 | 機密データの漏えい（トークン・原本・申告入力） | 法令・信用 | 暗号化、非公開配信、アップロード検証、監査ログ、AI へ送るデータの最小化 |
| 7 | 税理士法 52 条（運営者・記帳代行が税務判断・申告書作成に関与）と誤解される表示 | 法令リスク | 判断はオーナー本人のみの権限設計、画面文言、約款の弁護士確認 |
| 8 | 電帳法の「完全対応」と誤認 | 税務調査での否認 | 要件別の対応状況表示、紙原本の保存運用を案内、事務処理規程のひな形 |
| 9 | 公式様式 PDF の再配布・重ね刷りの利用条件 | 著作権・利用規約 | 確認まで独自レイアウト。確認後に公式様式 |
| 10 | 既存の未適用マイグレーションの状態が不明 | Phase 1 の開始手順 | 最初に `showmigrations` で確認（DB 起動が必要） |
| 11 | 非同期ジョブを Celery なしで実装する運用負荷 | AI 読取・取込の遅延 | `run_jobs` を systemd/cron で監視。失敗は画面に出す |
| 12 | 複数店舗で店舗ごとの事業者が異なる場合の分離不足 | 他の納税者の情報が見える | 事業者単位の queryset 絞り込みとテスト |
| 13 | XTX を検証前に公開してしまう | 誤った申告データ | `is_released=false` 既定、運営者のみ取得、公式ソフト取込試験の記録を必須 |
| 14 | 消費税課税事業者の利用者 | 対応範囲外の申告を完成扱い | `consumption_tax_status` が課税なら `unsupported_items` に入れ完成不可 |
| 15 | ミセミル・TaxFlow の未検証部分の持ち込み（AI 実 API 未接続、freee/MF CSV 未照合） | 動かない・合わない | 流用後に NOXIA のテストで再検証。実 API は mock で開発し本番接続は opt-in |

---

## 付録 A. ミセミル（survey_project）調査の要点

| NOXIA の要件 | ミセミルの対応コード | 移植方法 |
|---|---|---|
| 領収書 AI 読取 | `accounting/ai.py`（Anthropic strict tool-use、`RECEIPT_SCHEMA`、Sonnet→Opus の 2 段構え、サーバー側で税額再計算・科目名解決）、`tests_ai.py` 17 件 | ほぼそのまま移植。機能フラグ・`normalize_invoice_number` の依存を外し、プロンプトを個人事業主向けに。承認状態・使用量保存・予算を追加 |
| 重複検出 | 無し | 新規（TaxFlow の dHash・dedupe を流用） |
| 銀行 CSV 取込 | 無し（カード明細のみ）。`statements.py` の文字コード判定・見出し語＋統計の列判定・和暦・符号解釈は純粋関数 | `statements.py` を移植し、入金／出金 2 列・残高・口座行を追加。列対応の保存は新規 |
| 照合 | `reconcile.py`（金額一致＋日付窓＋店名類似度、貪欲割当、同点処理、amount_diff）、`api_reconcile.py`（manual／ignore／reset／unmatch）、Excel 3 シート | ロジック流用。多対一・手数料差・残高連続性・照合履歴は新規 |
| レジ出金と領収書の突合 | `register_check.py`（店舗×日、確認時金額の控え、±3 日の日付ずれ候補） | 考え方を流用 |
| 消費税・インボイス | `tax.py`（内税切捨て）、`yayoi.py`（適格／区分記載 80%→50%、2029 年の終了判定なし） | `tax.py` はそのまま。インボイス判定は作り直し |
| 給与の源泉 | `withholding.py`＋税額表モデル・CSV（令和8・9年分） | NOXIA は給与計算をしないため参考。報酬・料金は既存 NOXIA が対応済み |
| 所得控除・税額計算 | **`year_end_calc.py`（純粋）＋`payroll/data/year_end_{2025,2026,2027}.json`（コードに数値を書かない方針、`provisional` フラグ、改正前への切替 `params_for`）、`rounding.py`、`tests_year_end_calc.py` 16 件（国税庁の設例を含む）** | **最優先で移植**。確定申告向けの拡張は §7.2 |
| 住民税 | 特別徴収の管理のみ（税額計算なし） | 不要 |
| PDF 様式出力 | `filing/printing/pdfbase.py`（reportlab 内蔵 CID フォント、左上原点の座標、`Sheet.spaced` でマスに右詰め、pypdf で重ね合わせ、mediabox のずれ補正） | そのまま移植。様式ごとの座標モジュールを新規 |
| CSV 仕様準拠 | `filing/csvfile.py`・`text.py`（JIS 水準・異体字置換・cp932/CRLF・カンマ拒否・不足なら出力しない） | `text.py` はそのまま、csvfile の型を流用 |
| 暗号化 | `core/crypto.py`（AES-256-GCM・版付き鍵・ローテーション・fail-closed）、`core/db_fields.py`、`labor/storage.save_encrypted` | そのまま移植 |
| 監査ログ | `core/audit.py`（追記専用・meta フィルタ・request_id）、730 日保持 | そのまま移植 |
| 非公開ファイル | `core/storage.PrivateMediaStorage`＋トークン配信。領収書は暗号化されていない | 移植し、領収書も暗号化・中身判定を追加 |
| 機能フラグ・ステップアップ・MFA | `CompanyFeature`、`core/stepup.py`、`mfa/` | 参考（DRF に合わせて作り直し） |
| 画面 | `journal/page.tsx` の「写真から一括登録」（3 並列・状態機械・縮小）、`reconcile/page.tsx` の集計タイル絞り込み | UI の型として流用（部品化して移す） |

**ミセミルに無いもの**: 確定申告書・収支内訳書・青色申告決算書、事業所得・給与以外の所得の計算、事業主貸借・家事按分・減価償却・棚卸・年次決算・元帳生成、青色申告特別控除・医療費・寄附金・雑損・予定納税、防衛特別所得税・定額減税、XTX、消費税申告計算、銀行 CSV・残高連続性・多対一・手数料仕訳、重複検出、報酬の源泉（NOXIA は実装済み）、電帳法対応、DRF、税理士ロール。

既知の落とし穴（移植時に直す）: インボイス経過措置の終了判定なし、登録番号は形式チェックのみ、給与所得控除表が 2,000 万円まで、復興特別所得税の段取りが年末調整方式、2027 年分 JSON は暫定、期限のハードコード（扶養の所得要件・インボイス切替日）、領収書ファイル非暗号化・中身判定なし、手入力伝票にロックなし。

## 付録 B. TaxFlow 調査の要点

TaxFlow は税理士事務所向け（Django 5.2＋DRF＋Next.js 16、pytest 83 件、実 AI API・Docker・freee 未検証、e-Tax/XTX の実装なし）。NOXIA に流用する部品:

- AI: `ai/providers.py`（Fixture／Anthropic／Gemini、例外の振り分け、refusal・max_tokens 検知、JSON Schema 構造化出力）、`prompts/document_extract_v2.md`＋GUARDRAIL、`schemas.py`（records・evidence 引用・tax_breakdown・rows・region、サーバー検証）、`service.py`（予算予約→精算、冪等キー、再試行）、`pricing.py`。注意: PDF title・画像ラベルにファイル名を入れている箇所は除く。
- 画像・重複: `intake/images.py`（EXIF 向き・HEIC・dHash 64bit・200dpi とカラー判定・API 用縮小）、`accounting/dedupe.py`（同一ハッシュ 1.0／dHash≤6／日付±3 日・金額・取引先 0.8、自動削除しない）。
- 取込: `common/text.py`（文字コード判定・CSV インジェクション対策）、`intake/tabular.py`（見出し行自動検出・数式を評価しない）、`intake/column_mapping.py`（見出し署名で列対応を学習、キーワード推定、AI 提案）、和暦・金額正規化。残高の連続性は未実装。
- 照合: `accounting/reconciliation.py`（取引先名正規化・類似度、exact／fee_diff（許容差 1,100 円）／2〜4 件の合算、手数料仕訳、別名学習）。確定取消は未実装。
- 仕訳: 借方/貸方・補助・部門・税区分の行、状態遷移、承認済みは新版で差替え、宣言的ルールと修正からの候補学習、決定論的検証。
- 出力: freee 17 列・弥生 25 列（2000/2110/2100/2101）・MF 23 列・汎用・TKC/MJS/JDL、コード変換表、取込試験の記録。
- 電帳法: 検索フィルタ、論理削除と履歴 API、自己点検チェックリスト（8 項目）、事務処理規程ひな形（`docs/templates/scanner_storage_rules.md`）、`docs/DENCHO_COMPLIANCE.md`。
- セキュリティ: TOTP 2FA、ログイン失敗ロック、無操作タイムアウト、IP 許可、追記専用監査、認証付き配信。
- Next.js: アップロード（ドラッグ&ドロップ・カメラ・分割送信・進捗ログ）、`DraftEditor`（原本対査 2 カラム・j/k/space/a・一括操作）、`DocumentViewer`、照合画面、列対応エディタ。

## 付録 C. 調査で使った主な一次ファイル

- NOXIA: `backend/apps/{core,sales,cash,staff,inventory,catalog,accounting}/models.py`、`apps/core/permissions.py`、`apps/accounting/{journals,services,withholding,freee,pdf,views}.py`、`frontend/src/components/AppShell.tsx`、`frontend/src/lib/{api,session,types}.ts(x)`、`frontend/src/app/(app)/**`、`docs/ACCOUNTING_DESIGN.md`、`docs/TAX_OPEN_QUESTIONS.md`、`docs/PAYROLL_LIMITATIONS.md`、`docs/UNDECIDED_RULES.md`、`README.md`。
- ミセミル: `accounting/{models,ai,statements,reconcile,register_check,pos_journal,yayoi,tax}.py`、`payroll/services/{year_end_calc,withholding,rounding,dependents,rates,versions}.py`、`payroll/data/year_end_*.json`、`filing/{printing/pdfbase,csvfile,text,models,confirmations}.py`、`core/{crypto,db_fields,storage,audit,stepup}.py`、`frontend/app/accounting/**`、`CLAUDE.md`、`HANDOFF_ACCOUNTING_AUTOMATION.md`、`VPS_DEPLOY_*.md`、`SECURITY_DEPLOYMENT.md`。
- TaxFlow: `apps/api/modules/{ai,intake,accounting,common,identity}/**`、`apps/web/src/**`、`docs/*.md`。
