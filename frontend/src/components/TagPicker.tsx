"use client";

import { useMemo, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import type { Tag, Visit, VisitTagInfo } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { Empty, ErrorBox, Modal, Spinner } from "./ui";

/** タグの小さな表示（色つき。色が無いタグは既定の色） */
export function TagChip({ tag, small }: { tag: { name: string; color: string }; small?: boolean }) {
  return (
    <span
      className={`inline-flex items-center rounded-full font-bold whitespace-nowrap ${small ? "px-2 text-xs" : "px-3 text-base"}`}
      style={{ background: tag.color || "var(--color-panel3)", color: tag.color ? "#fff" : "var(--color-text)", border: "1px solid rgba(0,0,0,0.25)", minHeight: small ? 20 : 30 }}
    >
      {tag.name}
    </span>
  );
}

/**
 * 伝票のタグ選択。左に候補（色・検索で絞り込み）、右に選択中。決定で PUT visits/<id>/tags（入れ替え）。
 * 決定するまでは画面内の下書き。
 */
export function TagPicker({ visitId, current, onClose, onSaved }: { visitId: number; current: VisitTagInfo[]; onClose: () => void; onSaved: (v: Visit) => void }) {
  const tags = useLoad<Tag[]>("/api/store/tags?active=1");
  const [picked, setPicked] = useState<number[]>(() => current.map((t) => t.id));
  const [q, setQ] = useState("");
  const [color, setColor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const all = tags.data ?? [];
  const colors = useMemo(() => [...new Set(all.map((t) => t.color).filter(Boolean))], [all]);
  const candidates = all.filter((t) => !picked.includes(t.id) && (color === null || t.color === color) && (q.trim() === "" || t.name.includes(q.trim())));
  // 停止されたタグが付いたままの伝票でも、選択中には出す（外すことはできる）
  const byId = new Map<number, { id: number; name: string; color: string }>([...current.map((t) => [t.id, t] as const), ...all.map((t) => [t.id, t] as const)]);
  const selected = picked.map((id) => byId.get(id)).filter((t): t is { id: number; name: string; color: string } => !!t);
  const activeIds = new Set(all.map((t) => t.id));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // 停止されたタグは API が受け付けないので、有効なものだけ送る
      const v = await api<Visit>(`/api/visits/${visitId}/tags`, { method: "PUT", body: { tag_ids: picked.filter((id) => activeIds.has(id)) } });
      onSaved(v);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="タグを選ぶ"
      wide
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button className="btn btn-xl btn-primary" onClick={save} disabled={busy || tags.loading}>
            {busy ? "保存中…" : "決定"}
          </button>
        </>
      }
    >
      <ErrorBox error={tags.error} onRetry={() => tags.reload(false)} />
      {tags.loading && <Spinner />}
      {tags.data && (
        <div className="grid grid-cols-2 gap-3">
          <section aria-label="タグの候補" className="space-y-2">
            <div className="flex items-center gap-2">
              <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="タグを検索" aria-label="タグを検索" />
            </div>
            {colors.length > 0 && (
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="色で絞り込む">
                <button className={`btn btn-sm ${color === null ? "btn-primary" : ""}`} onClick={() => setColor(null)} aria-pressed={color === null}>
                  全色
                </button>
                {colors.map((c) => (
                  <button key={c} className="rounded-full" style={{ width: 44, height: 44, background: c, border: color === c ? "4px solid var(--color-text)" : "2px solid rgba(0,0,0,0.25)" }} onClick={() => setColor(color === c ? null : c)} aria-pressed={color === c} aria-label={`色 ${c}`} />
                ))}
              </div>
            )}
            <div className="space-y-1 overflow-y-auto" style={{ maxHeight: "38vh" }}>
              {candidates.length === 0 && <Empty>{all.length === 0 ? "タグが登録されていません。設定で登録してください。" : "候補はありません。"}</Empty>}
              {candidates.map((t) => (
                <button key={t.id} className="w-full flex items-center justify-between rounded-xl px-3" style={{ minHeight: 52, background: "var(--color-panel2)", border: "1px solid var(--color-border)" }} onClick={() => setPicked((p) => [...p, t.id])} aria-label={`${t.name} を付ける`}>
                  <TagChip tag={t} />
                  <span className="font-bold" style={{ color: "var(--color-orange)" }}>
                    ＋ 付ける
                  </span>
                </button>
              ))}
            </div>
          </section>
          <section aria-label="選択中のタグ" className="space-y-2">
            <h3 className="font-bold" style={{ minHeight: 52, display: "flex", alignItems: "center" }}>
              選択中 <span className="num ml-1">{selected.length}</span>件
            </h3>
            <div className="space-y-1 overflow-y-auto" style={{ maxHeight: "46vh" }}>
              {selected.length === 0 && <Empty>左の候補を押すと、ここに入ります。</Empty>}
              {selected.map((t) => (
                <button key={t.id} className="w-full flex items-center justify-between rounded-xl px-3 pos-selected" style={{ minHeight: 52 }} onClick={() => setPicked((p) => p.filter((x) => x !== t.id))} aria-label={`${t.name} を外す`}>
                  <TagChip tag={t} />
                  <span className="font-bold">− 外す</span>
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
      {error && (
        <p className="text-lg font-bold mt-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </Modal>
  );
}
