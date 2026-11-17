#!/usr/bin/env bash
# GitHub の main（NOXIA_BRANCH）の最新を取り込んで本番に反映する。
# noxia ユーザーで実行する。noxia-deploy.timer が 1 分ごとに呼ぶ。
#
#   deploy.sh           新しいコミットがあるときだけ反映する
#   deploy.sh --force   同じコミットでも、前回失敗したコミットでも、もう一度反映する
#
# 流れ: 取得 → 新しいフォルダ（releases/日時-コミット）に展開 → Python・Node の依存を入れる
#       → migrate → Next.js をビルド → current を新しいフォルダに付け替え → 再起動 → 動作確認
# 途中で失敗したら current は前のまま（画面は止まらない）。再起動後の確認に失敗したら前の版に戻す。
# 同じコミットで何度も失敗しないよう、失敗したコミットは failed-revision に記録して次の push まで待つ。
set -euo pipefail

APP=/srv/noxia
REPO="$APP/repo"
BRANCH="${NOXIA_BRANCH:-main}"
VENV="$APP/venv"
KEEP_RELEASES=5
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

log() { echo "[deploy] $*"; }
cd "$APP"  # 呼び出し元の作業フォルダ（noxia が読めない場所のことがある）に依存しない

# 二重起動を防ぐ（反映に 1 分以上かかっても、次の timer は何もせず終わる）
if [[ -z "${NOXIA_DEPLOY_LOCKED:-}" ]]; then
  exec 9>"$APP/deploy.lock"
  flock -n 9 || { log "別の反映が実行中のため終了"; exit 0; }
  export NOXIA_DEPLOY_LOCKED=1
fi

git -C "$REPO" fetch --quiet origin "$BRANCH"
NEW=$(git -C "$REPO" rev-parse "origin/$BRANCH")

# このスクリプト自体も更新されうるので、手元の repo を最新にしてから新しい版で実行し直す
if [[ "$(git -C "$REPO" rev-parse HEAD)" != "$NEW" ]]; then
  git -C "$REPO" reset --quiet --hard "$NEW"
  exec bash "$REPO/deploy/deploy.sh" "$@"
fi

CUR=$(cat "$APP/current/REVISION" 2>/dev/null || true)
if [[ $FORCE -eq 0 ]]; then
  [[ "$NEW" == "$CUR" ]] && exit 0
  if [[ -f "$APP/failed-revision" && "$(cat "$APP/failed-revision")" == "$NEW" ]]; then
    exit 0  # 前回失敗したコミット。直したコミットを push するか --force で再実行する
  fi
fi

SHORT=${NEW:0:7}
REL="$APP/releases/$(date +%Y%m%d-%H%M%S)-$SHORT"
PREV=$(readlink -f "$APP/current" 2>/dev/null || true)
log "反映を開始: $SHORT（$(git -C "$REPO" log -1 --format=%s "$NEW")）"

fail() {
  log "失敗: $SHORT — 画面は前の版のまま動いています。直して push するか、deploy.sh --force で再実行してください"
  echo "$NEW" > "$APP/failed-revision"
  [[ -n "${REL:-}" && "$REL" != "$PREV" ]] && rm -rf "$REL"
  exit 1
}
trap fail ERR

mkdir -p "$REL"
git -C "$REPO" archive "$NEW" | tar -x -C "$REL"
echo "$NEW" > "$REL/REVISION"
ln -s "$APP/shared/.env" "$REL/backend/.env"

# --- バックエンド ---
log "Python の依存を入れています"
uv pip install --no-config --quiet --python "$VENV/bin/python" -r "$REL/backend/requirements.txt"
cd "$REL/backend"
"$VENV/bin/python" manage.py check --fail-level ERROR
log "DB を更新しています（migrate）"
"$VENV/bin/python" manage.py migrate --noinput
"$VENV/bin/python" manage.py seed_tax_config  # 年度別の税制設定（まだ無い年度だけ追加）

# --- フロントエンド ---
log "Next.js をビルドしています"
cd "$REL/frontend"
export NEXT_TELEMETRY_DISABLED=1
npm ci --no-audit --no-fund --loglevel=error
npm run build

# --- 切り替え・再起動 ---
trap - ERR
ln -sfn "$REL" "$APP/current.tmp"
mv -T "$APP/current.tmp" "$APP/current"
sudo /usr/bin/systemctl restart noxia-backend noxia-frontend noxia-jobs

healthy() {
  local i code_api code_web
  for i in $(seq 1 30); do
    sleep 2
    code_api=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/api/ || true)
    code_web=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ || true)
    # 応答があり 500 番台でなければ起動できている（未ログインの 401/403/404 や転送の 307 は正常）
    if [[ "$code_api" =~ ^[1-4] && "$code_web" =~ ^[1-4] ]]; then return 0; fi
  done
  log "動作確認に失敗（API: $code_api / 画面: $code_web）"
  return 1
}

if healthy; then
  rm -f "$APP/failed-revision"
  log "反映しました: $SHORT"
else
  echo "$NEW" > "$APP/failed-revision"
  if [[ -n "$PREV" && -d "$PREV" ]]; then
    ln -sfn "$PREV" "$APP/current.tmp"
    mv -T "$APP/current.tmp" "$APP/current"
    sudo /usr/bin/systemctl restart noxia-backend noxia-frontend noxia-jobs
    log "前の版（$(cat "$PREV/REVISION" | cut -c1-7)）に戻しました。DB の migrate は戻していません"
  fi
  exit 1
fi

# 古い版を消す（新しい順に KEEP_RELEASES 個残す。current は必ず残る）
ls -1dt "$APP"/releases/*/ 2>/dev/null | tail -n +$((KEEP_RELEASES + 1)) | while read -r old; do
  [[ "$(readlink -f "$old")" == "$(readlink -f "$APP/current")" ]] || rm -rf "$old"
done
