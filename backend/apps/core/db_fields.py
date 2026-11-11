"""暗号化して保存するモデルフィールド。保存時に暗号化し、読み出し時に復号する。先頭に "vN:" の無い値は移行前の平文として読む。"""
from __future__ import annotations

from django.db import models

from . import crypto


class EncryptedTextField(models.TextField):
    description = "暗号化して保存する文字列（検索・集計には使えない）"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        if crypto.is_encrypted_str(value):
            return value
        return crypto.encrypt(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return crypto.decrypt(value)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str) and crypto.is_encrypted_str(value):
            return crypto.decrypt(value)
        return value


class EncryptedCharField(models.CharField):
    description = "暗号化して保存する短い文字列（max_length は暗号文の長さ。平文の約 1.5 倍＋40 を確保する）"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        if crypto.is_encrypted_str(value):
            return value
        return crypto.encrypt(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return crypto.decrypt(value)

    def to_python(self, value):
        if value is None:
            return value
        if isinstance(value, str) and crypto.is_encrypted_str(value):
            return crypto.decrypt(value)
        return value
