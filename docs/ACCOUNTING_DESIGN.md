# 源泉徴収・キャスト精算・経理・freee 連携　設計書

対象: NOXIA（Django 5.2 + DRF / Next.js 15 / PostgreSQL 17）。2026-11-02 実装。
運用手順は `ACCOUNTING_MANUAL.md`、法令・税務上の要確認事項は `TAX_OPEN_QUESTIONS.md`。

---

## 1. 既存システムの調査結果

| # | 項目 | 調査結果 | 今回の扱い |
|---|---|---|---|
| 1 | 構成 | `backend/apps/*`（core, catalog, staff, sales, cash, customers, inventory, line_integration, terminals, analytics）、`frontend/src/app/(app)/*` | 新アプリ `apps/accounting` を追加。既存アプリは拡張のみ |
| 2 | DB・モデル | PostgreSQL。金額は Decimal、取引時点のスナップショット、取消は打消し・履歴 | 同じ方針（明細は書き換えず、取消・訂正を記録） |
| 3 | テナント | `Company` → `Store`。`X-Store-Id` を所属と照合（`core/permissions.py`）。従業員は会社所属 | 税務上の「事業者」(`TaxPayer`) を会社の下に追加し、店舗→事業者を `StoreTaxProfile` で対応付け |
| 4 | キャスト情報 | `staff.Employee`（氏名・表示名・入退店日）。契約区分・税務区分はなし | `accounting.CastContract` を追加 |
| 5 | 時給・バック | `WageSetting`（適用開始日付き）、`BackRule`/`BackEntry`（指名・同伴・商品のカテゴリ種別）。延長のバックはなし | 再利用。バックを報酬項目（指名・ドリンク・同伴・ボトル…）に分類 |
| 6 | 日払い・給与 | 退勤→お渡し額（時給×勤務時間＋確定バック）→「渡した」でレジ出金＋`EmployeePayment`。締め期間 `PayPeriod`。法定控除は未対応（`PAYROLL_LIMITATIONS.md`） | 「渡した」の中で源泉所得税・その他控除を計算し、差引支給額をレジから出す |
| 7 | 売上・現金・日次締め | `Checkout`/`Payment`/`Refund`/`Receivable`、`BusinessDay`・`CashMovement`・`BankDeposit`、締め後修正 `ClosingRevision` | 締め済み営業日から日次仕訳を作る |
| 8 | CSV・PDF | CSV は `analytics.ExportView`。PDF なし | reportlab（内蔵の日本語 CID フォント）で PDF を追加 |
| 9 | 権限・監査 | ケイパビリティ方式・再認証・`AuditLog`・運営者アクセス記録 | 経理用のケイパビリティと、記帳代行・税理士の権限付与を追加 |
| 10 | freee 等 | なし（決済端末は `terminals/providers.py` の接続部品方式） | 同じ方式で freee の接続部品（模擬／本番）を追加 |

### 実装済み・未実装・改善が必要だったもの
- **再利用**: 勤怠・時給・バック・日払いの「渡した」・現金出納・締め・二重登録防止（Idempotency-Key・行ロック）・監査ログ・ケイパビリティ・CSV 出力の仕組み。
- **新規**: 事業者、キャスト契約・税務区分、源泉税計算エンジン、控除額の台帳、源泉徴収明細、月次管理・納付、徴収高計算書の転記用データ、PDF、仕訳、freee 連携、証憑・経費・資金移動・カード入金、記帳代行・税理士の権限。
- **既存の不具合（今回は未修正・要承認）**: 同じ営業日に現金の会計を取り消すと、レジ締めの理論現金が二重に減る（`cash.services.day_summary` が取消済みの支払いを現金売上から外し、さらに返金も差し引くため）。確認テストで `会計前 0 → 会計後 2,500 → 取消後 −2,500`（正しくは 0）。修正案: 理論現金の計算では、その営業日に受け取った現金の支払い（取消済みを含む）を足し、返金を引く。締めの数字が変わるため、修正前に承認をいただきたい。

---

## 2. 全体の設計

