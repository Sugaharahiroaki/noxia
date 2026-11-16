# 統合作業の進み具合

`UNIFIED_PLAN.md` のフェーズごとの結果。再開するときは、このファイルの最後のフェーズと `git log` を見て、次のフェーズから続ける。

順番: P0 → T1 → T2 → T3 → T4 → T5 → T6 → T7 → M1 → M2 → M3 → M4 → M5 → M6（S1 はオーナーの回答待ちで実行しない）

作業の前提（毎回確認すること）:
- PostgreSQL が止まっているとテストが無言で待ち続ける。`.local\pgsql\bin\pg_ctl.exe -D .local\pgdata -l .local\pg.log start` を `Start-Process` で起動する。
- `npm run build` は検証環境（Next 3001）を止めてから行う（同じ `.next` を使うため）。
- `.local/e2e` の `django()` ヘルパーは `POSTGRES_DB` を見る。検証環境に向けるときは `$env:POSTGRES_DB="yorumiru_verify"` も付ける。

---

## P0 照合（2026-11-17）— 完了

コードは変更していない（下の「テストの修正」を除く）。計画書を直した。

### 既存テストの結果
- バックエンド: 388 件。最初は 2 件失敗 → テスト側の原因だったので直して 388 件すべて通過。
- `npm run typecheck`・`npm run build`: 通過。

### テストの修正（業務処理は変えていない）
深夜 0 時〜営業日切替（6 時）の間だけ落ちるテストが 2 件あった。暦の日付と営業日が 1 日ずれる時間帯に、テストが暦の日付を基準にしていたため。
- `apps/staff/tests/test_backs.py` `test_effective_dates`: 基準日を営業日（`business_date_now`）に変更。
- `apps/staff/tests/test_payroll.py` `test_closed_period_snapshot_unaffected_by_later_wage_change`: 遡及する時給設定の開始日を、既定の時給より確実に後になる日数に変更。

### 食い違いと直し方（計画書に反映済み）
| 計画の項目 | 実際のコード | 直し方 |
|---|---|---|
| 2.2「確定 → 卓詳細へ ✅」 | `visits/new/page.tsx` は確定後に `/floor` へ戻る。注文確定・延長・指名追加も同じ | 🔌 に訂正。行き先を卓詳細に変える（Q18） |
| A4 延長候補 | `services.cancel_line` は延長の取消で `CourseEntry.extension_minutes` を引く。候補ごとに分数が違うと終了予定が戻らない | 延長明細に分数を残す `OrderLine.extension_minutes`（null 可）を T3 で足す |
| A5 場内指名 | 指名・同伴を列挙している箇所が `staff/backs.py`（3 か所）・`sales/services.py`・`sales/views.py`・`BackRule.specificity`・フロント 5 か所にある | T2 で全部そろえる。経理（`accounting/compensation.py`）は触らず「その他バック」に入る（Q19） |
| B2 客の行 | `add_course_entry` は `Visit.guest_count` を有効なセットの人数合計で上書きする | 客の行がある伝票は途中合流でも客の行を作る。`guest_count` は来店人数のまま、在席人数は別に返す |
| B6 折半の検証 | `staff/backs.py` `validate_targets` が、fixed 合計の上限超え・重複・負の値・percent 合計を拒否済み | 検証は足さない。T4 はテストだけ。従業員は会社単位の照合（同じ会社の別店舗は可、別会社は拒否）が既存の設計 |
| B1 集計 3 値 | `floor/state` の卓は辞書を手で組み立てている。`cash.day_summary` の `sales_total` が「確定済み会計の合計」の既存の定義 | `paid_total` は同じ定義にする。卓の色・区分は辞書に足す |
| F1 POS テーマ | 全体が暗い配色で、`.input`・`.badge-*`・直書きの色（`#17223a`）が暗い背景前提 | POS 用クラスの中でそれらも上書きする |
| 5 章 給与項目 | 日払いは可。マイナスは % 控除・契約の控除・負のバック調整で可。ボーナスは正のバック調整（項目名は「バック調整」）だけで、手当として記録する API は無い。遅刻・欠勤の項目は無い | M3 では作らず、調整を符号で分けて表示し、報告に残す |

### 5 章「既存で代替」の確認結果
- `analytics/daily`・`monthly`・`breakdown`: あり。キャスト別は「担当（primary_employee）の伝票の会計額」で、参考設計の「呼び小計」（指名のある伝票の小計）とは別の指標。M1 で集計関数を足す。
- `/attendance`: 打刻・修正、日払い（お渡し）、バック一覧、支払集計（`payroll.view_all` と再認証が必要）。代替できる。
- `/closing`: 開店・入出金・日締め・金種・差額・締め後の修正。代替できる。月間一覧だけ無い（M2）。
- 時給の計算: 分単位・円未満切り捨て。参考設計 6.4 の検算例（22:00〜02:15・時給 1,400 → 5,950）と一致する。
- 売上仕訳は `Checkout.tax_breakdown` だけを使う。明細の種別を足しても経理の仕訳は変わらない。

