# 会計・確定申告統合 データベース設計案

状態: **Phase 0 の提案（未実装・マイグレーション未作成・承認待ち）**。2026-11-11 作成。
前提ドキュメント: `tax_accounting_audit.md`（既存調査）、`tax_architecture.md`（全体方針）。既存の経理設計は `ACCOUNTING_DESIGN.md`。

---

## 1. 設計方針

1. **既存テーブルは壊さない**。既存モデルへの変更は「列の追加（既定値あり）」と「選択肢の追加」だけにする。既存の POS・現金・日払い・源泉徴収のテーブルの意味は変えない。
2. **仕訳は複式簿記の正規化構造にする**。いまの `accounting.Journal.lines`（JSON）は freee 連携用の直列化として残し、帳簿・決算・申告の集計はすべて新しい `JournalLine` 行から行う。帳簿（売上帳・現金出納帳・預金出納帳・売掛帳・買掛帳・仕入帳・経費帳）は **仕訳行の絞り込み（ビュー）** であり、別テーブルに二重に持たない。
3. **1つの元データは1本の経路でだけ仕訳になる**（§7 二重計上防止マトリクス）。レジ現金の経費は既存の `cash.CashMovement` を経由し、レジ締めと帳簿の両方で同じ1件を見る。
4. **金額は Decimal(12,2)**（既存と同じ。表示は円単位）。税額・控除額の計算に float を使わない。
5. **確定済みの記録は書き換えない**。取消は `is_voided`、訂正は新規行＋`corrected_from`、承認・差戻しは履歴テーブル。年度を締めた後の仕訳は変更不可（再開は理由付きで記録）。
6. **年度別の税制はデータ（`TaxYearConfiguration`）で持ち、計算結果は版（`TaxReturnVersion`）に設定のハッシュとともに保存**する。過去年度の数字が後の改正で変わらない。
7. **納税者（事業者）単位の分離**。税務データは `TaxPayer` に属し、店舗は `StoreTaxProfile` で事業者に対応付ける（既存）。複数店舗の個人事業主は「店舗別の内訳」と「事業者合計」を `JournalLine.store` で区別する。
8. **個人番号（マイナンバー）は保存しない**（既存方針を維持。申告書への記入は利用者が国税庁の画面で行う）。
9. 新しい機能はアプリを分ける: `apps/accounting`（既存＋勘定科目・仕訳行・年度）、`apps/expenses`（領収書・承認・買掛）、`apps/banking`（銀行口座・取込・照合）、`apps/closing`（棚卸・固定資産・決算整理）、`apps/tax`（年度設定・申告書・版・出力・入力ガイド・税務監査ログ）。

---

## 2. 全体図

```
core.Company ─┬─ core.Store ──── accounting.StoreTaxProfile ──┐
              │                                              ▼
              └─ accounting.TaxPayer（事業者＝納税者） ── tax.IndividualTaxProfile（個人事業主の申告情報）
                      │
                      ├─ accounting.Account（勘定科目）◄── accounting.JournalLine（仕訳行）── accounting.Journal（仕訳）
                      │                                        │  ├ bank_account → banking.BankAccount
                      │                                        │  ├ store / employee / counterparty（補助）
                      │                                        │  └ fiscal_year → accounting.FiscalYear
                      │
                      ├─ 元データ（1件→1仕訳）
                      │    cash.BusinessDay（締め済み日次） / cash.BankDeposit / staff.EmployeePayment / accounting.TaxRemittance
                      │    accounting.Expense ◄─ expenses.ExpenseReceipt（領収書ワークフロー）─► cash.CashMovement（店舗現金のとき）
                      │    accounting.OwnerFundTransfer / accounting.CardSettlement ◄─ banking.BankReconciliation ◄─ banking.BankTransaction
                      │    expenses.Payable / expenses.PayablePayment
                      │    closing.InventoryClosing / closing.DepreciationEntry / closing.ClosingAdjustment（決算整理仕訳）
                      │
                      └─ tax.TaxReturn（年度×事業者）── tax.TaxReturnVersion ── tax.TaxExport
                              │   └ tax.TaxYearConfiguration（年度の税制・様式・ガイド定義）
                              └ tax.GuideProgress / tax.TaxAuditLog
```

---

## 3. 既存テーブルへの変更（追加のみ）

