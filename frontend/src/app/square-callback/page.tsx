"use client";

/**
 * Square のアプリからの戻り先（Square Developer の「Point of Sale API → Web Callback URL」に登録する URL）。
 * Square のアプリが ?data={status, transaction_id, state, error_code…} を付けてこのページを開く。
 * ここでは中身をそのままサーバーに渡すだけ（照合と会計の確定はサーバー）。
 * ホーム画面から開いた NOXIA と Safari ではログインが別なので、ログインしていなくても結果を届けられるようにしてある。
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";

type Result = { status: string; status_label: string; message: string; visit: number; checkout: number | null; store: number };
type View = { kind: "working" } | { kind: "done"; r: Result } | { kind: "error"; message: string };

export default function SquareCallbackPage() {
  const { me } = useSession();
  const [view, setView] = useState<View>({ kind: "working" });
  const sent = useRef(false);

  useEffect(() => {
    if (sent.current) return;
    sent.current = true;
    const raw = new URLSearchParams(window.location.search).get("data");
    if (!raw) {
      setView({ kind: "error", message: "Square のアプリからの結果がありません。NOXIA に戻って、会計の画面を確かめてください。" });
      return;
    }
    api<Result>("/api/terminals/square-pos/callback", { method: "POST", body: { data: raw }, storeId: "none" })
      .then((r) => setView({ kind: "done", r }))
      .catch((e) => setView({ kind: "error", message: errorMessage(e) }));
  }, []);

  const loggedIn = !!me;
  return (
    <main className="min-h-dvh flex items-center justify-center p-4">
      <div className="card max-w-xl w-full space-y-4 text-center" data-testid="square-callback" data-status={view.kind === "done" ? view.r.status : view.kind}>
        <div className="text-sm" style={{ color: "var(--color-muted)" }}>
          NOXIA ・ Square リーダーの決済
        </div>
        {view.kind === "working" && <Spinner label="Square の結果を NOXIA に記録しています…" />}
        {view.kind === "error" && (
          <p className="text-xl font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
            {view.message}
          </p>
        )}
        {view.kind === "done" && <ResultView r={view.r} loggedIn={loggedIn} />}
        {view.kind !== "working" && !loggedIn && (
          <p className="text-base" style={{ color: "var(--color-muted)" }}>
            ホーム画面の NOXIA に切り替えると、会計の画面に結果が出ています。
          </p>
        )}
      </div>
    </main>
  );
}

function ResultView({ r, loggedIn }: { r: Result; loggedIn: boolean }) {
  if (r.status === "completed") {
    return (
      <>
        <div className="text-3xl font-bold" style={{ color: "var(--color-ok)" }}>
          カードのお支払いが終わりました
        </div>
        <p className="text-lg">会計まで記録しました。</p>
        {loggedIn && (
          <div className="grid grid-cols-2 gap-3">
            <Link href={`/visits/${r.visit}`} className="btn btn-xl btn-primary">
              レシート・領収書
            </Link>
            <Link href="/floor" className="btn btn-xl">
              卓一覧へ
            </Link>
          </div>
        )}
      </>
    );
  }
  const attention = r.status === "needs_attention";
  return (
    <>
      <div className="text-3xl font-bold" style={{ color: attention ? "var(--color-danger)" : "var(--color-warn)" }}>
        {attention ? "要確認" : "カードで払えませんでした"}
      </div>
      <p className="text-lg font-bold">{r.message || r.status_label}</p>
      {loggedIn && (
        <Link href={`/visits/${r.visit}/checkout`} className="btn btn-xl btn-primary w-full">
          会計の画面に戻る
        </Link>
      )}
    </>
  );
}
