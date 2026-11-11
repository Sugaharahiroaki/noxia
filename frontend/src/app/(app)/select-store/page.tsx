"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { ErrorBox } from "@/components/ui";
import { ROLE_LABELS } from "@/lib/format";
import { useSession } from "@/lib/session";

export default function SelectStorePage() {
  const { me, setStore } = useSession();
  const router = useRouter();
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);
  if (!me) return null;

  const byCompany = new Map<number, { name: string; stores: typeof me.stores }>();
  for (const s of me.stores) {
    const c = byCompany.get(s.company.id) ?? { name: s.company.name, stores: [] };
    c.stores.push(s);
    byCompany.set(s.company.id, c);
  }

  async function choose(id: number, caps: string[]) {
    setBusy(id);
    setError(null);
    try {
      await setStore(id);
      router.replace(caps.includes("floor.operate") ? "/floor" : "/attendance");
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <h1 className="text-2xl font-bold">店舗を選択</h1>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        所属している会社・店舗だけが表示されます。
      </p>
      <ErrorBox error={error} />
      {me.stores.length === 0 && (
        <div className="card">
          <p>利用できる店舗がありません。会社オーナーまたは管理者に店舗への配属を依頼してください。</p>
          {me.user.is_platform_operator && (
            <button className="btn btn-primary mt-3" onClick={() => router.push("/platform")}>
              運営者: 会社・店舗管理へ
            </button>
          )}
        </div>
      )}
      {[...byCompany.entries()].map(([cid, c]) => (
        <div key={cid} className="card space-y-2">
          <h2 className="font-bold text-lg">{c.name}</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {c.stores.map((s) => (
              <button key={s.id} className="btn btn-lg justify-between" disabled={busy !== null} onClick={() => choose(s.id, s.caps)}>
                <span className="text-left">
                  <span className="block">{s.name}</span>
                  <span className="block text-xs font-normal" style={{ color: "var(--color-muted)" }}>
                    {ROLE_LABELS[s.role] ?? s.role}
                    {!s.is_active && " ・停止中"}
                    {s.is_demo && " ・開発用データ"}
                  </span>
                </span>
                <span>{busy === s.id ? "…" : "→"}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
      {(me.companies.some((c) => c.role === "owner" || c.role === "admin") || me.user.is_platform_operator) && (
        <div className="card space-y-2">
          <h2 className="font-bold text-lg">会社管理</h2>
          <div className="flex flex-wrap gap-2">
            {me.companies
              .filter((c) => c.role === "owner" || c.role === "admin" || c.role === "operator")
              .map((c) => (
                <button key={c.id} className="btn" onClick={() => router.push(`/company/${c.id}`)}>
                  {c.name}（全店集計・店舗・ユーザー）
                </button>
              ))}
            {me.user.is_platform_operator && (
              <button className="btn" onClick={() => router.push("/platform")}>
                運営者: 会社・店舗管理
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