| テーブル | 追加・変更 | 理由 |
|---|---|---|
| `accounting.Journal` | `fiscal_year` FK(null)、`entry_kind`（auto/manual/adjustment/opening/closing）、`status`（auto/confirmed/locked）、`confirmed_by/at`、`locked_at`、`store` は既存 | 決算整理仕訳（手入力）と年度ロック。`lines` JSON と `sync_key` はそのまま |
| `accounting.Expense` | `account` FK(Account, null)、`tax_breakdown` JSON（[{rate, taxable_incl, tax}]）、`bank_transaction` FK(null) | 8%/10% 混在の領収書、銀行明細との照合。`account_key` は互換のため残す |
| `accounting.Evidence` | `origin`（paper_photo 紙を撮影 / scan 紙をスキャン / electronic 電子取引データ）、`phash`（64bit 知覚ハッシュ, 16桁hex, null）、`captured_at`、`page_count`、`image_width/height`、`quality_flags` JSON | 電子帳簿保存法の「電子取引」と「スキャナ保存」の区別、類似画像の重複検出、画質判定 |
| `accounting.CardSettlement` | `bank_transaction` FK(null)、`period_start/period_end`（対象売上の期間）、`store` は既存 | 銀行明細からの自動作成と、カード売掛の未入金残高の計算 |
| `accounting.AccountMapping` | `account` FK(Account, null) | freee の科目対応を勘定科目マスタに紐付ける（`key` は互換） |
| `accounting.TaxCategory`（選択肢） | `outsourcing_fee`「一般業務委託費（源泉なし・要確認）」 | 仕様 §8 の支払区分3種 |
| `cash.CashMovement` | `account` FK(Account, null) | レジ現金の経費を承認済み領収書の科目で仕訳する（未設定なら従来どおり雑費＋警告） |
| `core.StoreSettings` | `receipt_capture_enabled`（既定 True）、`manager_can_approve_expenses`（既定 False） | 店舗タブレットの領収書ボタン、店長承認の許可範囲 |
| `core.Company` または新 `core.CompanySettings` | `ai_enabled`（既定 False）、`ai_monthly_budget_jpy`、`ai_provider`（mock/anthropic） | AI 読取の停止・予算（会社単位） |
| `core.AuditLog` | 変更なし | 一般操作は既存の監査ログ。申告関連は `tax.TaxAuditLog`（§4.5） |
| `core.permissions` | ケイパビリティ追加（`tax_architecture.md` §5） | — |

既存マイグレーションの状態（2026-11-05 時点のメモ）: `core 0003`・`staff 0003/0004`・`accounting 0001/0002` は開発用 DB に未適用。新しいマイグレーションはこれらの後に積む。本番 DB には何も適用しない。

---

## 4. 新規テーブル

### 4.1 `apps/accounting`（追加分）— 勘定科目・仕訳行・年度

**Account（勘定科目）** — 事業者ごと。初期値は個人事業主向けの標準科目を seed（収支内訳書・青色申告決算書の行に対応付け済み）。

| 列 | 型 | 内容 |
|---|---|---|
| taxpayer | FK TaxPayer | 事業者 |
| key | slug(40) | 安定キー。既存 `journals.ACCOUNTS` のキー（cash, bank, sales, cast_fee, rent …）と同じ値を使い、既存の仕訳と連続させる |
| code | char(8) | 表示用コード（例 1010） |
| name | char(60) | 科目名（例「普通預金」） |
| category | choice | asset / liability / equity / revenue / cogs / expense / owner（事業主貸借） |
| statement_line | char(40), null | 収支内訳書・青色申告決算書の行キー（`TaxYearConfiguration.statement_lines` と対応。例 `sales`, `purchases`, `salaries`, `rent`, `utilities`, `supplies`, `fees`, `other_expense`）。null は帳票に出ない科目（売掛金など） |
| default_tax | choice | 既定の税区分（sales_10 / sales_8 / purchase_10 / purchase_8 / out_of_scope / non_taxable） |
| is_cash / is_bank | bool | 現金出納帳・預金出納帳の対象 |
| is_system | bool | seed した科目（削除不可・名前変更可） |
| is_active, sort_order | | |
| UNIQUE | (taxpayer, key), (taxpayer, code) | |

**JournalLine（仕訳行）** — 帳簿・決算・申告の集計の唯一の元。

| 列 | 型 | 内容 |
|---|---|---|
| journal | FK Journal | 仕訳（ヘッダ） |
| taxpayer, date | FK / date | `journal` から複写（帳簿クエリ用のインデックス） |
| line_no | smallint | 行番号 |
| side | choice | debit / credit |
| account | FK Account | 勘定科目 |
| amount | Decimal(12,2) | 正の値のみ |
| tax_category | choice | 税区分（上記） |
| tax_amount | Decimal(12,2), null | 消費税額（税込経理では参考値。免税事業者は null） |
| bank_account | FK banking.BankAccount, null | 預金出納帳の口座別 |
| store | FK Store, null | 店舗別損益の内訳 |
| employee | FK staff.Employee, null | スタッフ支払台帳 |
| counterparty | char(100) | 取引先（売掛帳・買掛帳の相手先） |
| memo | char(120) | |
| UNIQUE | (journal, line_no) | |
| INDEX | (taxpayer, account, date), (taxpayer, bank_account, date), (taxpayer, store, date) | |

