"use client";

import { useEffect, useRef, useState } from "react";

/**
 * サーバー時刻との差を保持し、「現在時刻」を毎秒更新する。
 * 残り時間は常に 保存された終了予定時刻 − (端末時刻 + 差分) で再計算する。
 */
let offsetMs = 0;

export function syncServerClock(serverNowIso: string | undefined | null) {
  if (!serverNowIso) return;
  const server = new Date(serverNowIso).getTime();
  if (Number.isNaN(server)) return;
  offsetMs = server - Date.now();
}

export function serverNow(): number {
  return Date.now() + offsetMs;
}

export function useNow(tickMs = 1000): number {
  const [now, setNow] = useState(() => serverNow());
  const ref = useRef<number | null>(null);
  useEffect(() => {
    const tick = () => setNow(serverNow());
    tick();
    ref.current = window.setInterval(tick, tickMs);
    const onVis = () => document.visibilityState === "visible" && tick();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      if (ref.current) clearInterval(ref.current);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [tickMs]);
  return now;
}

export type AlarmLevel = "warn10" | "warn5" | "overdue" | null;

/** サーバーと同じ判定をクライアント側でも毎秒行う（表示用。確定操作はサーバーが正） */
export function computeAlarm(plannedEndIso: string | null, isUnlimited: boolean, isActive: boolean, now: number, mode: "10" | "5" | "both"): { level: AlarmLevel; remaining: number } {
  if (!isActive || isUnlimited || !plannedEndIso) return { level: null, remaining: 0 };
  const end = new Date(plannedEndIso).getTime();
  const remaining = Math.floor((end - now) / 1000);
  if (remaining <= 0) return { level: "overdue", remaining };
  if (remaining <= 300) return { level: mode === "10" ? "warn10" : "warn5", remaining };
  if (remaining <= 600 && mode !== "5") return { level: "warn10", remaining };
  return { level: null, remaining };
}
