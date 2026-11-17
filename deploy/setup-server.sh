#!/usr/bin/env bash
# NOXIA の本番サーバーを準備する（最初の 1 回だけ。ドメインを後から付けたときも、もう一度実行してよい）。
#
#   sudo bash setup-server.sh <ドメイン または IP アドレス>
#   例) sudo bash setup-server.sh pos.example.com
#       sudo bash setup-server.sh 49.212.223.140     ← ドメインがまだ無いときのお試し（HTTP のみ）
#
# 前提（済んでいること）: Ubuntu、PostgreSQL 17、DB とロール noxia、システムユーザー noxia（/srv/noxia）、
#                        uv と Python 3.11（/opt/python）、nginx、ufw で 80/443 を許可
# このあとは GitHub の main に push するだけで、1 分以内に自動で反映される（noxia-deploy.timer）。
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "sudo を付けて実行してください: sudo bash $0 <ドメイン>"; exit 1; }
HOST="${1:?使い方: sudo bash setup-server.sh <ドメイン または IP アドレス>}"
REPO_URL="${NOXIA_REPO_URL:-https://github.com/Sugaharahiroaki/noxia.git}"
BRANCH="${NOXIA_BRANCH:-main}"
APP=/srv/noxia

if [[ "$HOST" =~ ^[0-9.]+$ ]]; then SCHEME=http; else SCHEME=https; fi
step() { echo; echo "=== $* ==="; }

step "必要なソフトを確認"
apt-get install -y -qq nodejs npm git nginx curl >/dev/null
command -v pg_dump >/dev/null || { echo "PostgreSQL が見つかりません"; exit 1; }
command -v uv >/dev/null || { echo "uv が見つかりません（/usr/local/bin/uv）"; exit 1; }
id noxia >/dev/null 2>&1 || adduser --system --group --home "$APP" --shell /bin/bash noxia
sudo -u noxia psql -d noxia -c 'select 1' >/dev/null || { echo "noxia ユーザーで DB noxia に入れません（createuser / createdb を確認）"; exit 1; }

step "フォルダとソースの取得"
install -d -o noxia -g noxia -m 750 "$APP" "$APP/shared" "$APP/shared/media" "$APP/releases" "$APP/backups"
# 以降の noxia としての実行が、ubuntu のホーム（noxia は読めない）を作業フォルダにしないように
cd "$APP"
if [[ -d "$APP/repo/.git" ]]; then
  sudo -u noxia git -C "$APP/repo" fetch --quiet origin "$BRANCH"
  sudo -u noxia git -C "$APP/repo" reset --quiet --hard "origin/$BRANCH"
else
  sudo -u noxia git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP/repo"
fi

step "Python 3.11 の環境"
if [[ ! -x "$APP/venv/bin/python" ]]; then
  PY311=$(ls -d /opt/python/cpython-3.11.*-linux-x86_64-gnu 2>/dev/null | sort -V | tail -1)
  [[ -n "$PY311" ]] || { echo "Python 3.11 が見つかりません: sudo env UV_PYTHON_INSTALL_DIR=/opt/python uv python install 3.11"; exit 1; }
  sudo -u noxia uv venv --no-config --quiet --python "$PY311/bin/python3.11" "$APP/venv"
fi
"$APP/venv/bin/python" --version

step "設定ファイル（$APP/shared/.env）"
ENV="$APP/shared/.env"
if [[ ! -f "$ENV" ]]; then
  SECRET=$(python3 -c 'import secrets;print(secrets.token_urlsafe(60))')
  ENCKEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
  cat > "$ENV" <<EOF
# NOXIA 本番の設定（setup-server.sh が作成）。鍵 2 つは必ずサーバーの外にも控えておく。
# FIELD_ENCRYPTION_KEY_V1 を失うと、暗号化した連携トークン・申告データ・原本ファイルが読めなくなる。
DJANGO_SECRET_KEY=$SECRET
FIELD_ENCRYPTION_KEY_V1=$ENCKEY
DJANGO_DEBUG=0
DEV_ALLOW_LAN=0
LOG_LEVEL=INFO