制約（アプリ層＋テスト）: 1仕訳の借方合計＝貸方合計。合わない場合は既存どおり仮勘定行を足し `Journal.warnings` に残す。`Journal.lines` JSON は JournalLine から生成する（freee 送信用。将来 `freee.py` を JournalLine 読みに変えたら廃止）。

**FiscalYear（会計年度）** — 個人事業主は暦年固定（`TaxPayer.fiscal_year_end_month` が 12 でなければエラー）。

| 列 | 内容 |
|---|---|
| taxpayer, year | 事業者・年（UNIQUE） |
| start_date, end_date | 1/1〜12/31（年途中開業の年は開業日〜12/31） |
| filing_type | white / blue_10 / blue_55 / blue_65（年度を開いた時点の `IndividualTaxProfile` から複写。将来の拡張） |
| status | open / closing（決算作業中） / closed（締め） / filed（利用者が申告済みと記録） |
| closed_at/by, filed_at | |
| reopen_count | 再開回数。再開は `FiscalYearEvent`（action, reason, by, at）に記録 |

ルール: `status=closed` の年度に属する仕訳は作成・変更・取消を拒否する。`journals.build` は締めた年度を飛ばし、元データが変わっていれば「締め後に元データが変わった」警告を `FiscalYear.drift_warnings` に残す（隠さない）。

### 4.2 `apps/expenses` — 領収書ワークフロー・買掛

**ExpenseReceipt（領収書・経費の登録）** — 店舗タブレットからの1枚＝1件。

| 列 | 型 | 内容 |
|---|---|---|
| company, store | FK | 登録した店舗（スタッフは自店舗だけ見える） |
| taxpayer | FK, null | 店舗の事業者（`StoreTaxProfile` から解決。未設定なら承認不可） |
| evidence | OneToOne accounting.Evidence | 原本（画像/PDF）。1件1ファイル |
| status | choice | draft（下書き）/ ocr_running（AI読取中）/ pending（確認待ち）/ approved（承認済み）/ returned（差戻し）/ rejected（却下）/ voided（取消） |
| payment_method | choice | store_cash（店舗現金）/ owner_personal（オーナー個人資金）/ bank（銀行振込）/ card（クレジットカード）/ other |
| submitted_by, submitted_at | | スタッフ |
| **AI 読取** ocr_status | choice | none / running / done / failed / skipped |
| ocr_provider, ocr_model, ocr_prompt_version | char | 根拠の記録 |
| ocr_raw | JSON | モデルの構造化出力そのまま（金額は文字列） |
| ocr_date, ocr_vendor, ocr_total, ocr_tax_breakdown, ocr_invoice_number, ocr_summary, ocr_suggested_account_key, ocr_confidence | 各型 / JSON | 読取結果（自動確定しない） |
| **スタッフ確認値** receipt_date, vendor, total_amount, tax_breakdown, invoice_number, summary, memo | | スタッフが画面で直した値（AI 失敗時は手入力） |
| **管理者確定値** approved_date, approved_vendor, approved_amount, approved_tax_breakdown, account | FK Account | 承認時に確定した科目・税区分・金額 |
| approved_tax_category, approved_store, approved_payment_method, approved_note | | 対象店舗・支払方法の訂正 |
| duplicate_candidates | JSON | [{receipt_id, reasons:["sha256","phash","date_amount_vendor"], distance}] |
| duplicate_override_reason | char | 重複候補があっても承認した理由（必須） |
| cash_movement | OneToOne cash.CashMovement, null | `store_cash` を承認したときに作る／既存の現金経費の出金に紐付ける |
| expense | OneToOne accounting.Expense, null | `store_cash` 以外を承認したときに作る |
| payable | FK Payable, null | 後日払い（カード・請求書）のとき |
| reviewed_by/at, return_reason, reject_reason | | |
| version | int | 編集ごとに +1（楽観ロック） |
| INDEX | (store, status), (taxpayer, status), (taxpayer, receipt_date) | |

**ExpenseReceiptEvent（承認履歴＝仕様の ExpenseApproval）** — 追記のみ。

