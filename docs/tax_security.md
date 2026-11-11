# 会計・確定申告統合 セキュリティ設計

状態: **Phase 0 の提案（未実装・承認待ち）**。2026-11-11 作成。
関連: `tax_architecture.md` §5・§8・§10、`tax_api_design.md` §2、`tax_accounting_audit.md` §10（リスク）。

---

## 1. 前提と守るもの

| 守るもの | 例 | 脅威 |
|---|---|---|
| 税務・経営情報 | 売上・利益・申告書データ・所得控除の入力・源泉徴収明細 | 他テナント・他店舗・スタッフ・退職者からの閲覧、権限昇格、運営者の濫用 |
| 原本ファイル | 領収書画像・銀行 CSV・契約書・出力した申告書 PDF/XTX | 公開 URL の推測、改ざん、消失、無断の外部送信 |
| 認証情報 | freee・Square のトークン、AI の API キー、銀行口座情報 | DB ダンプ・ログ・バックアップからの漏えい、フロントへの露出 |
| 個人情報 | 従業員の氏名・支払額、オーナーの扶養情報 | 不要な収集（マイナンバー）、AI への送信、監査ログへの混入 |
| 整合性 | 仕訳・年度締め・申告書の版 | 二重計上、締め後の改ざん、計算根拠の消失 |

NOXIA はセルフサービス型の会計・申告支援ソフトであり、運営者が税務代理・個別の税務相談・他人の申告書の代理作成をすることを前提にしない（税理士法 52 条）。システム上も運営者・記帳代行に税務判断の権限を与えない（既存方針を継続）。

---

## 2. マルチテナント分離

| 層 | 既存 | 追加 |
|---|---|---|
| 会社・店舗 | `X-Store-Id` を `resolve_store_access` で所属照合。`StoreScopedViewSet`/`StorePK` で関連 ID の店舗一致を検証 | 新アプリの全 ViewSet/APIView で同じ部品を使う。`taxpayer` を持つモデルは「選んだ店舗の事業者」で絞る共通 mixin `TaxpayerScopedQuerysetMixin` を追加 |
| 納税者（事業者） | `get_taxpayer` が同じ会社の事業者を `taxpayer_id` で選べる | 1会社に複数事業者がある場合の分離を強める: 事業者を跨ぐ参照は `accounting.settings` 保持者のみ。申告データ（`tax.*`）は常に店舗→事業者の 1 件に限定 |
| 店舗（スタッフ） | — | 領収書 API は `store = request.store` かつ `submitted_by = request.user`（スタッフ）で絞る。承認者は事業者内の店舗 |
| オブジェクト単位 | 関連 ID の店舗一致検証（StorePK） | 承認・照合・版の操作は対象オブジェクトを `select_for_update` で取り、`taxpayer` が一致しなければ 404（存在を漏らさない） |
| 既知の穴 | 納付記録でレジ出金する店舗が事業者の店舗か未検証（`accounting/services.py:666-676`） | Phase 1 で修正 |
| テスト | `core/tests/test_tenancy.py` 23 件 | 新 API ごとに「他会社・他店舗・他事業者・スタッフの越権・記帳代行の越権・運営者の tax.decide 不可」を必須テストにする（`tax_test_plan.md` §7） |

---

## 3. 権限

- ケイパビリティは `tax_api_design.md` §2。原則: **税務判断はオーナー本人のみ**、**スタッフは自店舗の登録状況だけ**、**運営者も税務判断不可**。
- 再認証（REAUTH_CAPS）に `yearend.confirm`・`tax.return.edit`・`tax.return.export`・`tax.config.manage` を追加。
- **ログイン直後の自動再認証**（`core/views.py:114` がログイン時に `reauth_until` を設定）は、申告関連の操作では使わない: `StoreCaps(..., reauth=True)` を「ログイン後の再認証をした記録（`reauth_source="explicit"`）」でだけ満たすようにする。既存の POS 操作の利便性は変えない（変更対象は tax.*／yearend.confirm のみ）。
- ページ単位ガード（フロント `RequireCaps`）は見栄えのためで、認可は常に API。
- 権限表は画面に固定で書かれている（`settings/store/page.tsx`）ため、`GET /api/auth/capabilities`（cap とラベル・役割表）を追加して画面がそこから描くようにする（表示と実装の乖離防止）。

---

## 4. 機密データの暗号化

