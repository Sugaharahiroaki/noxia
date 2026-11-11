"use client";

/**
 * 領収書の撮影・選択（タブレット）。撮ったらすぐプレビュー、「撮り直す」か「これでよい」。
 * 送る前に長辺 2000px に縮小する（10MB 制限と通信量の対策。PDF はそのまま）。
 */
import { useEffect, useRef, useState } from "react";
import { downscaleImage, isPdf } from "@/lib/image";

export type CapturedFile = { file: File | Blob; name: string; previewUrl: string | null; isPdf: boolean; originalSize: number };

export function CameraCapture({
  value,
  onChange,
  allowPdf = true,
  label = "領収書を撮る",
}: {
  value: CapturedFile | null;
  onChange: (f: CapturedFile | null) => void;
  allowPdf?: boolean;
  label?: string;
}) {
  const camRef = useRef<HTMLInputElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const url = value?.previewUrl;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [value]);

  async function pick(f: File | undefined) {
    if (!f) return;
    setBusy(true);
    try {
      if (isPdf(f)) {
        onChange({ file: f, name: f.name, previewUrl: URL.createObjectURL(f), isPdf: true, originalSize: f.size });
        return;
      }
      const small = await downscaleImage(f);
      onChange({ file: small, name: small instanceof File ? small.name : f.name, previewUrl: URL.createObjectURL(small), isPdf: false, originalSize: f.size });
    } finally {
      setBusy(false);
    }
  }

  if (value) {
    return (
      <div className="space-y-3">
        <div className="card-2" style={{ padding: 8, background: "#000", display: "flex", justifyContent: "center" }}>
          {value.isPdf ? (
            <iframe title="PDF" src={value.previewUrl ?? undefined} style={{ width: "100%", height: 420, border: 0, background: "#fff" }} />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={value.previewUrl ?? ""} alt="撮影した領収書" style={{ maxHeight: 420, maxWidth: "100%" }} />
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <button type="button" className="btn btn-xl" onClick={() => onChange(null)}>
            撮り直す
          </button>
          <div className="text-sm self-center" style={{ color: "var(--color-muted)" }}>
            {value.name}（{Math.round(((value.file as Blob).size ?? 0) / 1024)} KB）
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      <input ref={camRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => pick(e.target.files?.[0])} />
      <input ref={fileRef} type="file" accept={allowPdf ? "image/*,application/pdf" : "image/*"} hidden onChange={(e) => pick(e.target.files?.[0])} />
      <button type="button" className="btn btn-xl btn-primary w-full" style={{ minHeight: 120, fontSize: "1.8rem" }} disabled={busy} onClick={() => camRef.current?.click()}>
        📷 {busy ? "読み込み中…" : label}
      </button>
      <button type="button" className="btn btn-lg w-full" disabled={busy} onClick={() => fileRef.current?.click()}>
        写真{allowPdf ? "・PDF" : ""}を選ぶ
      </button>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        明るい場所で、領収書全体が入るように撮ってください。金額・日付・お店の名前が読める大きさで。
      </p>
    </div>
  );
}