| 列 | 内容 |
|---|---|
| receipt | FK |
| action | submit / ocr_done / ocr_failed / edit / approve / return / resubmit / reject / void / correct |
| actor, at | |
| before, after | JSON（変わった項目だけ） |
| reason | 差戻し・却下・訂正の理由 |

**Payable（買掛金・未払金）**

| 列 | 内容 |
|---|---|
| company, taxpayer, store(null) | |
| supplier | FK inventory.Supplier, null（仕入先台帳と連携） |
| counterparty | 取引先名 |
| invoice_date, due_date | 請求日・支払期日 |
| amount, tax_breakdown, account | 金額・税率内訳・科目（仕入高・外注費・水道光熱費 …） |
| evidence | FK Evidence, null |
| source | receipt（領収書承認から）/ manual / purchase_order |
| purchase_order | FK inventory.PurchaseOrder, null |
| status | open / partially_paid / paid / voided |
| paid_amount | 支払済み合計 |
| is_voided, void_reason, created_by | |

**PayablePayment（買掛金の支払い）**: payable FK, date, amount, method（bank / cash_register / owner_personal / card）, bank_transaction FK(null), cash_movement FK(null), note, created_by, is_voided。

### 4.3 `apps/banking` — 銀行 CSV 取込・照合

**BankAccount（銀行口座）**

| 列 | 内容 |
|---|---|
| company, taxpayer | |
| name | 表示名（例「○○銀行 事業用」） |
| bank_name, bank_code(4), branch_name, branch_code(3) | |
| account_type | ordinary / checking / savings |
| account_number_last4 | 下4桁だけ保存（全桁は保存しない） |
| holder_name | 名義 |
| account | FK Account（普通預金科目。口座ごとの内訳は `JournalLine.bank_account`） |
| opening_balance, opening_balance_date | 取込開始時の残高 |
| is_business | 事業専用 / 家事兼用（兼用口座は家事費の行を「対象外」にする前提） |
| is_active, note | |

**BankImportProfile（銀行別の列対応）** — 一度決めた列対応を使い回す（初めての銀行は候補を自動推測し、人が確認）。

| 列 | 内容 |
|---|---|
| taxpayer, bank_account(null) | 口座別、または事業者共通 |
| name | 「京都信用金庫 CSV」など |
| encoding | auto / cp932 / utf-8 / utf-8-sig |
| delimiter, header_rows, footer_rows | |
| column_map | JSON {date, description, withdrawal, deposit, amount_signed, balance, counterparty, memo}（列番号または見出し名） |
| date_format | 例 `%Y年%m月%d日`, `%Y/%m/%d`, 和暦 |
| amount_rules | JSON（カンマ・全角数字・「円」除去・マイナス表現） |
| sample_hash, last_used_at | |

**BankStatementImport（取込ファイル）**

| 列 | 内容 |
|---|---|
| bank_account, profile | |
| file | 非公開ストレージ（公開 URL なし） |
| sha256 | UNIQUE (bank_account, sha256)（同じファイルの二重取込を拒否） |
| original_name, encoding_detected, rows_total, rows_imported, rows_duplicate, rows_error | |
| period_start, period_end, opening_balance, closing_balance | |
| balance_check | ok / mismatch / unknown（残高列があるとき、前回の最終残高と連続しているか） |
| status | parsed（確認待ち）/ committed（取込済み）/ rolled_back |
| error_log | JSON |
| imported_by | |

**BankTransaction（銀行明細1行）**

| 列 | 内容 |
|---|---|
| bank_account, statement_import, row_no | |
| date | 取引日 |
| description, counterparty | 摘要・正規化した相手名（全角→半角、カナ寄せ） |
| amount_in, amount_out | Decimal（どちらか一方） |
| balance | Decimal, null |
| raw | JSON（元の行） |
| row_hash | sha256(口座, 日付, 摘要, 入金, 出金, 残高, 同日同額の連番) — UNIQUE (bank_account, row_hash)。重複取込防止 |
| status | unmatched / suggested（候補あり）/ matched / partially_matched / ignored（家事費・対象外）/ transfer（口座間振替） |
| ignore_reason | |
| INDEX | (bank_account, date), (bank_account, status) | |

**BankReconciliation（照合＝消込）** — 1件の照合は「明細1行 ⇄ 対象1件」。多対一・一対多は複数行で表す（配分額を持つ）。

