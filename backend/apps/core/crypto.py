"""
フィールド暗号化（AES-256-GCM・版付き鍵）。ミセミル core/crypto.py の方式を移植。

- 鍵は環境変数 FIELD_ENCRYPTION_KEY_V1..V9（base64 の 32 バイト）と FIELD_ENCRYPTION_ACTIVE_VERSION（既定 1）。
- 本番（DEBUG=False）で鍵が無ければ EncryptionUnavailable（fail-closed）。開発・テストでは SECRET_KEY から導出した鍵で動く。
- 文字列: "v1:" + base64(nonce12 || ciphertext || tag)。先頭に "vN:" が無い値は移行前の平文として扱う（db_fields）。
- バイト列: b"NXENC1:" + version + b":" + nonce + ciphertext。
"""
from __future__ import annotations

import base64
import hashlib
import os

from django.conf import settings

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover
    AESGCM = None  # type: ignore

STR_PREFIX = "v"
BYTES_MAGIC = b"NXENC1:"
_NONCE = 12


class EncryptionUnavailable(RuntimeError):
    pass


def _keys() -> dict[int, bytes]:
    keys: dict[int, bytes] = {}
    for v in range(1, 10):
        raw = os.environ.get(f"FIELD_ENCRYPTION_KEY_V{v}")
        if raw:
            try:
                k = base64.b64decode(raw)
            except Exception as e:  # pragma: no cover
                raise EncryptionUnavailable(f"FIELD_ENCRYPTION_KEY_V{v} が base64 ではありません") from e
            if len(k) != 32:
                raise EncryptionUnavailable(f"FIELD_ENCRYPTION_KEY_V{v} は 32 バイトの鍵を base64 にしたものにしてください")
            keys[v] = k
    if not keys:
        # 開発・テスト用: SECRET_KEY から決定的に導出（settings.FIELD_ENCRYPTION_ALLOW_DERIVED。既定は DEBUG のとき）。
        # Django のテスト実行は DEBUG を False にするため、settings.DEBUG ではなくこのフラグで判定する
        if getattr(settings, "FIELD_ENCRYPTION_ALLOW_DERIVED", False):
            keys[1] = hashlib.sha256(("noxia-field-key:" + settings.SECRET_KEY).encode()).digest()
        else:
            raise EncryptionUnavailable("FIELD_ENCRYPTION_KEY_V1 が設定されていません（本番では必須）。")
    return keys


def active_version() -> int:
    try:
        return int(os.environ.get("FIELD_ENCRYPTION_ACTIVE_VERSION", "1"))
    except ValueError:
        return 1


def _aes(key: bytes):
    if AESGCM is None:  # pragma: no cover
        raise EncryptionUnavailable("cryptography パッケージがありません")
    return AESGCM(key)


def is_encrypted_str(value: str | None) -> bool:
    if not value or not isinstance(value, str):
        return False
    if not value.startswith(STR_PREFIX):
        return False
    head, sep, _ = value.partition(":")
    return sep == ":" and head[1:].isdigit()


def encrypt(plaintext: str) -> str:
    if plaintext is None:
        return plaintext
    keys = _keys()
    v = active_version()
    if v not in keys:
        v = max(keys)
    nonce = os.urandom(_NONCE)
    ct = _aes(keys[v]).encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{STR_PREFIX}{v}:" + base64.b64encode(nonce + ct).decode("ascii")


def decrypt(token: str) -> str:
    if not is_encrypted_str(token):
        return token
    head, _, body = token.partition(":")
    declared = int(head[1:])
    raw = base64.b64decode(body)
    nonce, ct = raw[:_NONCE], raw[_NONCE:]
    keys = _keys()
    order = [declared] + [k for k in sorted(keys, reverse=True) if k != declared]
    last_err: Exception | None = None
    for v in order:
        if v not in keys:
            continue
        try:
            return _aes(keys[v]).decrypt(nonce, ct, None).decode("utf-8")
        except Exception as e:  # noqa: BLE001 - 他の版の鍵でも試す（ローテーション）
            last_err = e
    raise EncryptionUnavailable("復号できません（鍵の版が合いません）") from last_err


def is_encrypted_bytes(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and bytes(data[: len(BYTES_MAGIC)]) == BYTES_MAGIC


def encrypt_bytes(data: bytes) -> bytes:
    keys = _keys()
    v = active_version()
    if v not in keys:
        v = max(keys)
    nonce = os.urandom(_NONCE)
    ct = _aes(keys[v]).encrypt(nonce, bytes(data), None)
    return BYTES_MAGIC + str(v).encode() + b":" + nonce + ct


def decrypt_bytes(data: bytes) -> bytes:
    if not is_encrypted_bytes(data):
        return bytes(data)
    rest = bytes(data[len(BYTES_MAGIC):])
    vpart, _, payload = rest.partition(b":")
    declared = int(vpart)
    nonce, ct = payload[:_NONCE], payload[_NONCE:]
    keys = _keys()
    order = [declared] + [k for k in sorted(keys, reverse=True) if k != declared]
    last_err: Exception | None = None
    for v in order:
        if v not in keys:
            continue
        try:
            return _aes(keys[v]).decrypt(nonce, ct, None)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise EncryptionUnavailable("復号できません（鍵の版が合いません）") from last_err


def check_configured() -> None:
    """起動時チェック（settings から呼ぶ）。本番で鍵が無ければ例外。"""
    _keys()
