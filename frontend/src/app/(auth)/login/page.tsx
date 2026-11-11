"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Me } from "@/lib/types";

export default function LoginPage() {
  const { me, loading, refresh } = useSession();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loading && me) router.replace("/select-store");
  }, [me, loading, router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<Me>("/api/auth/login", { method: "POST", body: { username, password }, storeId: "none" });
      await refresh();
      router.replace("/select-store");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <form onSubmit={submit} className="card w-full max-w-md space-y-4">
        <div className="flex flex-col items-center text-center pt-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon-192.png" alt="" width={96} height={96} />
          <h1 className="text-4xl font-semibold tracking-[0.35em] mt-3 pl-[0.35em]">NOXIA</h1>
          <p className="text-sm mt-1" style={{ color: "var(--color-muted)" }}>
            ラウンジ店舗運営アプリにログイン
          </p>
        </div>
        <label className="block">
          <span className="label">ユーザー名</span>
          <input className="input" autoComplete="username" autoCapitalize="none" value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">パスワード</span>
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && (
          <p className="text-sm font-semibold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy || !username || !password}>
          {busy ? "ログイン中…" : "ログイン"}
        </button>
        <p className="text-xs" style={{ color: "var(--color-muted)" }}>
          ホーム画面に追加して全画面で利用できます（Safari の共有メニュー →「ホーム画面に追加」）。画面ロック中・他アプリ利用中の通知は初版では保証しません。
        </p>
      </form>
    </div>
  );
}