| 列 | 内容 |
|---|---|
| bank_transaction | FK |
| match_type | card_settlement / bank_deposit（売上金の預入）/ employee_payment（振込給与・報酬）/ expense / payable_payment / owner_transfer / tax_remittance / transfer（自社口座間）/ manual_journal |
| 対象（いずれか1つ） | card_settlement FK / bank_deposit FK / employee_payment FK / expense FK / payable_payment FK / owner_transfer FK / tax_remittance FK / counter_bank_transaction FK / journal FK |
| amount | 配分額 |
| fee_amount | 手数料差（カード入金のとき。`CardSettlement.fee_amount` へ） |
| status | suggested / confirmed / voided |
| rule | 候補を出したルール名（amount_exact / amount_minus_fee / sum_of_period / name_alias …） |
| confirmed_by/at, voided_by/at, void_reason | 消込・消込解除の履歴 |
| journal | FK Journal, null（照合で生まれた仕訳。例: カード入金の手数料） |
| UNIQUE（部分） | status=confirmed の (bank_transaction, 対象) | |

### 4.4 `apps/closing` — 棚卸・固定資産・決算整理

**InventoryClosing（期首・期末棚卸）**

| 列 | 内容 |
|---|---|
| taxpayer, fiscal_year, store | UNIQUE (fiscal_year, store, kind) |
| kind | opening / closing（翌年の期首は前年の期末を複写） |
| as_of | 棚卸日 |
| method | last_purchase_cost（最終仕入原価法。届出がない個人事業主の法定評価方法）/ manual |
| total_amount | Decimal |
| stock_take | FK inventory.StockTake, null（既存の棚卸し結果を元にする） |
| status | draft / confirmed |
| confirmed_by/at, journal | 期末棚卸の決算整理仕訳 |

**InventoryClosingLine**: closing FK, product FK(null), name, quantity, unit_cost, amount, cost_source（supplier_product / product.cost_price / manual）。

**FixedAsset（固定資産・開業費）**

| 列 | 内容 |
|---|---|
| taxpayer, store(null) | |
| name, category | 資産名・種類（器具備品 / 建物附属設備 / 車両運搬具 / ソフトウェア / 開業費（繰延資産）…） |
| kind | tangible / intangible / deferred_opening_cost / deferred_other |
| acquired_on, acquisition_cost | |
| business_use_percent | 事業専用割合（家事按分） |
| depreciation_method | straight_line（定額法）/ declining_balance（定率法・届出が必要）/ lump_sum_3yr（一括償却資産 20万円未満）/ immediate（10万円未満の少額 → 消耗品費）/ blue_small_asset（30万円未満の特例・青色のみ・年300万円まで）/ voluntary（任意償却・開業費） |
| useful_life_years, rate | 耐用年数・償却率（**国税庁の償却率表の値を保存**。コードで 1/n を計算しない） |
| memorandum_value | 備忘価額 1円 |
| status | in_use / disposed / sold |
| disposed_on, disposal_amount | |
| evidence | FK Evidence |
| note | |

**DepreciationEntry（年度ごとの償却）**: asset FK, fiscal_year FK, months_in_service, amount_total, amount_business（按分後）, book_value_begin/end, calc_basis JSON（式・率・月数）, journal FK, UNIQUE (asset, fiscal_year)。

**ClosingAdjustment（決算整理）**: taxpayer, fiscal_year, kind（accrued_expense 未払金・未払費用 / accrued_revenue 未収金 / prepaid 前払費用 / household_split 家事按分 / bad_debt 貸倒 / inventory / depreciation / opening_cost / other）, description, amount, debit_account, credit_account, journal FK, created_by, is_voided, void_reason。

### 4.5 `apps/tax` — 年度設定・申告書・版・出力・ガイド・監査

**IndividualTaxProfile（個人事業主の申告情報）** — `TaxPayer` と 1対1（法人は作らない）。

| 列 | 内容 |
|---|---|
| taxpayer | OneToOne |
| individual_name, trade_name | 氏名・屋号 |
| business_start_date | 開業日（年途中開業の判定） |
| blue_approval_status | none / applied / approved / revoked |
| blue_approved_from_year, blue_application_date | 青色申告承認申請の年・提出日（未承認なら警告） |
| bookkeeping_method | simple（簡易簿記）/ double（複式） |
| consumption_tax_status | exempt（免税）/ taxable_general / taxable_simplified / taxable_2wari（2割特例） — 2026年分は免税の想定、課税なら「未対応」として申告書を完成扱いにしない |
| invoice_registration_number | T+13桁（登録している場合） |
| note | |

**TaxYearConfiguration（年度別の税制・様式・ガイド定義）** — 運営者が管理。公式資料で確認できた項目だけ `verified=true` にする。

