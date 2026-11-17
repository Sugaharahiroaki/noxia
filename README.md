# NOXIA — 小規模ラウンジ向け店舗運営 Web アプリ

iPad 1台から 入店・注文・会計・延長管理・バック集計・日締め・店舗管理 を行う Web アプリです。
複数の独立した会社が利用し、各会社が複数店舗を持つ前提で設計しています（会社・店舗の境界は API で検証）。

- フロントエンド: TypeScript + Next.js 15 (App Router) + React 19 + Tailwind CSS 4
- バックエンド: Python 3.11 + Django 5.2 LTS + Django REST Framework 3.18
- データベース: PostgreSQL 17（開発用はポータブル版をプロジェクト内に展開。管理者権限不要）
- 日本語 UI / 日本円 / Asia/Tokyo / iPad Safari 横向き優先 / ホーム画面に追加して利用

## ディレクトリ
```
backend/   Django プロジェクト（config/ 設定、apps/ 各機能）
frontend/  Next.js プロジェクト（src/app 画面、src/lib API クライアント等）
scripts/   開発用 PowerShell スクリプト（DB準備・起動・バックアップ・復元）
docs/      未確定ルール、試験手順、給与計算の制限事項、経理設計（ACCOUNTING_*）、会計・確定申告統合（tax_*.md。設計の入口は tax_accounting_audit.md、実装状況は tax_implementation_status.md）
.local/    開発用 PostgreSQL 本体とデータ（Git 管理外）
```

## ローカル起動（Windows）
前提: Python 3.11 以上、Node.js 20 以上（24 で確認）。PowerShell は `-ExecutionPolicy Bypass` で実行してください。

**2回目以降はこれ1本でOK**（DB起動 → Django と Next.js を別ウィンドウで起動 → ブラウザを開く。止めるときは各ウィンドウで Ctrl+C）:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1        # Next.js は本番ビルドで起動
powershell -ExecutionPolicy Bypass -File scripts\start-all.ps1 -Dev   # Next.js を開発モードで起動
```

初回セットアップの手順:

```powershell
# 1. 開発用 PostgreSQL を準備して起動（初回: ダウンロード約330MB → .local/pgsql、initdb → .local/pgdata）
powershell -ExecutionPolicy Bypass -File scripts\setup-dev-db.ps1
#    2回目以降の起動/停止: scripts\start-db.ps1 / scripts\stop-db.ps1

# 2. バックエンド（venv 作成・依存インストール・migrate・runserver 127.0.0.1:8000）
powershell -ExecutionPolicy Bypass -File scripts\dev-backend.ps1

# 3. 開発用データ（別ターミナル。2社×各2店舗、各役割のユーザー、商品・コース・従業員。すべて仮設定）
cd backend
.\.venv\Scripts\python.exe manage.py seed_dev          # 既にあればスキップ
.\.venv\Scripts\python.exe manage.py seed_dev --reset  # DB を全消去して作り直す（開発用DBのみ）