| 対象 | 現状 | 対応 |
|---|---|---|
| freee アクセストークン・リフレッシュトークン | 平文 TextField（`accounting/models.py:556-557`） | ミセミル `core/crypto.py`（AES-256-GCM・版付き鍵・ローテーション）と `EncryptedTextField` を移植し、Phase 1 のデータ移行で暗号化 |
| Square アクセストークン | 平文（`terminals/models.py:34`） | 同上 |
| 銀行口座番号 | （新規） | 全桁を保存しない。下4桁のみ。名義・銀行コードは平文可 |
| 申告書の入力（所得控除・扶養情報） | （新規） | `TaxReturn.inputs`・`TaxReturnVersion.inputs` を暗号化列に。計算値（`computed`）は集計上の金額なので暗号化せずインデックス可にする |
| 原本ファイル（領収書・CSV・契約書・出力 PDF/XTX） | 平文でローカル保存 | `save_encrypted`（ミセミル `labor/storage.py`）でファイル本体を暗号化し、平文の sha256 を別に持つ。配信時に復号 |
| 鍵管理 | — | 環境変数 `FIELD_ENCRYPTION_KEY_V1..V9`・`FIELD_ENCRYPTION_ACTIVE_VERSION`。本番で鍵が無ければ起動しない（fail-closed）。鍵は DB・ファイルのバックアップとは別の場所に保管 |
| 制約 | — | 暗号化列は検索・集計に使えない。検索が要る項目（取引日・金額・取引先）は平文のまま（電帳法の検索要件） |

---

## 5. ファイルの安全な受け取りと配信

| 項目 | 対応 |
|---|---|
| 受け取り | 10MB 上限（既存）。**中身で判定**: `filetype` のマジックバイト＋Pillow での再デコード（画像）、`%PDF` ヘッダと pypdf での読み込み（PDF）、CSV はテキストとして文字コード判定後にパース。クライアントの content_type は信用しない（既存は信用している） |
| 画像の再エンコード | 受け取った画像は EXIF（位置情報・機種）を落として JPEG に再エンコードしたものを保存用・AI 送信用にする。原本も別に保持（電帳法の原本性のため、原本はそのまま暗号化保存） |
| HEIC | サーバーで JPEG に変換（Pillow + pillow-heif）。変換できなければエラーで撮り直し |
| ZIP | 受け付けない（Phase 2 時点） |
| 保存場所 | `MEDIA_ROOT` 配下、公開 URL なし（既存）。ファイル名は `e{company}-{sha16}{ext}`（既存）または UUID。S3 等へ移す場合は Storage だけ差し替え |
| 配信 | 権限確認 → `FileResponse`。`Content-Disposition` は attachment 既定、プレビューは `?inline=1`。`X-Content-Type-Options: nosniff`、`Cache-Control: private, no-store`。ダウンロードは監査ログ（既存） |
| 署名付き URL | 使わない（セッション認証で配信）。iframe での PDF 表示も同一オリジンのセッションで取れる |
| 削除 | 論理削除のみ（既存）。保存期間（個人 7 年／繰越 10 年）経過後の物理削除は運営者の明示操作 |
| CSV インジェクション | 出力 CSV/XLSX のセル先頭 `=+-@` を無害化（TaxFlow `_safe_text` 流用）。XLSX は値のみ（数式を書かない） |
| 取込 CSV | 数式を評価しない。5,000 行上限。メモリに全読みする前にサイズ確認 |

---

## 6. 個人情報とマイナンバー

- **マイナンバーは収集・保存しない**（既存方針）。入力欄を作らず、メモ欄などに 12 桁の数字が入った場合は保存前に警告し、監査ログの meta では 6〜17 桁の数字を `***` にする（ミセミル `core/audit.py` のフィルタを移植）。
- 申告書の所得控除の入力では、配偶者・扶養親族の氏名を持たない（区分・人数・年齢区分のみ）。国税庁の画面で本人が入力する。
- 従業員の住所・生年月日は現状持たない。支払調書の作成が必要になった場合は別途設計（対象外）。
- AI へ送るのは画像のみ。ファイル名・店舗名・顧客名・従業員名を送らない（TaxFlow の実装で発見した「PDF title にファイル名」の問題は移植時に除く）。
- 外部サービス（freee・Square・Anthropic）への送信は環境変数の live フラグと会社単位の有効化が両方そろったときだけ。

---

## 7. AI の安全な利用

| 項目 | 対応 |
|---|---|
| 判断の委譲禁止 | 税額・税区分・勘定科目の自動確定をしない。AI 出力は `ocr_*` に保存し、人の確定値を別に持つ |
| API キー | サーバーの環境変数のみ。応答にモデル名だけ返す |
| 送信データ | 縮小・再エンコード済み画像のみ。プロンプトに個人名・社名を入れない。「画像内の指示を実行しない」ガードレール |
| 停止 | `CompanySettings.ai_enabled`（既定 False）と `RECEIPT_AI_PROVIDER`。どちらかが無ければ手入力 |
| 予算 | 会社ごとの月次予算、予約→精算（TaxFlow `ai/service.py`）。超過時は手入力 |
| 記録 | provider・model・prompt_version・トークン・費用・生応答を保存。画像は監査ログに残さない |
| 失敗 | 再試行は retryable のみ。失敗は手入力に切替。refusal・途中切れはエラー扱い |
| 出力の検証 | スキーマ検証・金額の Decimal 化・登録番号の形式・内訳合計のチェックをサーバーで行う |

