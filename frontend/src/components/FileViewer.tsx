"use client";

/**
 * 原本（画像・PDF）のビューア。認証付きでファイルを取り、画像は拡大・回転、PDF は iframe で表示する。
 * path には /api/.../file?inline=1 のような取得 API を渡す（公開 URL は作らない）。
 */
import { useEffect, useState } from "react";
import { errorMessage, fetchBlobUrl } from "@/lib/api";
import { Spinner } from "./ui";

export function FileViewer({ path, name, height = 520 }: { path: string | null; name?: string; height?: number }) {
  const [url, setUrl] = useState<string | null>(null);
  const [type, setType] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(1);
  const [rot, setRot] = useState(0);

  useEffect(() => {
    let revoke: string | null = null;
    let alive = true;
    setUrl(null);
    setError(null);
    setZoom(1);
    setRot(0);
    if (!path) return;
    fetchBlobUrl(path)
      .then(({ url: u, contentType }) => {
        if (!alive) {
          URL.revokeObjectURL(u);
          return;
        }
        revoke = u;
        setUrl(u);
        setType(contentType);
      })
      .catch((e) => alive && setError(errorMessage(e)));
    return () => {
      alive = false;
      if (revoke) URL.revokeObjectURL(revoke);
    };
  }, [path]);

  if (!path) return <div className="card-2 text-center py-10" style={{ color: "var(--color-muted)" }}>原本はありません</div>;
  if (error) return <div className="card-2" role="alert">{error}</div>;
  if (!url) return <Spinner label="原本を読み込み中…" />;
  const isPdf = type.includes("pdf");
  return (
    <div className="card-2" style={{ padding: 8 }}>
      {!isPdf && (
        <div className="flex flex-wrap gap-2 mb-2">
          <button className="btn btn-sm" onClick={() => setZoom((z) => Math.min(4, z + 0.5))}>
            拡大
          </button>
          <button className="btn btn-sm" onClick={() => setZoom((z) => Math.max(0.5, z - 0.5))}>
            縮小
          </button>
          <button className="btn btn-sm" onClick={() => setRot((r) => (r + 90) % 360)}>
            回転
          </button>
          <button className="btn btn-sm" onClick={() => { setZoom(1); setRot(0); }}>
            元に戻す
          </button>
          <a className="btn btn-sm" href={url} download={name ?? "file"}>
            保存
          </a>
        </div>
      )}
      <div style={{ height, overflow: "auto", background: "#000", borderRadius: 10, display: "flex", alignItems: "flex-start", justifyContent: "center" }}>
        {isPdf ? (
          <iframe title={name ?? "PDF"} src={url} style={{ width: "100%", height: "100%", border: 0, background: "#fff" }} />
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={url}
            alt={name ?? "原本"}
            style={{ transform: `scale(${zoom}) rotate(${rot}deg)`, transformOrigin: "top center", maxWidth: "100%", transition: "transform 0.15s" }}
          />
        )}
      </div>
    </div>
  );
}
