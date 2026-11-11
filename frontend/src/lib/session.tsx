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
  reauthValid: boolean;
  requestReauth: () => Promise<boolean>;
  withReauth: <T>(fn: () => Promise<T>) => Promise<T>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [storeId, setStoreId] = useState<number | null>(null);
  const [storeEpoch, setStoreEpoch] = useState(0);
  const [reauthUntil, setReauthUntil] = useState<number | null>(null);
  const [reauthOpen, setReauthOpen] = useState(false);
  const reauthResolver = useRef<((ok: boolean) => void) | null>(null);
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
      setReauthUntil(data.reauth_until);
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

  const reauthValid = reauthUntil !== null && Date.now() / 1000 < reauthUntil;

  const requestReauth = useCallback(() => {
    return new Promise<boolean>((resolve) => {
      reauthResolver.current = resolve;
      setReauthOpen(true);
    });
  }, []);

  const finishReauth = useCallback((ok: boolean, until?: number) => {
    setReauthOpen(false);
    if (ok && until) setReauthUntil(until);
    reauthResolver.current?.(ok);
    reauthResolver.current = null;
  }, []);

  const withReauth = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T> => {
      try {
        return await fn();
      } catch (e) {
        if (e instanceof ApiError && e.code === "reauth_required") {
          const ok = await requestReauth();
          if (!ok) throw e;
          return await fn();
        }
        throw e;
      }
    },
    [requestReauth],
  );

  const value: SessionState = {
    me,
    loading,
    store,
    storeEpoch,
    refresh,
    setStore,
    logout,
    can,
    reauthValid,
    requestReauth,
    withReauth,
  };

  return (
    <SessionContext.Provider value={value}>
      {children}
      {reauthOpen && <ReauthDialog onDone={finishReauth} />}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("SessionProvider が必要です");
  return ctx;
}

function ReauthDialog({ onDone }: { onDone: (ok: boolean, until?: number) => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ reauth_until: number }>("/api/auth/reauth", { method: "POST", body: { password }, storeId: "none" });
      onDone(true, r.reauth_until);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "再認証に失敗しました。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4">
      <form onSubmit={submit} className="card w-full max-w-md space-y-4">
        <h2 className="text-xl font-bold">パスワードの再確認</h2>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          給与・バック設定などの管理操作です。共有端末での誤操作を防ぐため、ログイン中のユーザーのパスワードを入力してください。
        </p>
        <input className="input" type="password" autoFocus autoComplete="current-password" placeholder="パスワード" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-sm" style={{ color: "var(--color-danger)" }}>{error}</p>}
        <div className="flex gap-3 justify-end">
          <button type="button" className="btn" onClick={() => onDone(false)} disabled={busy}>
            キャンセル
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy || !password}>
            {busy ? "確認中…" : "確認する"}
          </button>
        </div>
      </form>
    </div>
  );
}
