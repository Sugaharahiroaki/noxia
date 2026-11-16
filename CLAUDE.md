# NOXIA

小規模ラウンジ・ガールズバー向けの店舗運営 Web アプリ。店舗タブレット（iPad 横向き）の POS と、PC の管理・経理画面を **1 つのアプリ・1 つの DB** で提供する。起動手順・環境変数・開発用ログインは `README.md` を読む。

## いま進めている作業
店舗タブレット POS の作り込みと、管理用の集計画面の追加を「統合計画」に沿って進めている。

- 統合計画（正）: `docs/unified/UNIFIED_PLAN.md`
- フェーズ別の作業指示: `docs/unified/PROMPTS.md`
- 参照元の仕様（読むだけ。そのまま実装しない）:
  - `docs/unified/spec/tablet_pos_video_spec.md` … タブレットの操作映像の分析
  - `docs/unified/spec/admin_reference_design.md` … 管理画面の参考設計。**技術スタック・モデル名・URL・配色・10章のプロンプトは NOXIA に当てはめない**。使うのは画面の列定義（7章）と計算式・検算例（6章）だけ

参照元と既存コードが食い違うときは、既存コードと `UNIFIED_PLAN.md` を優先する。`UNIFIED_PLAN.md` に書いていない食い違いを見つけたら、実装せずに報告する。

## 技術
- バックエンド: Python 3.11 / Django 5.2 / DRF 3.18 / PostgreSQL 17（`backend/`）
- フロントエンド: Next.js 15 App Router / React 19 / Tailwind CSS 4 / TypeScript（`frontend/`）
- 部品ライブラリ・状態管理ライブラリは入れていない。`src/components/ui.tsx` と `src/lib/useLoad.ts` を使う
- 出力: XLSX は openpyxl、PDF は reportlab（どちらも導入済み）。新しいライブラリを足す前に相談する

## コマンド
```powershell
# 起動（DB → Django 8000 → Next 3000）
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Dev

# バックエンドのテスト（Django テストランナー。pytest ではない）
cd backend; $env:PYTHONUTF8="1"; .\.venv\Scripts\python.exe manage.py test apps -v 1
# 対象を絞る例
.\.venv\Scripts\python.exe manage.py test apps.sales -v 1

# フロントエンドの検査
cd frontend; npm run typecheck; npm run build

# 画面の通し確認（開発用 DB を壊さない検証環境。Django 8001 / Next 3001）
powershell -ExecutionPolicy Bypass -File scripts\verify-env.ps1 -Reset
cd .local\e2e; $env:BASE_URL="http://localhost:3001"; node pos.mjs
```
`.local/` は Git 管理外。E2E スクリプトは `.local/e2e/*.mjs`（Playwright を node で直接実行）に置く。

## 用語と既存モデルの対応
参照元の言葉を、NOXIA の既存の名前に読み替える。新しい名前で同じものを作らない。

| 参照元の言葉 | NOXIA |
|---|---|
| Shop / ShopSetting | `core.Store` / `core.StoreSettings` |
| 伝票（Bill） | `sales.Visit`（営業中の伝票）＋ `sales.Checkout`（会計の確定） |
| セット（BillSet / SetPattern） | `sales.CourseEntry` / `catalog.Course` |
| 指名・同伴（BillNomination） | `sales.OrderLine`（`line_type` = nomination / douhan） |
| 注文（BillOrder / Menu） | `sales.OrderLine`（product）/ `catalog.Product`・`Category` |
| キャスト・スタッフ | `staff.Employee` ＋ `EmployeeStoreAssignment.role`（cast / staff / manager） |
| バック | `staff.BackRule` → `staff.BackEntry`、折半は `sales.OrderLineBackTarget` |
| 勤怠 | `staff.Shift` |
| 入出金・レジ精算 | `cash.CashMovement` / `cash.BusinessDay` |
| 売掛 | `sales.Receivable`（支払方法 receivable「ツケ・未収」） |
| 小計 / 本計 | `Checkout.subtotal` / `Checkout.total` |

## 守ること
- **金額計算は `backend/apps/sales/pricing.py` の `compute_totals` だけ**。フロントで税・サービス料・丸めを計算しない。画面の金額は API の `quote` / `running_total` をそのまま出す
- 金額は `Decimal`。丸めは `apps/core/money.py` の関数を使う
- 伝票の操作は `backend/apps/sales/services.py` の関数を通す。ビューに業務処理を書かない
- 営業日は `apps/core/business_date.py` で求める。`date.today()` を使わない
- 店舗データは必ず `store` を持ち、`StoreCaps(...)`（`X-Store-Id` と所属の照合）で守る。別店舗の卓・従業員・商品の ID を受け取ったら拒否する。新しい API には必ず「別店舗の ID を渡すと拒否される」テストを付ける
- 状態を変える POST は `@idempotent` を付け、フロントは `newIdempotencyKey()` を送る（二重タップ・再送で重複させない）
- 価格・税率・バック条件は取引時点の値を明細に保存する。マスタ変更で過去の明細を書き換えない
- 会計済みの明細は直接書き換えない。訂正は既存の取消・返金処理を使う
- 権限は役割ではなくケイパビリティ（`apps/core/permissions.py`）で判定する。「タブレット用ロール」は作らない
- 経理（`apps/accounting`・`banking`・`closing`・`tax`・`expenses`）と源泉徴収の既存機能は、指示がない限り触らない。源泉徴収は `StoreSettings.withholding_enabled` で店舗ごとに切り替える既存の仕組みを使う
- 参照元アプリのロゴ・名称・CSS・文言をコピーしない。アプリ名は NOXIA、アイコンは `frontend/public/` の既存のものを使う
- 映像や参照設計に出てくる金額・率・時刻（15,600円、15%、20%、10分前 など）を定数として埋め込まない。店舗設定かテスト用データに置く

## 進め方
- 1 フェーズ = `PROMPTS.md` の 1 ブロック。指示された範囲の外を直さない
- モデルを変えたら migration を作り、既存データが壊れない既定値にする
- フェーズの最後に、バックエンドのテスト全件・`npm run typecheck`・`npm run build` を通す。通らないまま完了と報告しない
- 画面を変えたフェーズは、検証環境で実際に操作して確認する（iPad mini 横向き相当: 1133×744）
- コミットはフェーズ単位。メッセージは日本語で、既存の履歴の書き方に合わせる
- 仕様が決まっていない箇所（`UNIFIED_PLAN.md` の 6 章）は、推測で実装せずに報告に残す