```
POS（会計・勤怠・バック） ──▶ 日払い「渡した」 ──▶ EmployeePayment（手渡し額・源泉税・その他控除）
                                    │                         └─▶ CashMovement（レジ出金＝手渡し額）
                                    └─▶ accounting.services ──▶ WithholdingRecord（計算根拠つき明細）
                                                                 └─▶ DeductionUsage（控除額の台帳）
月次: WithholdingRecord を支払日の年月で事業者ごとに集計 ──▶ WithholdingMonth（集計・確認・納付の状態）
      └─▶ 徴収高計算書の転記用データ（JSON / PDF）・月次集計 PDF・キャスト別明細 CSV ──▶ 経営者が e-Tax で送信・納付 ──▶ TaxRemittance
仕訳: 締め済み営業日・振込・納付・経費・資金移動・カード入金 ──▶ Journal（sync_key で一意）──▶ FreeeSync ──▶ freee 振替伝票
```

### 2.1 源泉税計算エンジン（`apps/accounting/withholding.py`）
- 税額 =（源泉対象額 − 控除額）× 税率、1円未満切り捨て。控除額 = 1日あたりの控除額 × 報酬計算期間の**暦日数** − 同じ期間の給与等。
- 税率・控除額・端数処理は `WithholdingRule`（適用開始日・終了日つき）から**支払日**で選ぶ。コードに税率はない。
  - 〜2026-12-31: 所得税10%＋復興特別所得税2.1% = 10.21%
  - 2027-01-01〜: 所得税10%＋防衛特別所得税1%＋復興特別所得税1.1% = 10.21%（合計は同じ・内訳と根拠が異なる）
- **控除額の台帳**（`DeductionUsage`）: 事業者×キャスト×日ごとに 5,000円まで。支払いは計算期間の初日から順に未使用分を使う。
  - 同じ計算期間への追加支払い・月単位契約の日々の前払い・期間が重なる支払い・同じ事業者の別店舗での支払いでも重複しない。
  - 支払いを取り消すと台帳の行も消え、控除は再び使える。
  - 給与等（同じ期間）の額も台帳に入れて控除額から差し引く（施行令322条）。
- 報酬計算期間は契約の `calc_period`（日・週・半月・月）から勤務日を含む期間を出す。未設定・税務区分未確認・事業者未設定・ルールなしは計算せず **needs_review**。
- 「金額を変えて渡す」（手渡し額を指定）は、手渡し額＋その他控除 = 報酬 − 税額(報酬) となる最小の報酬額を求めてから計算する（`solve_gross_for_net`）。
- 明細には、計算期間・日数・対象額・使える控除額・使った控除額・課税対象額・税率・税額・ルールの版・エンジンの版・説明文を保存する。

### 2.2 給与所得
- 雇用（給与所得として確認済み）の人にはホステス報酬の計算式を使わない。税額表（甲欄・乙欄・丙欄）・扶養・社会保険の計算は **NOXIA では行わない**。
- 給与計算ソフトで計算した源泉徴収額を `SalaryWithholdingEntry` に入力（または振込の締め支払い登録時に入力）し、給与の徴収高計算書の集計に使う。
- 納期の特例（事業者の設定）は給与だけ半年ごと。ホステス報酬は常に毎月。

### 2.3 日払い精算との統合
- **源泉所得税の差し引きは店舗設定 `StoreSettings.withholding_enabled`（既定 False・2026-11-05 変更）**。False の間は税務区分も見ず計算しない（`tax.status = "disabled"`、`WithholdingRecord` も作らない）。レジの手入力の日払い・振込の支払いも同じ（振込で源泉徴収額を入力すると 400）。変更は tax.decide（オーナー）のみ。
- **店舗の控除項目** `StoreSettings.payout_deductions`（名目・%・最初からチェック、10個まで、code はサーバーが振って保持）。お渡しのときにチェックしたもの（`POST pay` の `deductions`、省略時は「最初からチェック」）だけ、その日の報酬総額×% を差し引く。契約の控除（厚生費など）は従来どおり自動。
  - 引いた内訳は `EmployeePayment.deduction_items` に保存し、同じ勤務日の2回目以降の支払いでは控除ごとに引き済みの額を除く（取消で戻る）。
  - 画面は `net_without_options − チェックした項目の額` を出し、`expected_amount` としてサーバーの計算と照合する（違えば 409）。源泉税の対象額は減らさない。
