"""
アップロードされた原本ファイルの検査と前処理。

- 中身（マジックバイト）で種類を判定する。クライアントの content_type は信用しない。
- 画像は Pillow で再デコードして壊れていないか確かめ、EXIF の向きを直し、縮小・JPEG 再エンコードした「送信用・プレビュー用」画像を作る。
- 知覚ハッシュ（dHash 64bit）と画質の目安（短辺・長辺・推定 dpi）を返す。
- HEIC/HEIF は pillow-heif があれば JPEG に変換する。
"""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

from django.conf import settings

from apps.core.exceptions import ApiError

ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/heic", "image/heif", "image/webp"}
CSV_TYPES = {"text/csv", "text/plain", "application/vnd.ms-excel"}
MAX_PIXELS = 50_000_000


@dataclass
class UploadInfo:
    content_type: str
    sha256: str
    size: int
    data: bytes
    is_image: bool = False
    width: int | None = None
    height: int | None = None
    page_count: int = 1
    phash: str = ""
    quality_flags: list[str] = field(default_factory=list)
    preview_jpeg: bytes | None = None  # 縮小・EXIF 除去済み（AI 送信・プレビュー用）


def _sniff(data: bytes, declared: str, name: str) -> str:
    try:
        import filetype

        kind = filetype.guess(data)
        if kind is not None:
            return kind.mime
    except ImportError:  # pragma: no cover
        pass
    head = data[:12]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[4:8] == b"ftyp":
        return "image/heic"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    return declared or ""


def _dhash(img) -> str:
    small = img.convert("L").resize((9, 8))
    px = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def inspect_upload(f, *, allow_pdf: bool = True, max_bytes: int | None = None) -> UploadInfo:
    """UploadedFile を読み、種類の検証・ハッシュ・画像の前処理を行う。不正なら ApiError。"""
    max_bytes = max_bytes or settings.EVIDENCE_MAX_BYTES
    if f is None:
        raise ApiError("validation_error", "ファイルを選んでください。")
    if f.size > max_bytes:
        raise ApiError("validation_error", f"ファイルが大きすぎます（{max_bytes // (1024 * 1024)}MBまで）。")
    data = f.read()
    f.seek(0)
    ctype = _sniff(data, f.content_type or "", f.name)
    if ctype == "image/heif":
        ctype = "image/heic"
    if ctype not in ALLOWED_TYPES or (ctype == "application/pdf" and not allow_pdf):
        raise ApiError("validation_error", "PDF または画像（JPEG・PNG・HEIC・WebP）を選んでください。ファイルの中身が画像・PDF ではありません。")
    info = UploadInfo(content_type=ctype, sha256=hashlib.sha256(data).hexdigest(), size=len(data), data=data)
    if ctype == "application/pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            info.page_count = max(1, len(reader.pages))
        except Exception:  # noqa: BLE001
            raise ApiError("validation_error", "PDF を読み込めませんでした。壊れていないか確認してください。")
        return info
    info.is_image = True
    try:
        from PIL import Image, ImageOps

        if ctype == "image/heic":
            try:
                import pillow_heif

                pillow_heif.register_heif_opener()
            except ImportError:
                raise ApiError("validation_error", "HEIC 形式はこのサーバーでは扱えません。JPEG で撮り直してください。")
        img = Image.open(io.BytesIO(data))
        img.load()
        if img.width * img.height > MAX_PIXELS:
            raise ApiError("validation_error", "画像が大きすぎます。")
        img = ImageOps.exif_transpose(img)
        info.width, info.height = img.width, img.height
        rgb = img.convert("RGB")
        info.phash = _dhash(rgb)
        short, long_ = min(img.width, img.height), max(img.width, img.height)
        if short < 450 or long_ < 1000:
            info.quality_flags.append("low_resolution")
        max_edge = int(getattr(settings, "RECEIPT_AI_MAX_EDGE", 2000))
        if long_ > max_edge:
            scale = max_edge / long_
            rgb = rgb.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))))
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=85, optimize=True)  # EXIF（位置情報など）は付けない
        info.preview_jpeg = out.getvalue()
    except ApiError:
        raise
    except Exception:  # noqa: BLE001
        raise ApiError("validation_error", "画像を読み込めませんでした。壊れていないか確認してください。")
    return info


def inspect_csv(f, *, max_bytes: int = 5 * 1024 * 1024) -> tuple[bytes, str]:
    """CSV/テキストの読み取り（文字コード判定）。戻り値は (bytes, encoding)。"""
    if f is None:
        raise ApiError("validation_error", "ファイルを選んでください。")
    if f.size > max_bytes:
        raise ApiError("validation_error", "ファイルが大きすぎます（5MBまで）。")
    data = f.read()
    f.seek(0)
    if data[:4] == b"%PDF" or data[:2] == b"PK" or data[:2] == b"\xff\xd8":
        raise ApiError("validation_error", "CSV（テキスト）ファイルを選んでください。")
    return data, detect_encoding(data)


def detect_encoding(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "cp932", "euc_jp"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best and best.encoding:
            return best.encoding
    except Exception:  # noqa: BLE001
        pass
    return "utf-8"