### 決まっていないこと（6 章に追加）
- Q15 ヘッダーの「トップ」→ 出さない
- Q16 ヘッダーの「注文」→ 使用中の卓を選んで、その卓の注文画面を開く
- Q17 延長候補ごとに料金が違うときの「延長後に無制限へ変更」の差額 → 既存の計算のまま
- Q18 確定後に卓詳細へ戻すか卓一覧へ戻すか → 卓詳細へ（今までの「自動で卓一覧へ戻る」運用から変わる。実機で確認が必要）
- Q19 場内指名のバックの経理上の報酬項目 → 既存の振り分けどおり「その他バック」

### T1 に入る前にオーナーが決めること
なし（すべて「決まるまでの扱い」で進められる）。

---

## T1 POS テーマ・ヘッダー・ホール・長押しメニュー（2026-11-17）— 完了

### 変更したファイル
- バックエンド: `apps/catalog/models.py`・`serializers.py`・`migrations/0003_table_color_kind.py`（卓の色 `color`・営業枠の区分 `kind`。既定は空）、`apps/sales/services.py`（`change_entry_time`）、`apps/sales/views.py`（`floor/state` に `unpaid_total`・`paid_total`・`sales_total`・`engaged_employee_ids`・卓の色と区分、`ChangeEntryTimeView`）、`apps/sales/urls.py`、`seed_dev.py`（卓の区分。仮設定）、`apps/sales/tests/test_floor_header.py`（14 件）
- フロント: `src/app/globals.css`（`.pos-theme`）、`src/components/PosHeader.tsx`（新規）、`AppShell.tsx`、`VisitActionMenu.tsx`（新規）、`src/lib/useLongPress.ts`（新規）、`src/lib/types.ts`、`src/app/(app)/floor/page.tsx`、`visits/[id]/checkout/page.tsx`（直書きの色 1 か所）
- 検証用（Git 管理外）: `.local/e2e/t1.mjs`、`.local/verify-env-ascii.ps1`（`scripts\verify-env.ps1` は Windows PowerShell 5.1 で日本語の解析に失敗するため、同じ内容の ASCII 版）

### 画面の入口
- ホール: ログイン → 店舗を選ぶ → `/floor`。POS の画面（`/floor`・`/visits/...`）は金色のヘッダーに変わる。
- ヘッダー: メニュー（既存のナビ・店舗切替・ログアウト）／未精算・精算済・売上合計／日時／ホール状況（赤バッジ = 営業中の伝票数）／注文（使用中の卓を選ぶ）／待機キャスト／設定（T6 までは `/settings/store`）
- 長押しメニュー: ホールで使用中の卓を長押し（PC は右クリックでも可）→ 伝票削除・テーブル移動・時間変更・経過時間表示

### 確認した内容と結果
- バックエンド 402 件、`npm run typecheck`、`npm run build`: 通過
- `.local/e2e/t1.mjs`（1133×744）: 29 項目すべて通過。スクリーンショットは `.local/e2e/screenshots/t1/`
  - 空席の表示、5 列の格子、区分の見出し、縦横スクロールなし、タップ領域 44px 以上
  - 既存の入店画面で入店 → 使用中カード（人数・残り分・開始〜終了・金額）、ヘッダーの 3 値
  - 長押し → メニュー（卓詳細へは移動しない）、削除のキャンセルと確定、テーブル移動、時間変更（開始・終了が 10 分早まり金額は同じ）、経過時間表示の切替（再読み込み後も保持）
  - 管理画面（`/sales`）には POS テーマが付かない
- 途中で見つけて直した不具合: 長押しのあと指をメニューの上で離すと次の右クリックでメニューが開かなかった（押し始めのたびに状態を戻すようにした）

### 決めた扱い（境界）
- 時間変更は「開始時刻の訂正」。終了予定を同じ分だけずらす（延長した分は残る）。無制限は開始時刻だけ変える。会計済み・終了済みのセット、会計済み／取消済みの伝票は 409 で拒否。未来の時刻と 24 時間より前は拒否。料金は変えない。最初のセットを直したときは伝票の入店時刻もそろえる（営業日は変えない）。
- `paid_total` は `cash.day_summary` の `sales_total` と同じ定義（一部返金は引かない）。
- 待機キャスト（Q7）: 出勤中のキャストのうち、営業中の伝票の担当・セットの担当・指名や同伴の対象に入っていない人。商品のバック対象（ドリンクをもらっただけ）は「卓に付いている」に数えない。

### 仮の扱い・決まっていないこと
- ヘッダーの「トップ」は出していない（Q15）。「注文」は使用中の卓を選ぶ一覧（Q16）。テーブル結合は出していない（Q6）。
- 経過時間表示の切替は端末ごと（ブラウザの localStorage）で、卓ごとではなくホール全体に効く。映像では卓ごとか全体か判別できない。
- 卓の色は卓カードの上の帯に出す（カード全体の色は空席＝青・使用中＝金で固定）。色の設定画面は T6。

### 実機の iPad で確かめること
- タッチの長押し（自動テストはマウスの長押しと右クリックで確認。Safari の長押しで文字選択や呼び出しメニューが出ないこと）
- 長押しのあとに卓詳細へ移動しないこと
