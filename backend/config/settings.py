"""
yorumiru backend settings.

環境変数は backend/.env から読み込む（.env.example を参照）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-secret-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
# Next.js(開発: http://localhost:3000) から /api をプロキシするため、Origin を信頼する
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")


def _lan_ipv4_addresses() -> list[str]:
    """この PC のプライベート IPv4 アドレス（同じ Wi-Fi の iPad から開くため）。"""
    import ipaddress
    import socket

    found: set[str] = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            found.add(ip)
    except OSError:
        pass
    try:
        # 既定ルートに使うインターフェースの IP（実際には送信しない）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    result = []
    for ip in sorted(found):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.is_private and not addr.is_loopback and not addr.is_link_local:
            result.append(ip)
    return result


# 開発時のみ: 同じ LAN の端末（iPad など）から http://<PCのIP>:3000 で開けるようにする。本番は DEV_ALLOW_LAN=0。
if DEBUG and env_bool("DEV_ALLOW_LAN", True):
    _front_port = os.environ.get("FRONTEND_PORT", "3000")
    for _ip in _lan_ipv4_addresses():
        if _ip not in ALLOWED_HOSTS:
            ALLOWED_HOSTS.append(_ip)
        _origin = f"http://{_ip}:{_front_port}"
        if _origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_origin)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.catalog",
    "apps.staff",
    "apps.sales",
    "apps.cash",
    "apps.customers",
    "apps.inventory",
    "apps.line_integration",
    "apps.terminals",
    "apps.analytics",
    "apps.accounting",
    "apps.expenses",
    "apps.banking",
    "apps.closing",
    "apps.tax",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestIdMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "yorumiru"),
        "USER": os.environ.get("POSTGRES_USER", "yorumiru"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "yorumiru"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "TEST": {"NAME": os.environ.get("POSTGRES_TEST_DB", "test_yorumiru")},
    }
}

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- セッション / CSRF ---
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", False)
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", str(60 * 60 * 24 * 30)))  # 30日。使うたびに延びるので、店舗のタブレットはログインしたままにできる
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = False  # フロントエンドがヘッダに載せるため読める必要がある
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", False)
CSRF_USE_SESSIONS = False

# 共有iPadで機微な管理操作（給与等）を行う前に再認証を求める有効時間（分）
REAUTH_VALID_MINUTES = int(os.environ.get("REAUTH_VALID_MINUTES", "10"))

# 開発用シードデータの投入を許可するか（本番では必ず false）
ALLOW_DEV_SEED = env_bool("ALLOW_DEV_SEED", DEBUG)

# LINE: 実送信の明示的な許可（開発・テストでは false のまま）
LINE_SEND_ENABLED = env_bool("LINE_SEND_ENABLED", False)

# カード端末: 本番（実際にお金が動く）の決済会社への送信を許可するか。開発・テストでは false のまま（模擬端末・Sandbox は使える）
CARD_TERMINAL_LIVE_ENABLED = env_bool("CARD_TERMINAL_LIVE_ENABLED", False)
# 模擬端末（承認・拒否を画面で選ぶテスト用）を使えるか。本番では既定で使えない（実際に決済していないカード売上が残るため）
CARD_TERMINAL_MOCK_ENABLED = env_bool("CARD_TERMINAL_MOCK_ENABLED", DEBUG)

# freee 会計: 本番の freee へ仕訳を送るか（開発・テストでは false のまま。模擬接続は使える）
FREEE_LIVE_ENABLED = env_bool("FREEE_LIVE_ENABLED", False)
FREEE_CLIENT_ID = os.environ.get("FREEE_CLIENT_ID", "")
FREEE_CLIENT_SECRET = os.environ.get("FREEE_CLIENT_SECRET", "")
FREEE_REDIRECT_URI = os.environ.get("FREEE_REDIRECT_URI", "")

# 証憑・契約書ファイルの保存先（公開 URL は作らず、権限を確認する API からだけ返す）
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media")))
EVIDENCE_MAX_BYTES = int(os.environ.get("EVIDENCE_MAX_BYTES", str(10 * 1024 * 1024)))
# 納期限の計算で、法律の規定以外に休日として扱う日（"YYYY-MM-DD" をカンマ区切り。政府が臨時に祝日を設けた場合など）
ACCOUNTING_EXTRA_HOLIDAYS = [s.strip() for s in os.environ.get("ACCOUNTING_EXTRA_HOLIDAYS", "").split(",") if s.strip()]

# --- フィールド暗号化（freee/Square のトークン・申告入力・原本ファイル） ---
# 鍵は FIELD_ENCRYPTION_KEY_V1..V9（base64 の 32 バイト）。本番（DEBUG=0）で鍵が無ければ起動しない（apps.core.crypto）。
# 開発・テストでは SECRET_KEY から導出した鍵で動く。FIELD_ENCRYPTION_ALLOW_DERIVED=1 で本番でも導出鍵を許す（推奨しない）
FIELD_ENCRYPTION_ALLOW_DERIVED = env_bool("FIELD_ENCRYPTION_ALLOW_DERIVED", DEBUG)  # 既定: 開発（DEBUG）のときだけ導出鍵を許す

# --- 領収書の AI 読取（apps.expenses.ocr） ---
# mock: 固定結果（開発・テスト）/ anthropic: Claude の画像・PDF 直読み。会社設定 CompanySettings.ai_enabled も必要
RECEIPT_AI_PROVIDER = os.environ.get("RECEIPT_AI_PROVIDER", "mock")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RECEIPT_AI_MODEL = os.environ.get("RECEIPT_AI_MODEL", "claude-sonnet-5-5")
RECEIPT_AI_FALLBACK_MODEL = os.environ.get("RECEIPT_AI_FALLBACK_MODEL", "claude-opus-5-5")
RECEIPT_AI_TIMEOUT = int(os.environ.get("RECEIPT_AI_TIMEOUT", "120"))
RECEIPT_AI_MAX_EDGE = int(os.environ.get("RECEIPT_AI_MAX_EDGE", "2000"))

# --- XTX（e-Tax 申告データ）の公開可否。公式ソフトでの取込試験が完了した年度だけ "2026,2027" のように列挙する ---
TAX_XTX_RELEASED_YEARS = [int(s) for s in os.environ.get("TAX_XTX_RELEASED_YEARS", "").split(",") if s.strip().isdigit()]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.exception_handler",
    "COERCE_DECIMAL_TO_STRING": True,
    "DATETIME_FORMAT": "iso-8601",
    "UNAUTHENTICATED_USER": None,
    # ?format=csv を CSV 出力の指定に使うため、DRF のレンダラー切替としては扱わない
    "URL_FORMAT_OVERRIDE": None,
}

# テスト実行時はパスワードハッシュを軽量化する（本番の挙動には影響しない）
if len(sys.argv) > 1 and sys.argv[1] == "test":
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# 本番では暗号鍵・SECRET_KEY が無いまま起動しない（fail-closed）
if not DEBUG and not (len(sys.argv) > 1 and sys.argv[1] in ("makemigrations", "collectstatic", "check")):
    if SECRET_KEY == "dev-only-insecure-secret-key-change-me":
        raise RuntimeError("DJANGO_SECRET_KEY を設定してください（既定値のままでは本番で起動できません）。")
    if not FIELD_ENCRYPTION_ALLOW_DERIVED and not any(os.environ.get(f"FIELD_ENCRYPTION_KEY_V{v}") for v in range(1, 10)):
        raise RuntimeError("FIELD_ENCRYPTION_KEY_V1 を設定してください（本番では暗号鍵が必須です）。")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"redact": {"()": "apps.core.logging.RedactFilter"}},
    "formatters": {"std": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "std", "filters": ["redact"]},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.request": {"level": "WARNING"},
        "yorumiru": {"level": "INFO"},
    },
}
