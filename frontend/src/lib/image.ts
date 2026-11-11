/**
 * 撮影した画像の縮小（送信前）。長辺 maxEdge px・JPEG 品質 quality。EXIF は付けない（位置情報などを送らない）。
 * createImageBitmap が EXIF の向きを直すブラウザ（iOS Safari を含む）ではそのまま正しい向きになる。
 */
export async function downscaleImage(file: File, maxEdge = 2000, quality = 0.85): Promise<Blob> {
  if (!file.type.startsWith("image/") || file.type === "image/heic" || file.type === "image/heif") return file;
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" } as ImageBitmapOptions);
  } catch {
    return file; // ブラウザが読めない形式はそのまま送り、サーバー側で判定する
  }
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const w = Math.max(1, Math.round(bitmap.width * scale));
  const h = Math.max(1, Math.round(bitmap.height * scale));
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return file;
  ctx.drawImage(bitmap, 0, 0, w, h);
  bitmap.close?.();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  if (!blob) return file;
  return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg", lastModified: Date.now() });
}

export function isPdf(file: File | Blob): boolean {
  return file.type === "application/pdf" || (file instanceof File && /\.pdf$/i.test(file.name));
}
