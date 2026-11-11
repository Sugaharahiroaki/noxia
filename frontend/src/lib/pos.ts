import { api, newIdempotencyKey } from "./api";
import type { Visit } from "./types";

export const UNDO_REASON = "入力ミス（操作直後の取り消し）";

/** 直前に追加した明細（注文・延長）をまとめて取り消す。理由と操作者はサーバーに記録される。 */
export function undoLines(visitId: number, lineIds: number[]) {
  return api<Visit>(`/api/visits/${visitId}/lines/cancel-batch`, {
    method: "POST",
    idempotencyKey: newIdempotencyKey(),
    body: { line_ids: lineIds, reason: UNDO_REASON },
  });
}

/** 経過時間 h:mm */
export function fmtElapsed(fromIso: string, now: number): string {
  const sec = Math.max(0, Math.floor((now - new Date(fromIso).getTime()) / 1000));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

/** 残り時間（分単位、表示用） */
export function fmtRemainShort(seconds: number): string {
  if (seconds <= 0) {
    const over = Math.ceil(-seconds / 60);
    return `${over}分超過`;
  }
  const m = Math.ceil(seconds / 60);
  if (m >= 60) return `残り${Math.floor(m / 60)}時間${m % 60 ? `${m % 60}分` : ""}`;
  return `残り${m}分`;
}
