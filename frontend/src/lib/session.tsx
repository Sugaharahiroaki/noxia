"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, setStoreIdGetter } from "./api";
import type { Me, StoreInfo } from "./types";

const STORE_KEY = "yorumiru.storeId";

type SessionState = {
  me: Me | null;
  loading: boolean;
  store: StoreInfo | null;
  storeEpoch: number;
  refresh: () => Promise<Me | null>;
  setStore: (id: number | null) => Promise<void>;
  logout: () => Promise<void>;
  can: (...caps: string[]) => boolean;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [storeEpoch, setStoreEpoch] = useState(0);
  const storeIdRef = useRef<number | null>(null);
  storeIdRef.current = storeId;

  useEffect(() => {
    setStoreIdGetter(() => storeIdRef.current);
  }, []);

  const refresh = useCallback(async (): Promise<Me | null> => {
    try {
      const data = await api<Me>("/api/auth/me", { storeId: "none" });
      if (!data.user) {
        setMe(null);
        setStoreId(null);
        return null;
      }
      setMe(data);
      let sid: number | null = null;
      try {
        const saved = localStorage.getItem(STORE_KEY);
        if (saved && data.stores.some((s) => s.id === Number(saved))) sid = Number(saved);
      } catch {
        /* ignore */
      }
      if (sid === null && data.stores.length === 1) sid = data.stores[0].id;
      setStoreId(sid);
      return data;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMe(null);
        setStoreId(null);
        return null;
      }
      throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh().catch(() => setLoading(false));
    const onAuth = () => {
      setMe(null);
      setStoreId(null);
    };
    window.addEventListener("yorumiru:auth-required", onAuth);
    return () => window.removeEventListener("yorumiru:auth-required", onAuth);
  }, [refresh]);

  const setStore = useCallback(async (id: number | null) => {
    if (id !== null) {
      await api("/api/auth/select-store", { method: "POST", body: { store_id: id }, storeId: "none" });
    }
    try {
      if (id === null) localStorage.removeItem(STORE_KEY);
      else localStorage.setItem(STORE_KEY, String(id));
    } catch {
      /* ignore */
    }
    setStoreId(id);
    // 店舗切替時に前の店舗の画面情報を残さないため、配下を再マウントする
    setStoreEpoch((n) => n + 1);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST", storeId: "none" });
    } finally {
      setMe(null);
      setStoreId(null);
      try {
        localStorage.removeItem(STORE_KEY);
      } catch {
        /* ignore */
      }
    }
  }, []);

  const store = useMemo(() => me?.stores.find((s) => s.id === storeId) ?? null, [me, storeId]);

  const can = useCallback((...caps: string[]) => !!store && caps.every((c) => store.caps.includes(c)), [store]);

  const value: SessionState = {
    me,
    loading,
    store,
    storeEpoch,
    refresh,
    setStore,
    logout,
    can,
  };

  return (
    <SessionContext.Provider value={value}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("SessionProvider が必要です");
  return ctx;
}
