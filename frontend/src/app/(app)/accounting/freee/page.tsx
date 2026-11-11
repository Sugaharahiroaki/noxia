"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { Empty, ErrorBox, Field, PageHeader, Spinner } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, downloadFile } from "@/lib/api";
import { monthRange, thisMonth } from "@/lib/accounting";
import { fmtDate, fmtDateTime, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { FreeeStatus, Journal } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type CsvStatus = { from: string; to: string; total: number; new: number; changed: number; last_exported_at: string | null; can_mark: boolean };

type Mappings = {
  accounts: { key: string; default_name: string; account_name: string; freee_account_item_id: number | null }[];
  taxes: { key: string; label: string; tax_code: number | null; tax_name: string }[];
};

const SYNC_BADGE: Record<string, string> = {
  pending: "badge",
  sending: "badge badge-accent",
  synced: "badge badge-ok",
  failed: "badge badge-danger",
  needs_update: "badge badge-warn",
  needs_delete: "badge badge-warn",
  deleted: "badge",
  unknown: "badge badge-danger",
};

export default function FreeePage() {
  return (
    <Suspense fallback={<Spinner />}>
      <FreeeInner />
    </Suspense>
  );
}

function FreeeInner() {
  const { can, withReauth } = useSession();
  const toast = useToast();
  const params = useSearchParams();
  const status = useLoad<FreeeStatus>("/api/accounting/freee");
  const [month, setMonth] = useState(thisMonth());
  const { from, to } = monthRange(month);
  const journals = useLoad<{ journals: Journal[] }>(`/api/accounting/journals?from=${from}&to=${to}`);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const callbackDone = useRef(false);
  const canManage = can("accounting.freee");

  // freee の認可画面から戻ってきたとき（?code=&state=）
  useEffect(() => {
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state || callbackDone.current || !canManage) return;
    callbackDone.current = true;
    withReauth(() => api<FreeeStatus>("/api/accounting/freee/callback", { method: "POST", body: { code, state } }))
      .then((s) => {
        status.setData(s);
        toast.show({ message: "freee と接続しました" });
        window.history.replaceState(null, "", "/accounting/freee");
      })
      .catch(setErr);
  }, [params, canManage, withReauth, status, toast]);

  async function action(name: string, body: Record<string, unknown> = {}) {
    setBusy(true);
    setErr(null);
    try {
      const r = await withReauth(() => api<FreeeStatus & { authorize_url?: string }>(`/api/accounting/freee/${name}`, { method: "POST", body }));
      if (r.authorize_url) {
        window.location.href = r.authorize_url;
        return;
      }
      status.setData(r);
      if (r.result) toast.show({ message: `freee に送信: 成功 ${r.result.sent}件・失敗 ${r.result.failed}件`, tone: r.result.failed ? "error" : undefined });
      await journals.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  const s = status.data;
  return (
    <div className="space-y-4">
      <PageHeader
        title="freee 連携"
        subtitle="NOXIA で作った仕訳を freee 会計の振替伝票として送ります。同じ仕訳は一度だけ作成し、変更は更新・取消は削除として送ります。送信に失敗したものは再送できます。"
      />
      <ErrorBox error={status.error || err} onRetry={() => status.reload(false)} />
      {status.loading && <Spinner />}
      {s && (
        <div className="card space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className={s.status === "connected" ? "badge badge-ok" : s.status === "error" ? "badge badge-danger" : "badge"} style={{ fontSize: "1rem" }}>
              {s.status === "connected" ? "接続中" : s.status === "error" ? "エラー" : "未接続"}
            </span>
            {s.status === "connected" && (
              <span>
                {s.mode === "mock" ? "模擬接続（freee には送りません・操作の練習用）" : `freee 事業所: ${s.freee_company_name}（ID ${s.freee_company_id}）`}
              </span>
            )}
            {s.connected_at && <span className="text-sm" style={{ color: "var(--color-muted)" }}>接続日時 {fmtDateTime(s.connected_at)}</span>}
            {canManage && (
              <div className="ml-auto flex flex-wrap gap-2">
                <button className="btn" disabled={busy} onClick={() => action("connect", { mode: "mock" })}>
                  模擬接続で試す
                </button>
                <button className="btn btn-primary" disabled={busy || !s.live_enabled} onClick={() => action("connect", { mode: "live" })} title={s.live_enabled ? "" : "サーバー設定（FREEE_LIVE_ENABLED・クライアントID）が必要です"}>
                  freee と接続
                </button>
                {s.status === "connected" && (
                  <button className="btn btn-ghost" disabled={busy} onClick={() => action("disconnect")}>
                    接続を解除
                  </button>
                )}
              </div>
            )}
          </div>
          {!s.live_enabled && (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              freee 本番との接続は、運営者がサーバーに freee のアプリ登録情報（FREEE_CLIENT_ID 等）を設定し FREEE_LIVE_ENABLED=1 にすると使えます。それまでは模擬接続で操作を確認できます。
            </p>
          )}
          {s.last_error && <p style={{ color: "var(--color-danger)" }}>{s.last_error}</p>}
          {canManage && s.status === "connected" && (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="この日以降の仕訳を送る" hint="freee に既に入力済みの期間を送らないための設定です。">
                <input className="input" type="date" defaultValue={s.sync_from ?? ""} onBlur={(e) => action("settings", { sync_from: e.target.value || null })} />
              </Field>
              <button className="btn btn-primary btn-lg" disabled={busy} onClick={() => action("send")}>
                {busy ? "送信中…" : "未送信・変更分を freee に送る"}
              </button>
            </div>
          )}
          <div className="flex flex-wrap gap-2 text-sm">
            {Object.entries(s.sync_counts).map(([k, n]) => (
              <span key={k} className={SYNC_BADGE[k] ?? "badge"}>
                {k}: {n}
              </span>
            ))}
          </div>
        </div>
      )}

      <FreeeCsvExport />

      <Mapping />

      <div className="card space-y-2">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-bold">仕訳ごとの連携状態</h2>
          <input className="input ml-auto" style={{ width: 180 }} type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
        </div>
        <ErrorBox error={journals.error} />
        {journals.data?.journals.length === 0 && <Empty>この月の仕訳はありません（「月次損益・経費・証憑」で仕訳を作ってください）</Empty>}
        {journals.data && journals.data.journals.length > 0 && (
          <table className="table">
            <thead>
              <tr>
                <th>日付</th>
                <th>内容</th>
                <th className="text-right">金額</th>
                <th>状態</th>
                <th>freee ID</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {journals.data.journals.map((j) => (
                <tr key={j.id}>
                  <td className="num">{fmtDate(j.issue_date)}</td>
                  <td>
                    {j.description}
                    {j.last_error && <span className="block text-xs" style={{ color: "var(--color-danger)" }}>{j.last_error}</span>}
                  </td>
                  <td className="text-right num">{yen(j.lines.filter((l) => l.side === "debit").reduce((a, l) => a + Number(l.amount), 0))}</td>
                  <td>
                    <span className={SYNC_BADGE[j.sync_status] ?? "badge"}>{j.sync_status_label}</span>
                  </td>
                  <td className="num">{j.freee_id ?? "—"}</td>
                  <td>
                    {canManage && j.sync_status === "unknown" && <ResolveUnknown syncId={j.sync_id} onDone={() => journals.reload()} />}
                    {canManage && j.sync_status === "failed" && (
                      <button className="btn btn-sm" disabled={busy} onClick={() => action("send", { journal_ids: [j.id] })}>
                        再送
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card text-sm space-y-1" style={{ color: "var(--color-muted)" }}>
        <p className="font-semibold" style={{ color: "var(--color-text)" }}>
          API とファイル取込の使い分け
        </p>
        <p>・まずは上の「freee 取込用 CSV」で渡します（freee の契約だけで使えます。NOXIA 側のアプリ登録は不要）。</p>
        <p>・API（自動送信）は、運営者が freee のアプリ登録を済ませ FREEE_LIVE_ENABLED=1 にしたときに使えます。送るものは同じ仕訳です。</p>
        <p>・freee 側で直接行うもの: 口座の明細の自動取得・消込、固定資産・減価償却・在庫、決算書と申告書の作成、電子申告。</p>
      </div>
    </div>
  );
}

function ResolveUnknown({ syncId, onDone }: { syncId: number; onDone: () => void }) {
  const { withReauth } = useSession();
  const [err, setErr] = useState<unknown>(null);
  return (
    <span className="flex flex-col gap-1">
      <button
        className="btn btn-sm"
        onClick={async () => {
          const id = window.prompt("freee 側にこの仕訳がありましたか？ あった場合は freee の振替伝票IDを、なかった場合は空欄のまま OK を押してください（未送信に戻します）。", "");
          if (id === null) return;
          try {
            await withReauth(() => api(`/api/accounting/freee/syncs/${syncId}/resolve`, { method: "POST", body: { freee_id: id.trim() || null } }));
            onDone();
          } catch (e) {
            setErr(e);
          }
        }}
      >
        確認した
      </button>
      <ErrorBox error={err} />
    </span>
  );
}

function Mapping() {
  const { can, withReauth } = useSession();
  const q = useLoad<Mappings>("/api/accounting/account-mappings");
  const [edit, setEdit] = useState<Mappings | null>(null);
  const [freeeAccounts, setFreeeAccounts] = useState<{ id: number; name: string }[] | null>(null);
  const [freeeTaxes, setFreeeTaxes] = useState<{ code: number; name_ja: string }[] | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const canEdit = can("accounting.freee");
  const d = edit ?? q.data;
  if (!d) return <ErrorBox error={q.error} />;
  return (
    <div className="card space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-bold">勘定科目・税区分の対応付け</h2>
        {canEdit && (
          <button
            className="btn btn-sm ml-auto"
            onClick={async () => {
              try {
                setFreeeAccounts(await api("/api/accounting/freee/master/account-items"));
                setFreeeTaxes(await api("/api/accounting/freee/master/taxes"));
                setEdit(edit ?? q.data);
              } catch (e) {
                setErr(e);
              }
            }}
          >
            freee の科目を読み込んで設定
          </button>
        )}
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        法人と個人事業主で既定の科目名が変わります（例: 経営者からの資金投入は法人「役員借入金」、個人「事業主借」）。freee 本番に送るには、すべての科目と税区分に freee 側の科目・税区分を対応付けてください。税区分の選び方は税理士等に確認してください。
      </p>
      <ErrorBox error={err} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <table className="table text-sm">
          <thead>
            <tr>
              <th>NOXIA の科目</th>
              <th>freee の勘定科目</th>
            </tr>
          </thead>
          <tbody>
            {d.accounts.map((a, i) => (
              <tr key={a.key}>
                <td>{a.default_name}</td>
                <td>
                  {edit && freeeAccounts ? (
                    <select
                      className="input"
                      value={a.freee_account_item_id ?? ""}
                      onChange={(e) => {
                        const id = e.target.value ? Number(e.target.value) : null;
                        const name = freeeAccounts.find((x) => x.id === id)?.name ?? a.default_name;
                        setEdit({ ...edit, accounts: edit.accounts.map((x, j) => (j === i ? { ...x, freee_account_item_id: id, account_name: name } : x)) });
                      }}
                    >
                      <option value="">（未設定）</option>
                      {freeeAccounts.map((x) => (
                        <option key={x.id} value={x.id}>
                          {x.name}
                        </option>
                      ))}
                    </select>
                  ) : a.freee_account_item_id ? (
                    `${a.account_name}（ID ${a.freee_account_item_id}）`
                  ) : (
                    <span style={{ color: "var(--color-muted)" }}>未設定</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <table className="table text-sm">
          <thead>
            <tr>
              <th>NOXIA の税区分</th>
              <th>freee の税区分</th>
            </tr>
          </thead>
          <tbody>
            {d.taxes.map((t, i) => (
              <tr key={t.key}>
                <td>{t.label}</td>
                <td>
                  {edit && freeeTaxes ? (
                    <select
                      className="input"
                      value={t.tax_code ?? ""}
                      onChange={(e) => {
                        const code = e.target.value ? Number(e.target.value) : null;
                        const name = freeeTaxes.find((x) => x.code === code)?.name_ja ?? "";
                        setEdit({ ...edit, taxes: edit.taxes.map((x, j) => (j === i ? { ...x, tax_code: code, tax_name: name } : x)) });
                      }}
                    >
                      <option value="">（未設定）</option>
                      {freeeTaxes.map((x) => (
                        <option key={x.code} value={x.code}>
                          {x.name_ja}
                        </option>
                      ))}
                    </select>
                  ) : t.tax_code !== null ? (
                    `${t.tax_name}（${t.tax_code}）`
                  ) : (
                    <span style={{ color: "var(--color-muted)" }}>未設定</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {edit && (
        <button
          className="btn btn-primary"
          onClick={async () => {
            try {
              const saved = await withReauth(() => api<Mappings>("/api/accounting/account-mappings", { method: "PUT", body: edit }));
              q.setData(saved);
              setEdit(null);
            } catch (e) {
              setErr(e);
            }
          }}
        >
          対応付けを保存
        </button>
      )}
    </div>
  );
}

/** freee の「取引入力 → 振替伝票 → インポート → 他社会計ソフトインポート」で取り込む CSV を出す。出した仕訳には印が付き、次は未出力分だけ出せる。 */
function FreeeCsvExport() {
  const { can } = useSession();
  const toast = useToast();
  const [month, setMonth] = useState(thisMonth());
  const [onlyNew, setOnlyNew] = useState(true);
  const [sjis, setSjis] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const { from, to } = monthRange(month);
  const st = useLoad<CsvStatus>(`/api/accounting/freee/csv-status?from=${from}&to=${to}`, [from, to]);
  const d = st.data;
  const count = d ? (onlyNew ? d.new : d.total) : 0;
  async function dl() {
    setBusy(true);
    setErr(null);
    try {
      const q = `from=${from}&to=${to}${onlyNew ? "&only_new=1" : ""}${sjis ? "&encoding=sjis" : ""}${can("accounting.freee") ? "" : "&mark=0"}`;
      await downloadFile(`/api/accounting/freee.csv?${q}`);
      toast.show({ message: can("accounting.freee") ? `freee 取込用 CSV を出力しました（${count} 件に出力済みの印を付けました）` : "freee 取込用 CSV を出力しました" });
      await st.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="card space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-bold">freee 取込用 CSV（振替伝票）</h2>
        <input className="input ml-auto" style={{ width: 180 }} type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
      </div>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        この月の仕訳を、freee の「取引入力 → 振替伝票 → インポート → 他社会計ソフトインポート」で取り込める形で出します。金額は税込で、消費税額は freee 側で税区分から計算されます。
        出力した仕訳には印が付くので、次からは「未出力の仕訳だけ」を出せば二重に取り込みません（承認後に訂正した仕訳はもう一度「未出力」になります）。
      </p>
      <ErrorBox error={st.error || err} onRetry={() => st.reload(false)} />
      {st.loading && <Spinner />}
      {d && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm">
          <div className="card-2"><div style={{ color: "var(--color-muted)" }}>この月の仕訳</div><div className="font-semibold num">{d.total} 件</div></div>
          <div className="card-2"><div style={{ color: "var(--color-muted)" }}>未出力</div><div className="font-semibold num">{d.new} 件</div></div>
          <div className="card-2"><div style={{ color: "var(--color-muted)" }}>出力後に変わった</div><div className="font-semibold num">{d.changed} 件</div></div>
          <div className="card-2"><div style={{ color: "var(--color-muted)" }}>最後に出力</div><div className="font-semibold">{d.last_exported_at ? fmtDateTime(d.last_exported_at) : "—"}</div></div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={onlyNew} onChange={(e) => setOnlyNew(e.target.checked)} />
          未出力の仕訳だけ
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={sjis} onChange={(e) => setSjis(e.target.checked)} />
          Windows 向け（Shift-JIS）。Mac や Google スプレッドシートで開くなら外す
        </label>
        <button className="btn btn-primary" disabled={busy || !d || count === 0} onClick={dl}>
          {busy ? "出力中…" : `CSV を出力（${count} 件）`}
        </button>
      </div>
      <details className="text-sm" style={{ color: "var(--color-muted)" }}>
        <summary>freee での取り込み方（列の割り当て）</summary>
        <ol className="list-decimal ml-5 mt-1 space-y-1">
          <li>freee 会計で「取引入力」→「振替伝票」→ 右上の「インポート」→「他社会計ソフトインポート」。</li>
          <li>この CSV を選び、列の割り当てで「日付＝取引日」「伝票番号＝伝票番号」「借方勘定科目・借方税区分・借方金額」「貸方勘定科目・貸方税区分・貸方金額」「摘要＝摘要」を指定。「NOXIA仕訳ID」は取り込まなくてよい（控え用）。</li>
          <li>税額は「税込で入力し、税区分から自動計算」を選ぶ。</li>
          <li>勘定科目や税区分の名前が freee と違うときは、取込画面の対応付けで freee 側の科目を選ぶ（NOXIA 側の名前は「科目の対応」で変えられる）。</li>
          <li>同じ伝票番号の行は 1 つの仕訳（複合仕訳）として取り込まれる。</li>
        </ol>
      </details>
    </div>
  );
}