| 列 | 内容 |
|---|---|
| year | UNIQUE（例 2026） |
| status | draft / verified / published |
| income_tax | JSON: 速算表 [{上限, 率, 控除額}]、課税所得の端数（1,000円未満切捨て）、税額の端数（100円未満切捨て） |
| surtaxes | JSON: 復興特別所得税率、防衛特別所得税（開始年・率） |
| deductions | JSON: 基礎控除（合計所得金額の区分と額）、配偶者控除・配偶者特別控除表、扶養控除（一般・特定・老人・同居老親）、特定親族特別控除、生命保険料控除（新・旧・上限）、地震保険料控除、医療費控除（足切り・上限）、寄附金控除、社会保険料・小規模企業共済、青色申告特別控除（10万円の要件） |
| statement_lines | JSON: 収支内訳書（一般用）と青色申告決算書（一般用）の行定義 [{key, label, form, box_no(null=未確認), account_keys[]}] |
| guide | JSON: 国税庁作成コーナーの入力ガイド [{section, order, key, label, source:"計算値のキー", official_label(null=未確認), verified:false, help}] |
| forms | JSON: 様式の版・PDF の置き場所・欄座標ファイルの版（未確認なら null） |
| sources | JSON: [{title, url, checked_on}] 一次資料 |
| verified_by/at, note | |
| content_hash | 計算に使った設定の同一性確認 |

**TaxReturn（申告書作成データ）** — 事業者×年度で1件。

| 列 | 内容 |
|---|---|
| taxpayer, fiscal_year | OneToOne（年度） |
| config | FK TaxYearConfiguration |
| filing_type | white / blue_10 |
| status | draft / in_review / complete / exported / filed_by_user（利用者が国税庁で申告したと記録）/ locked |
| inputs | JSON: 事業所得以外の入力（給与所得・その他所得・所得控除の入力値・源泉徴収税額・予定納税額・配偶者/扶養の区分と人数。**氏名・個人番号は持たない**） |
| business_summary | JSON: 店舗別損益と事業者合計（仕訳行からの集計。収支内訳書の行キーごと） |
| computed | JSON: 計算過程すべて（所得金額 → 所得控除 → 課税所得 → 税額 → 復興特別所得税 → 税額控除 → 源泉・予定納税 → 納付/還付。各段に根拠キーと端数処理） |
| unsupported_items | JSON: 未対応の所得・控除・ケース（1件でもあれば complete にできない） |
| warnings | JSON（青色未承認、締めていない営業日、仮勘定、未承認の領収書、未照合の銀行明細 …） |
| completed_at, locked_at, updated_by | |

**TaxReturnVersion（申告書の版）** — 計算を実行するたびに追記。

| 列 | 内容 |
|---|---|
| tax_return, version_no | UNIQUE |
| created_at/by, reason | 「計算」「訂正」「提出用」など |
| inputs, business_summary, computed | JSON（その時点の複写） |
| config_hash | 使った `TaxYearConfiguration.content_hash` |
| journal_snapshot_hash | 年度の仕訳行全体のハッシュ（計算根拠の仕訳が後で変わったか検出） |
| is_final | 利用者が「この版で申告した」と記録した版 |

**TaxExport（出力ファイル）**

| 列 | 内容 |
|---|---|
| version | FK TaxReturnVersion |
| format | pdf_shushi_uchiwakesho / pdf_aoiro_kessansho / pdf_shinkokusho / pdf_guide / pdf_ledger_* / csv_ledger_* / xlsx_* / xtx |
| file, sha256, size | 非公開ストレージ |
| generated_at/by | |
| validation | JSON（XTX: スキーマ検証結果・必須項目チェック・帳票間連動チェック。PDF: 欄の未確認フラグ） |
| is_released | XTX は公式ソフトでの取込試験が完了するまで false（運営者だけダウンロード可） |
| download_count, last_downloaded_at | |

**GuideProgress（入力ガイドの進捗）**: tax_return FK, item_key, checked, checked_at, copied_at, note。UNIQUE (tax_return, item_key)。

**TaxAuditLog（申告関連の監査ログ）** — 追記のみ・ハッシュ連鎖。一般の操作は `core.AuditLog`。

| 列 | 内容 |
|---|---|
| taxpayer, fiscal_year(null), tax_return(null) | |
| user, action | compute / version_create / export / download / lock / unlock / config_change / profile_change / year_close / year_reopen |
| detail | JSON（金額の要約・ファイルの sha256 など。個人情報は入れない） |
| created_at | |
| prev_hash, hash | 1件前のハッシュを含めた sha256（改ざん検出） |

---

## 5. 仕様の概念モデルとの対応

