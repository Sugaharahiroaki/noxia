"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function InvitePage() {
  return (
    <Suspense fallback={<Spinner />}>
      <InviteInner />
    </Suspense>
  );
}

function InviteInner() {
  const params = useSearchParams();
  const router = useRouter();
  const { refresh } = useSession();
  const token = params.get("token") ?? "";
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/accept-invite", { method: "POST", body: { token, username, password, display_name: displayName }, storeId: "none" });
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
        <h1 className="text-2xl font-bold">招待を受けてアカウントを作成</h1>
        {!token && <p style={{ color: "var(--color-danger)" }}>招待トークンがありません。招待リンクからアクセスしてください。</p>}
        <label className="block">
          <span className="label">ユーザー名（ログインに使用）</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="none" />
        </label>
        <label className="block">
          <span className="label">表示名</span>
          <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <label className="block">
          <span className="label">パスワード（8文字以上）</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        </label>
        {error && (
          <p className="text-sm font-semibold" role="alert" style={{ color: "var(--color-danger)" }}>
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary btn-lg w-full" disabled={busy || !token || !username || !password}>
          {busy ? "作成中…" : "アカウントを作成してログイン"}
        </button>
      </form>
    </div>
  );
}