# 4. フロントエンド（npm install → next dev、http://localhost:3000）
powershell -ExecutionPolicy Bypass -File scripts\dev-frontend.ps1
```
ブラウザで http://localhost:3000 を開きます。`/api/*` は Next.js が Django（127.0.0.1:8000）へプロキシするため、
Cookie セッションと CSRF が同一オリジンで動きます。iPad から使う場合は PC の IP で `http://<IP>:3000` を開き、
`backend/.env` の `DJANGO_ALLOWED_HOSTS` と `CSRF_TRUSTED_ORIGINS` に `http://<IP>:3000` を追加してください。

手動で起動する場合:
```powershell
cd backend; .\.venv\Scripts\python.exe manage.py migrate; .\.venv\Scripts\python.exe manage.py runserver 127.0.0.1:8000
cd frontend; npm install; npm run dev
```
Docker を使える環境では、`docker run -e POSTGRES_USER=yorumiru -e POSTGRES_PASSWORD=yorumiru -e POSTGRES_DB=yorumiru -p 5432:5432 postgres:17` でも同じ接続情報で動きます。

### 開発用ログイン（パスワード共通: `Dev-pass-1234`）
| ユーザー名 | 役割 |
|---|---|
| `operator` | 運営者（全社にアクセス可。アクセスは記録される。Django 管理画面 `/django-admin/` も利用可） |
| `owner_a` | A社オーナー（ラウンジA 本店・二号店） |
| `manager_a1` | A本店 店舗管理者（**店舗タブレット用**。メニューは営業の画面だけ。管理・経理・設定は出ない） |
| `staff_a1` | A本店 スタッフ |
| `cast_a1` | A本店 キャスト（従業員「あい」に紐付け。自分の勤怠・成績のみ） |
| `staff_a2` | A二号店 スタッフ |
| `owner_b` | B社オーナー（Bar B・別館） |
| `staff_b1` | Bar B スタッフ |

給与・バック設定などパソコンから行う管理操作は、パスワードの再確認（10分有効）を求めます。店舗のタブレットで行う操作（会計・取消・開店・日締め・締め後の修正・日払い・領収書）は再確認なしで、ログインは使い続ける限り切れません（店舗ごとに 1 アカウントでログインしたままにする運用）。

## 環境変数（backend/.env）
`backend/.env.example` をコピーして使います。

| 変数 | 説明 |
|---|---|
| `DJANGO_SECRET_KEY` | 本番では長いランダム文字列にする |
| `DJANGO_DEBUG` | 本番は `0` |
| `DJANGO_ALLOWED_HOSTS` | 例 `localhost,127.0.0.1,192.168.1.10` |
| `CSRF_TRUSTED_ORIGINS` | フロントエンドのオリジン 例 `http://localhost:3000,http://192.168.1.10:3000` |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | HTTPS 運用時は `1` |
| `REAUTH_VALID_MINUTES` | 再認証の有効時間（既定 10） |
| `SESSION_COOKIE_AGE` | ログインの有効秒数（既定 30 日。使うたびに延びる） |
| `POSTGRES_DB/USER/PASSWORD/HOST/PORT` | 接続情報（既定は開発用 `yorumiru/yorumiru@localhost:5432/yorumiru`） |
| `ALLOW_DEV_SEED` | `1` のとき `seed_dev` を実行できる。本番は `0` |
| `LINE_SEND_ENABLED` | `1` のときだけ LINE へ実送信する。開発・テストは `0`（モック送信） |
| `CARD_TERMINAL_LIVE_ENABLED` | `1` のときだけ本番の決済会社（実際にお金が動く）へ金額を送る。開発・テストは `0`（模擬端末・Square Sandbox は使える） |
| `CARD_TERMINAL_MOCK_ENABLED` | 模擬端末（画面で承認／拒否を選ぶテスト用）を使えるか。未指定なら `DJANGO_DEBUG` と同じ。本番は `0` |
| `FREEE_LIVE_ENABLED` | `1` のときだけ本番の freee 会計へ仕訳を送る。開発・テストは `0`（模擬接続は使える） |
| `FREEE_CLIENT_ID` / `FREEE_CLIENT_SECRET` / `FREEE_REDIRECT_URI` | freee のアプリ登録情報。戻り先は `https://<ドメイン>/accounting/freee` |
| `MEDIA_ROOT` | 証憑・契約書ファイルの保存先（既定 `backend/media`。公開URLは作らない。バックアップ対象に含める） |
| `ACCOUNTING_EXTRA_HOLIDAYS` | 納期限の計算で休日として扱う臨時の日（`YYYY-MM-DD` をカンマ区切り） |

フロントエンドは `BACKEND_URL`（既定 `http://127.0.0.1:8000`）で Django の場所を変えられます。

## テスト
```powershell
cd backend
$env:PYTHONUTF8="1"; .\.venv\Scripts\python.exe manage.py test apps -v 1   # PostgreSQL 上に test_yorumiru を自動作成
cd ..\frontend
npm run typecheck   # TypeScript 型検査
npm run build       # 本番ビルド
```
テスト内容: 料金計算（1時間2,500円＋延長で5,000円、無制限3,500円、無制限変更の設定料金、サービス料・値引き・税・端数）、
時刻境界・日付またぎ・再読み込み後のアラーム、現金＋カード併用・釣銭・分割会計、同時操作・再送での二重登録防止、
取消・返金とバック・在庫・現金の整合、バックの固定額/割合/個人別/複数人分配、料金・時給変更が過去に影響しないこと、
日払いの二重控除防止、日締め・現金差額・取り分け・銀行入金、権限外アクセスの拒否、会社・店舗境界（ID差し替え、検索・CSV・集計）、LINE モック送信、
決済端末（模擬端末での承認・拒否・取消、処理中の手入力会計の禁止、会計の1回だけ確定、Square への送信内容・本番送信の停止・通知の署名と重複排除）。

## バックアップ・復元
```powershell
powershell -ExecutionPolicy Bypass -File scripts\backup-db.ps1                       # backups/yorumiru-日時.dump
powershell -ExecutionPolicy Bypass -File scripts\restore-db.ps1 -File backups\xxx.dump # 既存データを上書き（確認あり）
```
`-DbUrl "postgresql://user:pass@host:5432/db"` で本番 DB も指定できます。本番では毎営業日の締め後に自動実行する運用を推奨します。

## 本番サーバー（Ubuntu の VPS）と自動反映
**GitHub の `main` に push すると、サーバーが 1 分以内に気づいて自動で反映する**（サーバーから取りに行く方式。GitHub に鍵を預けない）。
仕組みは `deploy/`: `deploy.sh`（反映）、`setup-server.sh`（最初の準備）、`systemd/`（常駐サービス・タイマー）、`nginx/`。

- 反映の流れ: `releases/日時-コミット` に展開 → 依存を入れる → `migrate` → Next.js をビルド → `current` を付け替えて再起動 → 応答を確認。
  途中で失敗したら画面は前の版のまま。再起動後に応答しなければ前の版に戻す（**DB の migrate は戻らない**ので、列の削除などは 2 回に分けて出す）。
  失敗したコミットは再試行しない。直して push するか、サーバーで `sudo -u noxia bash /srv/noxia/repo/deploy/deploy.sh --force`。
- 反映の記録: `journalctl -u noxia-deploy -f`。アプリのログ: `journalctl -u noxia-backend -u noxia-frontend -u noxia-jobs -f`
- バックアップ: 毎朝 7 時に DB と添付ファイルを `/srv/noxia/backups` に（14 日分）。サーバーの外への保管は別途。
- 設定: `/srv/noxia/shared/.env`（`setup-server.sh` が秘密鍵を生成）。`DJANGO_SECRET_KEY` と `FIELD_ENCRYPTION_KEY_V1` は必ずサーバーの外にも控える。

最初の準備（前提: PostgreSQL 17、DB とロール `noxia`、システムユーザー `noxia`、`/opt/python` の Python 3.11、uv、nginx、ufw で 80/443 許可）:
```bash
curl -fsSLO https://raw.githubusercontent.com/Sugaharahiroaki/noxia/main/deploy/setup-server.sh
sudo bash setup-server.sh <ドメイン>          # ドメインが無いお試しは IP アドレス（HTTP のみ）
sudo apt -y install certbot python3-certbot-nginx && sudo certbot --nginx -d <ドメイン>   # HTTPS 化
```
運営者アカウントの作成（最初の 1 回。`seed_dev` は本番では使えない）:
```bash
# /srv/noxia は noxia だけが入れるので、noxia として移動してから実行する
sudo -u noxia -H bash -c 'cd /srv/noxia/current/backend && /srv/noxia/venv/bin/python manage.py createsuperuser'
sudo -u noxia -H bash -c 'cd /srv/noxia/current/backend && /srv/noxia/venv/bin/python manage.py shell -c "from django.contrib.auth import get_user_model as g; print(g().objects.filter(is_superuser=True).update(is_platform_operator=True))"'
```

## アプリアイコン・ロゴ
`frontend/public/` に置いています。差し替えるときは同じファイル名・サイズで上書きしてください。
| ファイル | 用途 |
|---|---|
| `apple-touch-icon.png`（180×180、角まで塗りつぶし） | iPad の「ホーム画面に追加」（角は iPad 側で丸められる） |
| `icon-192.png` / `icon-512.png`（角丸・透明） | PC の Chrome / Edge で「アプリとしてインストール」したとき・ブラウザのタブ |
| `icon-maskable-512.png`（余白あり） | Android など、端末側で形を切り抜く場合 |
| `favicon.ico` | ブラウザのタブ |
| `logo-mark.png` | アプリ内の上部バー（横向き・PC のときだけ小さく表示） |

PC へのインストールは HTTPS（または localhost）のときだけ表示されます。

## 運用の始め方（1 店舗・アカウント 2 つ）
会社 → 店舗 → アカウント（会社ごとに別。別会社のデータは見えない）の構造は最初からあるので、まず 1 店舗で始めて、あとから店舗を足せます。
1. 運営者（`operator`）が「運営者: 会社・店舗管理」で会社と店舗を 1 つ作り、オーナーのアカウント（会社オーナー）を作る。
2. オーナーが PC でログイン → メニュー「会社名（全店）」→ 招待 で、タブレット用のアカウントを作る（店舗権限: 店舗管理者。招待リンクをタブレットで開いてパスワードを決める）。
3. PC の「店舗設定・権限設定」→「タブレット」で、そのアカウントに「タブレット用」の印を付ける。タブレットに出す画面は既定で営業の画面だけ
   （卓一覧・入店・開店と日締めと現金・売上と会計履歴・領収書と経費・出勤と日払い）。チェックで増やせる。
4. タブレットではそのアカウントでログインしたままにする（営業の操作は再認証なし）。管理・経理・設定は PC でオーナーのアカウントから。
5. 2 店舗目以降: 「会社名（全店）」→「＋ 店舗を追加」。新しい店舗のタブレット用アカウントを招待して（または既存のアカウントをその店舗に配属して）、同じように印を付ける。
   商品・卓・料金・設定は店舗ごとに別。顧客は会社の設定で共有できる。

## 画面一覧
全画面、店舗タブレットと同じ明るい配色（クリーム＋金）。
店舗タブレット POS（iPad 横向き。営業ヘッダー）:
ホール（卓一覧・長押しで伝票削除／テーブル移動／時間変更） / 入店ウィザード（セット構成 → 人数 → 客ごとのキャストと本指名・場内・同伴 → 時間と金額） /
卓詳細（タグ・人数の増減・セットの時系列・延長の候補） / 注文（4 列・キャスト折半） / 明細 / 会計 / POS 設定（卓・セット構成・カテゴリー・商品・タグ・料金・アラート・料金計算） /
ログイン / 店舗選択 / 手書き領収書への転記用表示 /
開店・日締め・現金管理 / 売上・会計履歴（未収・取消・返金） / 従業員管理（時給・ログイン） / 時給・バック・料金設定 /
勤怠・支払集計 / 顧客・ボトルキープ / 商品・在庫・棚卸し（商品マスタ） / 仕入先・発注 / 予約・LINE連携 /
経営ダッシュボード（CSV出力） / 店舗設定・権限設定（卓・権限表・操作履歴） / 会社管理（全店集計・店舗・招待） / 運営者管理 /
集計（管理用。メニュー「集計」= `/reports`。見るだけで、金額は変更しない）: 収支（月次） / 営業日報（日次） / 伝票一覧・伝票詳細（キャスト別の売上小計） /
タグ集計 / 商品集計（日付×商品、キャスト×商品） / お客様分析（来店回数・単価・ランク） / レジ精算（月間） / キャスト集計・スタッフ集計 /
報酬支払明細（1 人・1 か月。PDF 出力。キャスト本人は自分の分だけ） / 履歴（入出金の履歴、伝票の削除履歴）。各表は XLSX で出力できる /
入出金項目・名称変更（設定。その他入金・その他出金を分ける項目と、POS・集計で使う用語の言い換え） /
領収書の登録（撮影→確認→支払方法→登録）・自店舗の状態一覧 /
経理: 会計ダッシュボード / 経費・領収書の承認 / 銀行 CSV 取込・照合 / 帳簿（元帳・現金・預金・売上・売掛・買掛・経費・スタッフ支払・試算表） / 固定資産・棚卸 / 年間決算 /
確定申告（集計・控除入力・税額計算・版・PDF/XLSX） / 国税庁 作成コーナー 入力ガイド / 源泉所得税管理 / 月次損益・証憑 / freee 連携 / キャスト契約・税務区分 / 経理設定（事業者・権限・AI 読取・申告情報）

## 設計の要点
- **テナント**: `Company` → `Store`、`User` ↔ `CompanyMembership`（会社内権限） / `StoreMembership`（店舗内権限）。
  店舗データは必ず `store` を持ち、リクエストの `X-Store-Id` は所属と照合してから使う（`apps/core/permissions.py`）。
  関連付ける従業員・商品・伝票などの所属も検証する（`StorePK`、各サービス）。運営者（`is_platform_operator`）のアクセスは `OperatorAccessLog` に記録。
- **権限**: ケイパビリティ（`floor.operate` など）を役割ごとに定義し API で検証。給与・設定・税務など管理操作は再認証必須（`REAUTH_CAPS`）。店舗の操作は再認証なし。
- **金額**: Django 側で `Decimal` 計算（`apps/sales/pricing.py`）。価格・税率・バック条件は取引時点を明細に保存。合計の丸めは「端数調整」として保存し税額は変えない。
- **二重登録防止**: `Idempotency-Key`（`apps/core/idempotency.py`）＋ 行ロック ＋ 一意制約。処理中の再送は 409 を返し、クライアントは状態確認を促す。
- **アラーム**: 保存された終了予定時刻と現在時刻から毎回再計算（サーバー `apps/sales/alarms.py`、クライアント `useServerClock.ts`）。
- **営業日**: 店舗設定の切替時刻で実日時から営業日を決め、未締めの営業日があればそれに計上する。
- **LINE**: 接続情報は店舗別にサーバー側で保持。認証情報なし／`LINE_SEND_ENABLED=0` ではモック送信。Webhook は署名検証と `webhookEventId` による重複排除。
- **カード決済端末**（`apps/terminals`）: NOXIA のサーバー → 決済会社 → 店の端末、の順に金額を送る（クラウド連携）。端末を PC・iPad に直接つながないので、どの画面からでも同じように使える。
  決済会社ごとの違いは `providers.py` に閉じ込め、いまは「模擬端末（テスト用）」と「Square ターミナル（Terminal API）」に対応。
  送った時点の明細と金額を記録し、端末で承認されたらその明細で会計を1回だけ確定する（送信後に追加した注文は伝票に残る）。
  処理中は手入力の会計・送った明細の取消を止めて二重請求を防ぐ。結果は会計画面の数秒おきの確認と、Square の通知（署名検証・重複排除）の両方で受け取る。
  カード番号は NOXIA に届かず保存もしない。アクセストークンはサーバー側だけで保持し、画面には末尾4文字だけ返す。本番への送信は `CARD_TERMINAL_LIVE_ENABLED=1` のときだけ。
- **PC での利用**: タッチと同じ画面をキーボードでも操作できる。テンキー（お預かり・現金を数える等）は数字キー・Backspace・Delete・Enter、
  会計は Enter（確認画面でもう一度 Enter で確定）、現金＋カードは Tab で欄を切り替え。キーボードの案内はマウス・トラックパッドのある画面にだけ表示する。

## 既知の制限・未対応
- オフラインでの会計は未対応（通信断では成功扱いにせず、再接続後に画面更新で状態を照合）。
- ロック中・他アプリ利用中の終了通知は保証しない（Web アプリの制約）。
- 給与の法定控除・割増賃金は未対応（`docs/PAYROLL_LIMITATIONS.md`）。ホステス報酬の源泉所得税は計算する（給与所得の税額表による計算はしない）。
- 所得税徴収高計算書は転記用（独自形式）。e-Tax への取込・送信・納付は経営者ご本人が行う（NOXIA は認証情報を預からない）。
- 仕入先への自動発注・LINE 実送信は、接続方法と権限が確定するまで無効。
- カード決済端末は模擬端末と Square ターミナルに対応（Square の実機・Sandbox での接続確認は未実施）。本番の決済会社への送信は `CARD_TERMINAL_LIVE_ENABLED=1` にするまで止めてある。
  Airペイなど公開 API が見つからない決済会社は未対応（その場合は端末に手入力し、会計画面で記録する従来の方法を使う）。
- 実機 iPad Safari での検証は未実施（Chromium で iPad mini 横向き相当 1133×744 と 1180×820 を確認）。タッチの長押し・ソフトキーボードを出したときの入力欄は実機で確認が必要。
- 店舗タブレット POS で未対応のもの（オーナーの確認待ち。`docs/unified/UNIFIED_PLAN.md` 6 章）:
  支払方法別のサービス料率（現金とカードで率を変える）・「C 利用料率」、値増し、テーブル結合、「自動」延長、「出戻り」、折半のポイント、
  印刷（概算・会計・精算伝票、領収書、キッチン印刷）と注文チャイム、ヘッダーの「トップ」。待機キャストは「出勤中で卓に付いていない人」の一覧だけ。
- 客の退店（人−）は料金を変えない（開始済みのセットは減らさず、退店後の延長だけ在席人数で計算）。退店の取り消しは未対応。入店ウィザードより前に作った伝票・予約からの入店は、1 人ずつの退店を記録できない。
- キャスト折半はバックの配分を手入力する（バックの元の額をルールから自動で出して割り振る機能は無い。バックのルールが無い商品にはバックが付かない）。
- 伝票 ID・会計 ID は 12 文字のランダムな英数字（`K7QM-2XRA-9BHT` の形で表示）。集計・CSV・XLSX に出す。POS の画面の「伝票#」は営業日ごとの通し番号のまま。
- 集計（`/reports`）の仮の扱い（オーナーの確認待ち。`docs/unified/PROGRESS.md`）: 1 つの伝票に指名のキャストが複数いるときの売上の分け方（既定は均等割り）、
  金額指定の折半の商品集計での分け方（均等割り）、お客様のランクのしきい値（店舗ごとに設定。未設定の店舗はランクを出さない）、入出金項目と「粗利に含める」。
- 入出金項目は集計（粗利）と表示にだけ使う。レジの理論現金と仕訳は、項目に関係なくこれまでどおり種別（その他入金・その他出金 など）で決まる。現金に含めない項目は作れない。
- 店舗の方針として、PC の「売上・会計履歴」から暗証番号で取り消した会計は、操作履歴にも削除履歴にも残さない（「お客様に全額返す」で取消になった伝票は、取消済みの一覧・来店履歴にも出さない）。
  卓からの伝票・明細の取消は、これまでどおり操作履歴と削除履歴に残る。操作履歴の操作・対象・内容は日本語で表示する（`apps/core/audit_labels.py`）。
- 日払い＋締め払い: 契約（経理 → キャスト契約）の支払方法を「日払い＋締め払い」にして 1日の日払いの額（例: 10,000 円）を入れると、
  「出勤・お渡し」の「渡した」はその額だけをレジから出し、残りは「支払集計・締め払い」の残額（締めで渡す額）になる。その日の報酬が日払いの額より少ない日は全額を渡す。
  締め払いは振込かレジ以外の現金で記録し、集計期間の最終日の分として記録する（レジの現金から締め払いを出す操作は無い）。源泉所得税は店舗設定でオフ（既定）。
- タブレット用アカウントの画面の絞り込みは、メニューと画面の移動の出し分け（`apps/core/tablet.py`、`lib/tablet.ts`）。API の権限は役割（店舗管理者など）のまま。
- 名称変更は POS・集計画面・出力の見出しの言い換えだけ。伝票の明細に保存済みの名前（「指名料（名前）」など）、領収書、経理・税務の画面、一部の説明文・通知は変わらない。
- 集計で未対応のもの: 印刷履歴、日報登録（勤怠・入出金・メモを 1 画面で入れる画面）、お客様のカスタム項目・属性・宛名、参考設計の「状態・タイプ・金種」の列。
  XLSX は伝票一覧の絞り込み（支払方法・値引き・取消）を反映せず、期間内のすべてを出す。

## 経理（源泉所得税・キャスト精算・freee 連携）
- `apps/accounting`: 事業者（法人・個人事業主）→ 店舗、キャスト契約と税務区分（経営者が実態を確認して確定）、源泉税計算エンジン、日払いとの統合、月次の集計・経営者確認・納付記録、徴収高計算書の転記用データ・PDF、仕訳と freee 連携、証憑・経費、記帳代行・税理士の権限。
- 税率・控除額は日付付きのルール表（支払日で選択）。控除額（5,000円×暦日数）は事業者×キャスト×日の台帳で重複を防ぐ。
- 設計: `docs/ACCOUNTING_DESIGN.md`、運用: `docs/ACCOUNTING_MANUAL.md`、法令・税務上の要確認事項: `docs/TAX_OPEN_QUESTIONS.md`。
- 開発用ログインの追加: `bookkeeper_dev`（A社の記帳代行担当者）、`zeirishi_dev`（A社の税理士等）。

## 会計・帳簿・年間決算・確定申告（2026-11 追加）
- 複式簿記の基盤（勘定科目・仕訳行・年度の締め）を `apps/accounting`（`books.py`・`ledgers.py`）に追加し、帳簿はすべて仕訳行からの集計（帳簿テーブルは持たない）。
- `apps/expenses`: 店舗タブレットで領収書を撮って登録 → AI 読取（既定は mock。Anthropic は会社設定でオンにし、サーバーの `ANTHROPIC_API_KEY` が必要）→ オーナー・経理が原本と並べて承認。店舗現金払いはレジ出金に、それ以外は経費・買掛金に入る。重複検出あり。
- `apps/banking`: 銀行 CSV の取込（列対応を学習）と入出金の照合、カード入金の自動作成。
- `apps/closing`: 棚卸評価・固定資産と減価償却・開業費・決算整理・年間損益・年度締め。
- `apps/tax`: 個人事業主（白色・青色 10 万円控除）の所得税の集計と計算（年分ごとの税制 JSON）、作成コーナーへの転記ガイド、転記用 PDF/XLSX。**e-Tax への送信はしない。** 2026-11-12 の方針: 電子申告は各オーナーが自分の freee で行い、NOXIA は承認済みの仕訳を freee に渡すまで。XTX 生成は着手しない（枠だけ残し `TAX_XTX_RELEASED_YEARS` は空のまま）。
- 非同期ジョブ（AI 読取・CSV 解析）は DB キュー。開発では `scripts\dev-jobs.ps1`（`start-all.ps1` が一緒に起動）、本番では `manage.py run_jobs` を常駐か 1 分ごとに実行。
- 機密データ（申告の入力・連携トークン）は `FIELD_ENCRYPTION_KEY_V1` で暗号化。本番では未設定だと起動しない。
- freee で申告する事業者: 会計設定の「申告方法」を freee にすると、銀行照合・固定資産・年間決算・確定申告のメニューを出さず、freee 連携画面の「freee 取込用 CSV」で仕訳を渡す（freee の「他社会計ソフトインポート」で取込。出力済みの印で二重取込を防ぐ）。API での自動送信は運営者のアプリ登録後。
- 実装状況・テスト結果・公式確認待ちの一覧: `docs/tax_implementation_status.md`。利用者向け: `docs/tax_user_manual.md`。
- 検証用の環境（開発用 DB を壊さない）: `scripts\verify-env.ps1 -Reset` で検証用 DB・Django 8001・ジョブ・Next 3001 を起動し、`.local\e2e\*.mjs` を `BASE_URL=http://localhost:3001` で実行。`-Stop` で停止。

詳細は `docs/UNDECIDED_RULES.md`（オーナーが確定すべき設定）、`docs/TEST_PROCEDURE.md`（実店舗での試験手順）を参照してください。