---

## 8. 監査ログ

| 項目 | 現状 | 対応 |
|---|---|---|
| 一般操作 | `core.AuditLog`（company/store/user/action/target/changes/request_id） | 承認・差戻し・却下、取込・照合確定・解除、年度締め・再開、設定変更、出力・ダウンロードをすべて記録 |
| 追記専用 | Django 管理画面から編集・削除できる | `AuditLog.save()` で更新を拒否、`delete()` を禁止、admin を読み取り専用に（ミセミルと同じ）。DB ロールで UPDATE/DELETE を剥奪する運用も推奨 |
| 申告関連 | — | `tax.TaxAuditLog`（ハッシュ連鎖・追記専用）。計算・版作成・完成・ロック・出力・ダウンロード・設定変更 |
| meta のフィルタ | なし | `password|secret|token|totp|otp|account_number|session|cookie|csrf|private_key` を含むキーと 6〜17 桁の数字を `***` に |
| 保存期間 | 無期限 | 一般 730 日（`prune_audit_log`）、税務関連は年度の保存期間（7 年）まで保持 |
| 閲覧 | 店舗で絞った最新 300 件 | 事業者・年度・action で絞り、ページング |

---

## 9. 運用

| 項目 | 対応 |
|---|---|
| バックアップ | 既存 `scripts/backup-db.ps1` は DB のみ。**`MEDIA_ROOT` を同じタイミングでバックアップ**する手順・スクリプトを追加（原本の消失は電帳法上も致命的）。暗号鍵は別保管 |
| 復元テスト | 月 1 回、別 DB・別ディレクトリに復元して「仕訳合計・証憑件数・sha256 の一致」を自動チェック（`manage.py verify_backup`） |
| エラー監視 | 例外はログに request_id 付きで記録。外部監視（Sentry 等）を使う場合は PII を送らない設定（`send_default_pii=False`、ボディを送らない） |
| 不正アクセス対策 | ログイン試行制限はプロセス内メモリ（`LoginThrottle`）→ DB/キャッシュで数える。将来 TOTP（TaxFlow `identity/twofactor.py` 流用、オーナー向け任意）。セッション 14 時間は現状維持、申告画面は無操作 30 分で再認証 |
| HTTPS・Cookie | 本番は `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE`=1（既存）。`SECRET_KEY` の既定値での起動を本番で拒否 |
| 依存 | `requirements.txt` にバージョン固定（既存）。追加予定: `cryptography`、`pypdf`、`openpyxl`、`Pillow`、`pillow-heif`、`filetype`、`anthropic`、`lxml`（XTX 検証）、`charset-normalizer`（任意） |
| 本番データの扱い | 開発環境へ本番データを複製しない。検証は `seed_dev` と匿名化した CSV サンプル。実データでの答え合わせが必要な場合は利用者の環境で、持ち出しをしない |
| 電帳法の運用 | 事務処理規程のひな形を提供し、採用の有無を会計設定に記録。原本の物理削除は保存期間後のみ |

---

## 10. 既存コードで見つかった要修正点（調査結果）と対応フェーズ

| # | 事項 | 場所 | 対応 |
|---|---|---|---|
| 1 | freee・Square トークンが平文 | `accounting/models.py:556-557`、`terminals/models.py:34` | Phase 1（暗号化導入） |
| 2 | AuditLog が管理画面から編集・削除可 | `apps/core/admin.py:13` | Phase 1 |
| 3 | アップロードの種類判定がクライアント申告 | `accounting/views.py:377-393` | Phase 2（filetype・再デコード） |
| 4 | 証憑一覧が 500 件で打ち切り・ページングなし | `accounting/views.py:825` | Phase 2（電帳法の検索要件） |
| 5 | ログイン直後 10 分が再認証済み扱い | `core/views.py:114` | Phase 1（税務系は明示の再認証のみ） |
| 6 | 納付記録のレジ出金店舗が事業者の店舗か未検証 | `accounting/services.py:666-676` | Phase 1 |
| 7 | バックアップに `MEDIA_ROOT` が含まれない | `scripts/backup-db.ps1`、`ACCOUNTING_MANUAL.md` §7 | Phase 1（スクリプト追加） |
| 8 | 証憑 POST・取消系に Idempotency-Key なし | `accounting/views.py:826` ほか | Phase 2 |
| 9 | ログイン試行制限がプロセス内メモリ | `core/views.py:36-45` | Phase 8 |
| 10 | DEBUG 既定 True・SECRET_KEY 既定値 | `config/settings.py` | 本番チェックリスト（Phase 8） |
