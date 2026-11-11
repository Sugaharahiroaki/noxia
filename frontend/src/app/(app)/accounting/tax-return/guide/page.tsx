"use client";

/**
 * 国税庁かんたん入力ガイド: 確定申告書等作成コーナーの入力順に、NOXIA の金額をコピーしながら入力し、入力済みにチェックする。
 * 作成コーナーは他社ソフトのデータ（.xtx）を取り込めないため、この転記が主経路。令和8年分の画面項目名は未確認（要確認の印）。
 */
import Link from "next/link";
import { useState } from "react";
import { CopyButton, FreeeHandlesNotice, RequireCaps, Tabs, YearPicker } from "@/components/common";
import { Empty, ErrorBox, PageHeader, Spinner } from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, downloadFile, errorMessage } from "@/lib/api";
import { fmtDateTime, yen } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { Guide, GuideItem } from "@/lib/types_tax";
import { useLoad } from "@/lib/useLoad";

export default function GuidePage() {
  const [year, setYear] = useState(new Date().getFullYear());
  return (
    <RequireCaps caps={["tax.return.view"]}>
      <PageHeader
        title="国税庁 確定申告書等作成コーナー かんたん入力ガイド"
        subtitle="国税庁の画面を開きながら、上から順に金額をコピーして入力してください。入力したらチェックを付けます（進み具合は保存されます）。"
        actions={<YearPicker value={year} onChange={setYear} />}
      />
      <FreeeHandlesNotice what="申告書の作成" />
      <GuideBody key={year} year={year} />
    </RequireCaps>
  );
}

function plain(v: unknown): string {
  if (v === null || v === undefined || v === "") return "";
  if (typeof v === "number" || typeof v === "string") return String(v).replace(/\.00$/, "");
  return "";
}

function GuideBody({ year }: { year: number }) {
  const q = useLoad<Guide>(`/api/tax/returns/${year}/guide`, [year], { refetchOnFocus: false });
  const { can, withReauth } = useSession();
  const toast = useToast();
  const [section, setSection] = useState("business");
  if (q.loading) return <Spinner />;
  if (q.error) return <ErrorBox error={q.error} onRetry={() => q.reload(false)} />;
  const g = q.data;
  if (!g) return <Empty>データがありません</Empty>;

  async function progress(item_key: string, patch: { checked?: boolean; copied?: boolean }) {
    try {
      await api(`/api/tax/returns/${year}/guide/progress`, { method: "PUT", body: { items: [{ item_key, ...patch }] } });
      q.setData({
        ...g!,
        items: g!.items.map((i) => (i.key === item_key ? { ...i, checked: patch.checked ?? i.checked } : i)),
        progress: { ...g!.progress, done: g!.items.filter((i) => (i.key === item_key ? patch.checked ?? i.checked : i.checked)).length },
      });
    } catch (e) {
      toast.show({ message: errorMessage(e), tone: "error" });
    }
  }

  const items = g.items.filter((i) => i.section === section);
  const sec = g.sections.find((s) => s.key === section);
  const pct = g.progress.total ? Math.round((g.progress.done / g.progress.total) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-3">
        <span className="badge badge-accent" style={{ fontSize: "1rem" }}>
          {g.filing === "blue" ? "青色申告（決算書）" : "白色申告（収支内訳書）"}
        </span>
        <span className="text-lg font-bold num">
          進み具合 {g.progress.done} / {g.progress.total}（{pct}%）
        </span>
        <div className="h-3 rounded-full flex-1 min-w-[160px]" style={{ background: "var(--color-panel3)" }}>
          <div className="h-3 rounded-full" style={{ width: `${pct}%`, background: "var(--color-ok)" }} />
        </div>
        {g.computed_at ? (
          <span className="text-sm" style={{ color: "var(--color-muted)" }}>
            計算: {fmtDateTime(g.computed_at)}
          </span>
        ) : (
          <Link href="/accounting/tax-return" className="badge badge-warn">
            まだ計算していません → 確定申告の画面で「計算する」
          </Link>
        )}
        <div className="ml-auto flex gap-2">
          {can("tax.return.export") && (
            <button className="btn btn-sm" onClick={() => withReauth(() => downloadFile(`/api/tax/returns/${year}/guide.pdf`)).catch((e) => toast.show({ message: errorMessage(e), tone: "error" }))}>
              A4 の PDF にする
            </button>
          )}
          {g.links.map((l) => (
            <a key={l.url} className="btn btn-sm" href={l.url} target="_blank" rel="noreferrer">
              {l.label} ↗
            </a>
          ))}
        </div>
      </div>
      {!g.all_verified && (
        <div className="card-2 text-sm">
          ※ 国税庁の令和8年分の画面はまだ公開されていないため、作成コーナー側の項目名は「要確認」です（令和7年分の操作手引きに基づく仮の順番）。公開後に運営者が確認して更新します。
        </div>
      )}
      {g.unsupported.length > 0 && (
        <div className="card" style={{ borderColor: "var(--color-danger)" }} role="alert">
          <p className="font-bold">未対応の項目があります。該当部分は税理士・税務署にご相談ください。</p>
          {g.unsupported.map((u) => (
            <p key={u.code} className="text-sm">
              ・{u.message}
            </p>
          ))}
        </div>
      )}
      <Tabs tabs={g.sections.map((s) => ({ key: s.key, label: s.label, badge: g.items.filter((i) => i.section === s.key && !i.checked).length, tone: "warn" }))} value={section} onChange={setSection} size="sm" />
      {sec && (
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          {sec.intro}
        </p>
      )}
      <ol className="space-y-2">
        {items.map((it) => (
          <GuideRow key={it.key} it={it} onCheck={(v) => progress(it.key, { checked: v })} onCopied={() => progress(it.key, { copied: true })} />
        ))}
      </ol>
      <p className="text-sm" style={{ color: "var(--color-muted)" }}>
        {g.notice}
      </p>
    </div>
  );
}

