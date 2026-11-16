"use client";

import { useCallback, useEffect, useRef } from "react";

const LONG_PRESS_MS = 550;
const MOVE_TOLERANCE_PX = 12;

/**
 * 長押し（タッチ・マウス）と右クリックでメニューを開くためのフック。
 * - 長押しが成立したら onLongPress を呼び、その直後に発火する通常のタップ（click）は握りつぶす。
 * - 指が動いたら（スクロール）長押しを取り消す。
 * 使い方: <button {...bind(payload)} onClick={guardClick(() => 開く)} />
 */
export function useLongPress<T>(onLongPress: (payload: T) => void) {
  const timer = useRef<number | null>(null);
  const fired = useRef(false);
  const start = useRef<{ x: number; y: number } | null>(null);
  const cb = useRef(onLongPress);
  cb.current = onLongPress;

  const clear = useCallback(() => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    start.current = null;
  }, []);

  useEffect(() => clear, [clear]);

  const bind = useCallback(
    (payload: T) => ({
      onPointerDown: (e: React.PointerEvent) => {
        // 新しい操作の始まり。前回の長押しの状態を残さない（長押しのあと指をメニューの上で離すと click が来ないため）
        fired.current = false;
        if (e.pointerType === "mouse" && e.button !== 0) return;
        start.current = { x: e.clientX, y: e.clientY };
        if (timer.current !== null) window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => {
          timer.current = null;
          fired.current = true;
          cb.current(payload);
        }, LONG_PRESS_MS);
      },
      onPointerMove: (e: React.PointerEvent) => {
        const s = start.current;
        if (s && Math.hypot(e.clientX - s.x, e.clientY - s.y) > MOVE_TOLERANCE_PX) clear();
      },
      onPointerUp: clear,
      onPointerLeave: clear,
      onPointerCancel: clear,
      onContextMenu: (e: React.MouseEvent) => {
        // 右クリック（PC）と、タッチの長押しでブラウザが出す呼び出しメニューの両方をここで受ける
        e.preventDefault();
        clear();
        if (!fired.current) {
          fired.current = true;
          cb.current(payload);
        }
      },
    }),
    [clear],
  );

  /** 長押しのあとに来る click を 1 回だけ無視する */
  const guardClick = useCallback(
    (fn: () => void) => (e: React.MouseEvent) => {
      if (fired.current) {
        fired.current = false;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      fn();
    },
    [],
  );

  return { bind, guardClick };
}
