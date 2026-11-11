"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/**
 * API 読み込み用フック。
 * - 店舗切替（storeEpoch の変化）で再読み込みし、前の店舗のデータを残さない。
 * - 画面復帰（visibilitychange / online / focus）で再読み込みして確定状態を照合する。
 */
export function useLoad<T>(path: string | null, deps: unknown[] = [], opts: { refetchOnFocus?: boolean; intervalMs?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);
  const seq = useRef(0);

  const reload = useCallback(
    async (silent = true) => {
      if (!path) {
        setData(null);
        setLoading(false);
        return;
      }
      const my = ++seq.current;
      if (!silent) setLoading(true);
      else setReloading(true);
      try {
        const d = await api<T>(path);
        if (my === seq.current) {
          setData(d);
          setError(null);
        }
      } catch (e) {
        if (my === seq.current) setError(e);
      } finally {
        if (my === seq.current) {
          setLoading(false);
          setReloading(false);
        }
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [path, ...deps],
  );

  useEffect(() => {
    setData(null);
    setError(null);
    reload(false);
  }, [reload]);

  useEffect(() => {
    if (opts.refetchOnFocus === false) return;
    const onVis = () => {
      if (document.visibilityState === "visible") reload(true);
    };
    const onOnline = () => reload(true);
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("online", onOnline);
    window.addEventListener("focus", onVis);
    return () => {
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("focus", onVis);
    };
  }, [reload, opts.refetchOnFocus]);

  useEffect(() => {
    if (!opts.intervalMs) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") reload(true);
    }, opts.intervalMs);
    return () => clearInterval(t);
  }, [reload, opts.intervalMs]);

  return { data, error, loading, reloading, reload, setData };
}
