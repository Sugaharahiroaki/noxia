"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, errorMessage, newIdempotencyKey } from "@/lib/api";
import { fmtDate, toInt, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { CashCurrent } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";
import { useToast } from "./Toast";
import { NumberPad, Spinner } from "./ui";

/**
 * 開店（レジ開け）カード。開店していないと入店・会計はできないので、卓一覧の一番上に出す。
 * 前日の締めで残した「翌日の釣銭」を引き継いだ金額で、ワンタップで開店できる。
 */
export function OpenRegisterCard({ onOpened }: { onOpened: () => void }) {
  const { can } = useSession();
  const toast = useToast();
  const cur = useLoad<CashCurrent>(can("closing.operate") ? "/api/cash/current" : null);
  const [edit, setEdit] = useState(false);
  const [amount, setAmount] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!can("closing.operate")) {
    return (
      <div className="card text-xl font-bold" style={{ borderColor: "var(--color-warn)", borderWidth: 2 }}>
        開店（レジ開け）がまだです。店長に開店してもらってください。
      </div>
    );
  }
  if (cur.loading || !cur.data) return <Spinner />;
  const d = cur.data;
  if (d.day) return null;
  const closedToday = !!d.previous_day && d.previous_day.date === d.business_date_today;
  if (closedToday) {
    return (
      <div className="card space-y-2" style={{ borderColor: "var(--color-accent)", borderWidth: 2 }}>
        <div className="text-2xl font-bold">本日（営業日 {fmtDate(d.business_date_today)}）のレジ締めは終わっています</div>
        <p className="text-lg" style={{ color: "var(--color-muted)" }}>
          続けて営業する場合は、店長・オーナーが「開店・日締め・現金」で営業日を再開してください。翌日の営業は朝の切替時刻を過ぎると開店できます。
        </p>
        <Link href="/closing" className="btn btn-lg">
          開店・日締め・現金へ
        </Link>
      </div>
    );
  }

  const value = amount ?? String(toInt(d.suggested_opening_float));
  async function open() {
    setBusy(true);
    setError(null);
    try {
      await api("/api/cash/open", { method: "POST", idempotencyKey: newIdempotencyKey(), body: { opening_float: toInt(value) } });
      toast.show({ message: `開店しました（釣銭 ${yen(value)}）` });
      onOpened();
      await cur.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-3" style={{ borderColor: "var(--color-warn)", borderWidth: 2 }} aria-label="開店">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-3xl font-bold">開店（レジ開け）がまだです</div>
          <div className="text-lg" style={{ color: "var(--color-muted)" }}>
            レジの釣銭を確認して開店してください。開店するまで入店・会計はできません（出勤は押せます）。
          </div>
        </div>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 items-center">
        <div>
          <div className="text-lg">釣銭準備金</div>
          <div className="font-bold num" style={{ fontSize: "3.2rem", lineHeight: 1.1 }}>
            {yen(value)}
          </div>
          <div className="text-base" style={{ color: "var(--color-muted)" }}>
            {amount !== null
              ? "変更した金額"
              : d.suggested_from_previous_day && d.previous_day
                ? `前回の締め（${fmtDate(d.previous_day.date)}）で残した翌日の釣銭`
                : "店舗設定の既定の釣銭"}
          </div>
        </div>
        <div className="grid gap-2">
          <button className="btn btn-xl btn-ok" disabled={busy || value === ""} onClick={open}>
            {busy ? "開店中…" : "この金額で開店する"}
          </button>
          <button className="btn btn-lg" onClick={() => setEdit((v) => !v)} disabled={busy}>
            {edit ? "金額の入力を閉じる" : "金額を変える"}
          </button>
        </div>
      </div>
      {edit && (
        <div className="max-w-md">
          <NumberPad value={value} onChange={(v) => setAmount(v)} quick={[30000, 50000, 70000, 100000]} />
        </div>
      )}
      {error && (
        <p className="text-lg font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
          {error}
        </p>
      )}
    </section>
  );
}