- `staff.payroll.daily_payout` に報酬項目の内訳（`items`）と源泉税の見込み（`tax`）を追加。`remaining_amount` は「今渡す額（源泉税・その他控除の後）」、`remaining_gross` は未精算の報酬。
- 「渡した」（`DailyPayoutPayView`）: 事業者の行をロック → 計算し直し → レジ出金（手渡し額）→ `EmployeePayment(amount=手渡し額, withholding_amount, deduction_amount)` → `WithholdingRecord`。画面に出ていた額（`expected_amount`）と違えば 409（古い画面での二重払い防止は既存どおり）。
- 源泉所得税はレジから出ないので理論現金には影響しない。仕訳では預り金。
- 取消（既存の出金取消）で明細も取消になり控除が戻る。訂正は `WithholdingAdjustment`（元の明細は変えない）。
- 手入力のレジ出金（日払い）・渡した額だけの振込登録は源泉税を計算せず「要確認」で月次に出す（漏れ防止）。
- 日払い精算書 PDF（店舗の1日分＋キャストごと、またはキャスト1人分）。キャスト本人は自分の分だけ出力可。

### 2.4 月次管理・納付
- 集計は**支払日（paid_on）の年月**、事業者単位（法定）。店舗別・キャスト別は内訳。
- 状態: 未集計 / 集計済み / 要確認 / 経営者確認済み / 納付待ち / 納付済み / 期限超過 / 訂正あり。
  集計時・確認時の数字を `snapshot` に保存し、その後に支払い・取消・訂正があれば「訂正あり」として再集計・再確認を求める。要確認が残っていれば確認できない。
- 納付期限: 翌月10日。土日・祝日・振替休日・国民の休日・12/29〜1/3 は翌日へ（国税通則法10条2項）。祝日は内閣府の規則どおりに計算（`jp_holidays.py`、臨時の祝日は `ACCOUNTING_EXTRA_HOLIDAYS`）。
- 納付の記録（経営者のみ・再認証）: 納付日・金額・方法・使ったお金（口座／レジ現金→出金を記録／個人資金）。納付すべき税額との照合結果を表示。e-Tax の認証情報は扱わない。

### 2.5 法定帳票の作成支援（`reports.py`, `pdf.py`）
- 報酬・料金等の所得税徴収高計算書: 区分コード **08「ホステス等の報酬・料金」**、人員（実人員）、支払額、税額、摘要「支払額からの控除額」、年度（納付日の会計年度）、納期等の区分（例 `50811`）。
- 給与所得・退職所得等の所得税徴収高計算書: 俸給・給料等／賞与／日雇労務者の賃金（延べ人員）、納期の特例は「自〜至」。
- 出力: 月次集計 PDF、キャスト別明細 CSV、転記用 PDF、JSON。**e-Tax に取り込める形式ではない**と画面・帳票に明記。

### 2.6 仕訳と freee 連携（`journals.py`, `freee.py`）
- 元データ1件につき仕訳1件、`sync_key = 事業者:種類:ID`（一意）。作り直しても二重にならず、内容が変われば revision が上がる。
- 日次仕訳（締め済み営業日）: 売上（税率別）／現金・カード売掛金・ツケ売掛金、返金、ツケ取消、ツケ回収、レジ経費、その他入出金（仮勘定＋警告）、日払い（報酬＝手渡し＋預り金＋その他控除）、現金過不足。
- 別仕訳: 銀行入金、振込の支払い、源泉税の納付、経費、経営者との資金移動（法人: 役員借入金／個人: 事業主借・事業主貸）、カード入金（手数料）。
- 貸借が合わない場合は仮勘定を入れて警告を残す（隠さない）。
- freee: OAuth2 認可コード（アクセストークン6時間・リフレッシュトークン90日・1回限り）。振替伝票 API で作成・更新（PUT）・削除（DELETE）。
  - 二重登録防止: 仕訳ごとに状態と freee の ID を保持。送信前に「送信中」を確定させ、途中で止まったものは「結果不明」にして自動再送しない。同じ内容は送り直さない。摘要に `[NOXIA:ID]`。
  - 失敗は「失敗」→ 再送ボタン。通信履歴 `FreeeSyncLog`。
  - 本番送信は `FREEE_LIVE_ENABLED=1` とクライアント情報があるときだけ。ほかは模擬接続。
  - 勘定科目・税区分は事業者ごとに freee のマスタから対応付け（未設定なら本番送信しない）。