| 仕様 §15 の概念 | 実装 | 状態 |
|---|---|---|
| AccountingTransaction | 元データ（Checkout/Payment/CashMovement/EmployeePayment/Expense/…）＋ `Journal`（source_type/source_id/sync_key） | 既存を再利用 |
| JournalEntry | `accounting.Journal`（ヘッダ。列追加） | 既存＋拡張 |
| JournalLine | `accounting.JournalLine` | **新規** |
| ExpenseReceipt | `expenses.ExpenseReceipt`（＋既存 `Evidence`／`Expense`） | **新規** |
| ExpenseApproval | `expenses.ExpenseReceiptEvent` | **新規** |
| BankAccount / BankTransaction / BankReconciliation | `banking.*` | **新規** |
| Receivable | `sales.Receivable`（ツケ）＋カード売掛（`Payment(method=card)` − `CardSettlement`）＝集計で表現 | 既存を再利用 |
| Payable | `expenses.Payable` / `PayablePayment` | **新規** |
| StaffPaymentTaxRecord | `accounting.WithholdingRecord` / `SalaryWithholdingEntry` / `WithholdingMonth` / `TaxRemittance` | 既存（支払区分に一般業務委託費を追加） |
| FixedAsset | `closing.FixedAsset` / `DepreciationEntry` | **新規** |
| InventoryClosing | `closing.InventoryClosing`（既存 `inventory.StockTake` を元にする） | **新規** |
| TaxpayerProfile | `accounting.TaxPayer`（既存）＋ `tax.IndividualTaxProfile` | 既存＋新規 |
| TaxYearConfiguration | `tax.TaxYearConfiguration` | **新規** |
| TaxReturn / TaxReturnVersion / TaxExport | `tax.*` | **新規** |
| TaxAuditLog | `tax.TaxAuditLog`（＋既存 `core.AuditLog`） | **新規** |
| （勘定科目） | `accounting.Account` | **新規**（仕様に明記はないが帳簿に必須） |
| （会計年度） | `accounting.FiscalYear` | **新規** |

---

## 6. マイグレーション計画（順序）

| 順 | フェーズ | マイグレーション | 内容 |
|---|---|---|---|
| 1 | 1 | `accounting 0003_books` | Account, JournalLine, FiscalYear, FiscalYearEvent。Journal/Expense/Evidence/CardSettlement/AccountMapping の列追加。TaxCategory の選択肢追加 |
| 2 | 1 | `accounting 0004_seed_accounts` | 既存の各 TaxPayer に標準科目を作成（データ移行）。既存 `Journal.lines` から JournalLine を生成（行があれば） |
| 3 | 1 | `cash 0004_cashmovement_account` | `CashMovement.account` |
| 4 | 1 | `core 0004_company_settings` | CompanySettings、StoreSettings の2列 |
| 5 | 2 | `expenses 0001_initial` | ExpenseReceipt, ExpenseReceiptEvent, Payable, PayablePayment |
| 6 | 3 | `banking 0001_initial` | BankAccount, BankImportProfile, BankStatementImport, BankTransaction, BankReconciliation |
| 7 | 4 | `closing 0001_initial` | InventoryClosing(+Line), FixedAsset, DepreciationEntry, ClosingAdjustment |
| 8 | 5–6 | `tax 0001_initial` | IndividualTaxProfile, TaxYearConfiguration, TaxReturn, TaxReturnVersion, TaxExport, GuideProgress, TaxAuditLog |
| 9 | 5–6 | `tax 0002_seed_year_2026` | 2026年分の `TaxYearConfiguration`（status=draft。公式確認が済んだ項目だけ verified に更新） |

すべて `makemigrations --check` と、空 DB・seed_dev 済み DB の両方で `migrate` → `migrate <app> zero` の往復を確認してから PR にする。本番 DB への適用は明示的な承認後。

---

## 7. 二重計上防止マトリクス（元データ → 仕訳の経路は1本だけ）

