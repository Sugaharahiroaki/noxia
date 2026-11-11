"use client";

import { useEffect, useState } from "react";
import { api, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtTime, yen } from "@/lib/format";
import type { Visit } from "@/lib/types";
import { Modal, Spinner } from "./ui";

export type CourseActionKind = "extend" | "unlimited";
export type CourseActionResult = { visit: Visit & { created_line_id?: number }; kind: CourseActionKind; amount: string; toEnd: string | null };

type ExtendPreview = { amount: string; minutes: number; from_planned_end_at: string; to_planned_end_at: string; guest_count: number; table_name: string; label: string };
type UnlimitedPreview = { amount: string; label: string; target_course: string };

/**
 * 延長・無制限変更の確認ダイアログ。料金と終了時刻をサーバーから取得して表示し、
 * スタッフが「確定」を押したときだけ反映する（自動課金しない）。
 */
export function CourseActionDialog({
  visitId,
  entryId,
  kind,
  title,
  onClose,
  onDone,
}: {
  visitId: number;
  entryId: number;
  kind: CourseActionKind;
  title: string;
  onClose: () => void;
  onDone: (r: CourseActionResult) => void;
}) {
  const [ext, setExt] = useState<ExtendPreview | null>(null);
  const [unl, setUnl] = useState<UnlimitedPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [idem] = useState(() => newIdempotencyKey());

  useEffect(() => {
    const path = `/api/visits/${visitId}/course-entries/${entryId}/${kind === "extend" ? "extend-preview" : "unlimited-preview"}`;
    api<ExtendPreview | UnlimitedPreview>(path)
      .then((d) => (kind === "extend" ? setExt(d as ExtendPreview) : setUnl(d as UnlimitedPreview)))
      .catch((e) => setError(errorMessage(e)));
  }, [visitId, entryId, kind]);

  async function confirm() {
    setBusy(true);
    setError(null);
    try {
      const path = `/api/visits/${visitId}/course-entries/${entryId}/${kind === "extend" ? "extend" : "change-unlimited"}`;
      const v = await api<Visit & { created_line_id?: number }>(path, { method: "POST", body: {}, idempotencyKey: idem });
      onDone({ visit: v, kind, amount: kind === "extend" ? ext!.amount : unl!.amount, toEnd: kind === "extend" ? ext!.to_planned_end_at : null });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  const ready = kind === "extend" ? !!ext : !!unl;

  return (
    <Modal
      title={title}
      onClose={busy ? undefined : onClose}
      footer={
        <>
          <button className="btn btn-xl" onClick={onClose} disabled={busy}>
            やめる
          </button>
          <button className="btn btn-xl btn-primary" onClick={confirm} disabled={!ready || busy}>
            {busy ? "処理中…" : kind === "extend" ? "延長する" : "無制限にする"}
          </button>
        </>
      }
    >
      {error && (
        <p className="text-lg font-bold mb-3" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
      {!ready && !error && <Spinner label="料金を確認中…" />}
      {kind === "extend" && ext && (
        <div className="space-y-3 text-center">
          <div className="text-2xl font-bold">
            {ext.table_name} {ext.label || `${ext.guest_count}名`}
          </div>
          <div className="text-xl num">
            終了 {fmtTime(ext.from_planned_end_at)} → <strong className="text-4xl">{fmtTime(ext.to_planned_end_at)}</strong>
          </div>
          <div className="text-5xl font-bold num" style={{ color: "var(--color-warn)" }}>
            ＋{yen(ext.amount)}
          </div>
          <div style={{ color: "var(--color-muted)" }}>{ext.minutes}分延長（元の終了予定時刻から加算）</div>
        </div>
      )}
      {kind === "unlimited" && unl && (
        <div className="space-y-3 text-center">
          <div className="text-2xl font-bold">「{unl.target_course}」へ変更</div>
          <div className="text-5xl font-bold num" style={{ color: "var(--color-warn)" }}>
            ＋{yen(unl.amount)}
          </div>
          <div style={{ color: "var(--color-muted)" }}>{unl.label}。変更後は時間のお知らせが出なくなります。</div>
        </div>
      )}
    </Modal>
  );
}