- 仕訳 CSV（借方・貸方の列）は freee の「他社会計ソフト仕訳のインポート」で列を割り当てて取り込む用（公式テンプレートと同じ並びとは保証しない）。
- 照合: POS 売上と仕訳の売上、現金差額、カード売上とカード入金、源泉税（徴収＋訂正）と納付、freee の状態。
- 月次損益: 仕訳から収益・費用を集計（資金移動・預り金・売掛金は損益に入れない）。仮勘定があれば「未確定」と表示。

### 2.7 権限（`core/permissions.py`）
| ケイパビリティ | オーナー | 会社管理者 | 店長 | 記帳代行 | 税理士等 | 運営者 |
|---|---|---|---|---|---|---|
| tax.view（源泉税の閲覧） | ○ | ○ | – | ○ | ○ | ○ |
| tax.decide（税務区分の確定・月次の経営者確認・訂正・納付記録） | ○ | – | – | – | – | – |
| tax.advise（確認コメント） | ○ | ○ | – | – | ○ | – |
| accounting.view / bookkeep（記帳・証憑・仕訳・照合・出力） | ○ | ○ | – | ○ | 閲覧のみ | ○ |
| accounting.freee（freee 連携の管理） | ○ | ○ | – | ○ | – | ○ |
| accounting.settings（事業者・権限付与） | ○ | – | – | – | – | ○ |
| contracts.edit（契約の登録） | ○ | ○ | ○ | – | – | ○ |

- 記帳代行・税理士等は、会社オーナーが `AccountingAccessGrant`（会社単位・有効期限つき）で許可した会社だけ見られる。
- 記帳代行ができないこと: 個別の税務判断、申告書の作成、税務代理としての e-Tax 送信、許可のない会社の閲覧。
- 運営者も税務上の判断（tax.decide）はできない。tax.decide・freee・設定の更新は再認証が必要。

---

## 3. データベースの変更

マイグレーション（**開発用 DB・本番 DB には未適用**。テスト DB と検証用 DB `yorumiru_verify` でのみ実行）:
- `staff/0003_employeepayment_deduction_amount_and_more.py`: `EmployeePayment` に `withholding_amount`・`deduction_amount`（既定 0。既存の行・計算は変わらない）。
- `accounting/0001_initial.py`: 下表のテーブルを追加。
- `accounting/0002_seed_withholding_rules.py`: 源泉徴収ルール2件を登録（データ移行）。
- `core/0003_payout_deductions.py`: `StoreSettings` に `withholding_enabled`（既定 False）・`payout_deductions`（既定 []）。（2026-11-05）
- `staff/0004_payout_deductions.py`: `EmployeePayment` に `deduction_items`（既定 []）。（2026-11-05）

| テーブル | 内容 |
|---|---|
| TaxPayer / StoreTaxProfile | 事業者（法人・個人事業主、所轄税務署、整理番号、納期の特例）と店舗の対応 |
| AccountingAccessGrant | 記帳代行・税理士等への権限付与 |
| CastContract / PayItemSetting | キャスト契約（契約区分・期間・計算期間・支払方法・税務区分・確認状況・チェック・控除・契約書）、報酬項目の扱い |
| WithholdingRule | 源泉徴収のルール（版・適用期間・税率・内訳・控除額・端数処理・根拠） |
| WithholdingRecord / DeductionUsage | 支払いごとの源泉徴収明細、控除額の台帳 |
| SalaryWithholdingEntry / WithholdingAdjustment | 給与の源泉徴収（外部計算の入力）、訂正 |
| WithholdingMonth / TaxRemittance | 月次の状態とスナップショット、納付記録 |
| Evidence / Expense / OwnerFundTransfer / CardSettlement | 証憑（論理削除・ハッシュ・保存期限）、経費、経営者との資金移動、カード入金 |
| AccountMapping / Journal / FreeeConnection / FreeeSync / FreeeSyncLog | 科目の対応付け、仕訳、freee 接続、連携状態、通信履歴 |