| 元データ | 仕訳の作り方 | ほかの経路で仕訳してはいけないもの |
|---|---|---|
| POS の会計（`Checkout`/`Payment`/`Refund`/`ReceivableCollection`） | 締め済み `BusinessDay` の日次仕訳（既存 `daily_store_lines`） | 銀行明細の「売上金預入」は `BankDeposit` と照合するだけ（売上にしない）。カード入金は売掛金の消込だけ |
| レジ現金の経費（`CashMovement.kind=expense`） | 日次仕訳（科目は `CashMovement.account`、未設定なら雑費＋警告） | 領収書（`ExpenseReceipt.payment_method=store_cash`）は承認時に `CashMovement` を作る／紐付けるだけで、別に `Expense` を作らない |
| レジ現金以外の経費（領収書 承認 → `Expense`） | 経費仕訳（既存 `source_type=expense`） | 銀行明細・カード明細は `Expense` と照合するだけ |
| 後日払い（`Payable`） | 発生時: 費用／買掛金・未払金。支払時（`PayablePayment`）: 買掛金／預金・現金 | 支払時に費用を再計上しない |
| カード売上の入金（銀行明細） | 照合確定 → `CardSettlement` → 預金・支払手数料／売掛金（カード） | 入金を売上にしない |
| 売上金の銀行預入 | `BankDeposit`（既存）→ 預金／現金 | 銀行明細は照合のみ |
| 日払い・振込の支払い（`EmployeePayment`） | 日次仕訳（レジ）または支払仕訳（振込） | 銀行明細は照合のみ |
| 源泉所得税の納付（`TaxRemittance`） | 既存 | 同上 |
| 経営者との資金移動（`OwnerFundTransfer`） | 既存（事業主借／事業主貸） | **売上・経費にしない**（仕様 §5-4） |
| 自社口座間の振替 | 照合で `transfer` にし、預金（A）／預金（B）の1仕訳 | 片側だけ仕訳しない |
| 家事費・事業と無関係な明細 | `BankTransaction.status=ignored`（理由必須）。仕訳しない。兼用口座は事業主貸 | — |
| 決算整理（棚卸・償却・未払・按分） | `closing.*` → `Journal(entry_kind=adjustment)` | 年度1回。再計算は同じ `sync_key` を上書き（revision） |

---

## 8. データ整合性（仕様 §15 の要求への対応）

| 要求 | 対応 |
|---|---|
| 金額は Decimal | 全列 Decimal(12,2)。`apps.core.money.D` を使う。テストで float 混入を検出 |
| 取引の一意性 | `Journal.sync_key`、`BankTransaction(row_hash)`、`BankStatementImport(sha256)`、`ExpenseReceipt.evidence` 1:1、`DepreciationEntry(asset, fiscal_year)`、`InventoryClosing(fiscal_year, store, kind)` |
| 二重計上防止 | §7 マトリクス＋照合テスト（売上＝仕訳の売上、カード売上＝入金＋手数料、現金差額、源泉＝納付） |
| 監査可能な訂正履歴 | `ExpenseReceiptEvent`、`BankReconciliation(void)`、`Expense.corrected_from`、`Journal.revision`、`FiscalYearEvent`、`TaxAuditLog`（ハッシュ連鎖） |
| 年度締め後の変更管理 | `FiscalYear.status=closed` で仕訳の変更を拒否。再開は理由付き記録。締め後に元データが変わったら drift 警告 |
| 申告書の版管理 | `TaxReturnVersion`（inputs/computed/config_hash/journal_snapshot_hash） |
| 申告時点の計算根拠保存 | `computed` に計算過程、`config_hash` に税制設定、`TaxExport.sha256` に出力ファイル |

---

## 9. ミセミル・TaxFlow から流用する構造

| NOXIA の新テーブル | 参考元 | 流用する点 |
|---|---|---|
| ExpenseReceipt の AI 読取列・確認フロー | ミセミル `accounting/ai.py`、TaxFlow の領収書データ化 | 出力 JSON の項目（日付・支払先・税率別内訳・登録番号・信頼度）、人が確認してから登録する流れ |
| Evidence.phash / 重複候補 | TaxFlow の dHash 近似重複 | 64bit 知覚ハッシュとハミング距離の閾値 |
| BankImportProfile / BankTransaction | ミセミル `accounting/statements.py`、TaxFlow の列対応プロファイル | 文字コード判定、列対応の保存、残高の連続性で漏れ・重複を検出 |
| BankReconciliation | ミセミル `accounting/reconcile.py`、TaxFlow の照合 | 金額一致・手数料差・合算入金の候補、消込解除 |
| CashMovement と領収書の突き合わせ | ミセミル `accounting/register_check.py` | 店舗×日の POS 出金合計と領収書合計の照合 |
| TaxYearConfiguration.deductions / income_tax | ミセミル `payroll/services/year_end_calc.py`・`rates.py`・`versions.py` | 所得控除・税額速算表・端数処理の表と年版の持ち方 |
| 収支内訳書・決算書の PDF | ミセミル `filing/printing/pdfbase.py` | 公式様式 PDF に値を重ねる技法（pypdf＋reportlab） |
| 税務情報の暗号化 | ミセミル `core/crypto.py` | フィールド暗号化（freee トークン・口座情報・申告 inputs） |

詳細は `tax_accounting_audit.md` §3（ミセミル調査）を参照。