function GuideRow({ it, onCheck, onCopied }: { it: GuideItem; onCheck: (v: boolean) => void; onCopied: () => void }) {
  const v = it.value;
  const isList = Array.isArray(v);
  const isObj = v !== null && typeof v === "object" && !isList;
  return (
    <li className="card-2 flex flex-wrap items-start gap-3" style={{ opacity: it.checked ? 0.7 : 1 }}>
      <button type="button" className={`btn ${it.checked ? "btn-ok" : ""}`} style={{ minWidth: 64 }} aria-pressed={it.checked} onClick={() => onCheck(!it.checked)}>
        {it.checked ? "✓ 済" : "　"}
      </button>
      <div className="flex-1 min-w-[220px]">
        <div className="font-semibold text-lg">
          {it.order}. {it.label}
        </div>
        <div className="text-sm" style={{ color: "var(--color-muted)" }}>
          作成コーナーの項目: {it.official_label ?? <span className="badge badge-warn">要確認</span>}
          {it.help ? ` ・ ${it.help}` : ""}
        </div>
        {isList && (
          <ul className="text-sm mt-1">
            {(v as Record<string, unknown>[]).slice(0, 14).map((row, i) => (
              <li key={i} className="flex items-center gap-2 num">
                {"month" in row ? `${row.month}: 売上 ${yen(String(row.sales))} / 仕入 ${yen(String(row.purchases))}` : "name" in row ? `${row.name}: ${yen(String(row.amount))}` : JSON.stringify(row)}
                {"amount" in row && <CopyButton value={plain(row.amount)} size="sm" onCopied={onCopied} />}
              </li>
            ))}
          </ul>
        )}
        {isObj && (
          <ul className="text-sm mt-1 num">
            {Object.entries(v as Record<string, unknown>).map(([k, val]) => (
              <li key={k}>
                {k}: {typeof val === "boolean" ? (val ? "はい" : "いいえ") : yen(plain(val) || "0")}
              </li>
            ))}
          </ul>
        )}
      </div>
      {!isList && !isObj && (
        <div className="flex items-center gap-2">
          <span className="text-2xl font-bold num">{typeof v === "number" && !it.copyable ? v : yen(plain(v) || "0")}</span>
          {it.copyable && <CopyButton value={plain(v) || "0"} onCopied={onCopied} />}
        </div>
      )}
    </li>
  );
}