# DB: サーバー内の noxia ユーザーとしてだけ入る（パスワードなし・外からは入れない）
POSTGRES_DB=noxia
POSTGRES_USER=noxia
POSTGRES_PASSWORD=
POSTGRES_HOST=/var/run/postgresql
POSTGRES_PORT=5432

MEDIA_ROOT=$APP/shared/media
ALLOW_DEV_SEED=0
LINE_SEND_ENABLED=0
CARD_TERMINAL_LIVE_ENABLED=0
CARD_TERMINAL_MOCK_ENABLED=0
FREEE_LIVE_ENABLED=0
RECEIPT_AI_PROVIDER=mock
EOF
  echo "新しく作成しました"
else
  echo "既存のものを使います（鍵は変えません）"
fi

# ドメイン（または IP）で変わる値だけ、毎回書き換える
set_env() {
  if grep -q "^$1=" "$ENV"; then sed -i "s|^$1=.*|$1=$2|" "$ENV"; else echo "$1=$2" >> "$ENV"; fi
}
set_env DJANGO_ALLOWED_HOSTS "$HOST,127.0.0.1,localhost"
set_env CSRF_TRUSTED_ORIGINS "$SCHEME://$HOST"
if [[ $SCHEME == https ]]; then
  set_env SESSION_COOKIE_SECURE 1; set_env CSRF_COOKIE_SECURE 1
  set_env FREEE_REDIRECT_URI "https://$HOST/accounting/freee"
else
  set_env SESSION_COOKIE_SECURE 0; set_env CSRF_COOKIE_SECURE 0
fi
chown noxia:noxia "$ENV"; chmod 600 "$ENV"

step "サービスの登録（systemd）"
cp "$APP"/repo/deploy/systemd/noxia-*.service "$APP"/repo/deploy/systemd/noxia-*.timer /etc/systemd/system/
sed -i "s|^Environment=NOXIA_BRANCH=.*|Environment=NOXIA_BRANCH=$BRANCH|" /etc/systemd/system/noxia-deploy.service
cat > /etc/sudoers.d/noxia <<'EOF'
# 自動反映（deploy.sh）が NOXIA のサービスだけを再起動できるようにする
noxia ALL=(root) NOPASSWD: /usr/bin/systemctl restart noxia-backend noxia-frontend noxia-jobs
EOF
chmod 440 /etc/sudoers.d/noxia
visudo -cf /etc/sudoers.d/noxia >/dev/null
systemctl daemon-reload
systemctl enable --quiet noxia-backend noxia-frontend noxia-jobs
systemctl enable --quiet --now noxia-backup.timer

step "nginx"
sed "s|__SERVER_NAME__|$HOST|" "$APP/repo/deploy/nginx/noxia.conf" > /etc/nginx/sites-available/noxia
# certbot が HTTPS を追記済みなら上書きしない
if [[ -f /etc/nginx/sites-enabled/noxia ]] && grep -q "managed by Certbot" /etc/nginx/sites-enabled/noxia 2>/dev/null; then
  echo "HTTPS 設定済みのため nginx の設定は変更しません"
else
  ln -sfn /etc/nginx/sites-available/noxia /etc/nginx/sites-enabled/noxia
fi
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl reload nginx

step "初回の反映（数分かかります）"
sudo -u noxia env NOXIA_BRANCH="$BRANCH" bash "$APP/repo/deploy/deploy.sh" --force
systemctl enable --quiet --now noxia-deploy.timer

step "完了"
echo "画面: $SCHEME://$HOST/"
echo "自動反映: GitHub の $BRANCH に push すると 1 分以内に反映（記録: journalctl -u noxia-deploy -f）"
echo "バックアップ: 毎朝 7 時に $APP/backups（14 日分）"
echo
echo "★ $ENV の DJANGO_SECRET_KEY と FIELD_ENCRYPTION_KEY_V1 を、パスワード管理ソフトなどサーバーの外に控えてください。"
if [[ $SCHEME == https ]]; then
  echo "★ HTTPS 化がまだなら: sudo apt -y install certbot python3-certbot-nginx && sudo certbot --nginx -d $HOST"
else
  echo "★ いまは HTTP（暗号化なし）です。お試しだけにして、ドメインを用意したら sudo bash $0 <ドメイン> を実行してください。"
fi