既存機能への影響: 既存テーブルへの追加は `EmployeePayment` の2列のみ（既定 0）。税務区分が確認済みでないキャストの日払いは従来と同じ金額を渡す（「要確認」の表示が増える）。
ファイル保存先は `MEDIA_ROOT`（既定 `backend/media`、公開 URL なし。API が権限を確認して返す）。

---

## 4. API（すべて `/api/accounting/...`、`X-Store-Id` 必須）

| メソッド・パス | 内容 | 権限 |
|---|---|---|
| GET overview | 事業者・店舗・アラート・e-Tax リンク | tax.view か accounting.view |
| GET/POST taxpayers, PATCH taxpayers/{id}, PUT stores/{id}/taxpayer | 事業者・店舗の事業者 | 閲覧 / accounting.settings |
| GET/POST grants, PATCH grants/{id} | 記帳代行・税理士の権限 | accounting.settings |
| GET/POST contracts, PATCH/DELETE contracts/{id} | 契約 | contracts.edit（閲覧は tax.view も可） |
| POST contracts/{id}/review | 税務区分の確定 | tax.decide |
| POST contracts/{id}/advice | 税理士コメント | tax.advise |
| GET/POST contracts/{id}/document | 契約書 | contracts.edit |
| GET/PUT pay-items | 報酬項目の扱い | 閲覧 / tax.decide |
| GET withholding/rules, months, month, records, alerts | ルール・年間一覧・月次・明細・アラート | tax.view |
| POST withholding/month/{aggregate,confirm,awaiting,advice} | 集計・経営者確認・納付待ち・コメント | 集計: tax.decide か bookkeep / 確認・納付待ち: tax.decide / コメント: tax.advise |
| POST withholding/remittances, remittances/{id}/void | 納付記録 | tax.decide（再認証） |
| POST withholding/records/{id}/resolve | 要確認の確定 | tax.decide（再認証） |
| POST withholding/adjustments, adjustments/{id}/void | 訂正 | tax.decide（再認証） |
| GET/POST withholding/salary-entries, …/{id}/void | 給与の源泉徴収（外部計算） | 閲覧 tax.view / 登録 tax.decide か bookkeep |
| GET withholding/form, form.pdf, summary.pdf, detail.csv | 転記用データ・PDF・CSV | tax.view |
| GET withholding/store-summary | 店舗の管理集計 | tax.view か payroll.view_all |
| GET daily-payouts.pdf | 日払い精算書 | payroll.pay（本人は自分の分） |
| GET/POST evidences, GET evidences/{id}/file, POST evidences/{id}/delete | 証憑 | 閲覧 accounting.view / 登録 bookkeep |
| GET/POST expenses, owner-transfers, card-settlements, POST {kind}/{id}/void | 経費・資金移動・カード入金（訂正は取消＋再登録） | 閲覧 accounting.view / 登録 bookkeep |
| GET journals, journals.csv, pl, reconcile, POST journals/build | 仕訳・CSV・損益・照合・仕訳作成 | 閲覧 / 作成 bookkeep |
| GET/PUT account-mappings | 科目・税区分の対応付け | 閲覧 / accounting.freee |
| GET freee, freee/logs, freee/master/{account-items,taxes}; POST freee/{connect,callback,disconnect,settings,send}; POST freee/syncs/{id}/resolve | freee 連携 | 閲覧 / accounting.freee（再認証） |

既存 API の変更: `GET /api/staff/daily-payouts`（`items`・`tax`・`withheld_amount` など追加）、`POST /api/staff/daily-payouts/pay`（源泉税を計算して記録、応答に `withholding_amount` など）、`POST /api/staff/payments`（`amount_basis`・`period_*`・`withholding_amount`）、`POST /api/cash/movements` と出金の取消（源泉徴収明細を連動）。

---

## 5. 画面

| 画面 | 内容 |
|---|---|
| 出勤・お渡し（既存を拡張） | カードに「源泉所得税（お店が預かる）」「厚生費」、確認待ちの警告、お渡し額は差引後。「渡した」の確認に内訳。日払い精算書 PDF |
| 経理 → 源泉所得税管理 `/accounting` | アラート、ホステス報酬／給与、年間の月別一覧（指定の11項目）、月の詳細（集計・経営者確認・納付待ち、店舗別・キャスト別、要確認の確定、転記用、PDF・CSV、e-Tax へのリンク、納付記録、訂正、税理士コメント、給与の入力） |
| 経理 → 月次損益・経費・証憑 `/accounting/books` | 月次損益、照合、仕訳（作成・CSV）、経費、証憑（電帳法の検索）、資金移動、カード入金 |
| 経理 → freee 連携 `/accounting/freee` | 接続（模擬・本番）、送信開始日、科目・税区分の対応付け、仕訳ごとの状態・再送・結果不明の確認、API と CSV の使い分け |
| 経理 → キャスト契約・税務区分 `/accounting/contracts` | 契約の登録、実態のチェックと税務区分の確定（理由必須）、税理士コメント、報酬項目の扱い |
| 経理 → 経理設定 `/accounting/settings` | 事業者、店舗の事業者、記帳代行・税理士の権限、計算ルール（閲覧） |

---

## 6. テスト

`backend/apps/accounting/tests/`（45件）＋既存209件 = 254件すべて成功。E2E は `.local/e2e/accounting.mjs`（検証用 DB に対して 16/16 成功）。
2026-11-05: 源泉の店舗設定・控除項目のテスト `apps/staff/tests/test_payout_deductions.py`（13件）を追加し計267件すべて成功。E2E `.local/e2e/deductions.mjs`（11/11）、`accounting.mjs` は最初に A社の店舗の源泉をオンにしてから実行（16/16）。

| 必須テスト | テスト |
|---|---|
| 1 日額報酬 | test_engine.test_daily_fee, test_payout.test_daily_fee_withholding_and_cash |
| 2 月単位契約の日払い | test_payout.test_monthly_contract_daily_advances |
| 3 複数回の追加支払い | test_payout.test_additional_payments_same_day |
| 4 重複控除防止 | test_engine.test_no_double_deduction_same_period, test_payout.test_same_taxpayer_two_stores_share_deduction |
| 5 源泉税0円 | test_engine.test_zero_tax, test_payout.test_zero_tax_payment_recorded |
| 6 端数処理 | test_engine.test_nta_example_rounding（国税庁の例 60,749円） |
| 7 給与と業務委託 | test_payout.test_salary_vs_outsourcing, test_contract_type_change_resets_review |
| 8・9 複数キャスト・複数店舗・同一事業者 | test_monthly.test_multi_cast_multi_store_aggregation |
| 10 月末をまたぐ支払い | test_payout.test_month_boundary_uses_payment_date |
| 11 訂正・過払い | test_monthly.test_overpayment_refund_adjustment, test_payout.test_void_restores_deduction |
| 12 月次確定後の修正 | test_monthly.test_workflow_and_correction_after_confirm |
| 13 freee 二重登録防止 | test_freee_permissions.test_freee_no_double_registration, test_failed_send_can_be_retried |
| 14 現金残高と日払い | test_payout.test_daily_fee_withholding_and_cash, test_daily_journal_balances_with_withholding |
| 15 税率改正 | test_engine.RuleSelectionTests |
| 16 他テナント | test_freee_permissions.TenancyTests |
| 17 帳票の正確性 | test_monthly.test_forms_and_exports, test_salary_form_from_external_payroll |
| 18 納付期限 | test_engine.DueDateTests |
| 19 権限 | test_freee_permissions.PermissionTests |
| 20 既存 POS の回帰 | 既存209件すべて成功 |
