"use client";

/** ファイル出力: 帳簿 CSV/Excel、仕訳 CSV、年間損益、申告書類（確定申告の画面へ） */
import Link from "next/link";
import { useState } from "react";
import { RequireCaps, YearPicker } from "@/components/common";
import { useToast } from "@/components/Toast";
import { ErrorBox, PageHeader } from "@/components/ui";
import { downloadFile, errorMessage } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import { useSession } from "@/lib/session";

const LEDGERS = [
  { key: "sales", label: "売上帳" },
  { key: "cash", label: "現金出納帳" },
  { key: "bank", label: "預金出納帳" },
  { key: "receivables", label: "売掛帳（カード・ツケ）" },
  { key: "payables", label: "買掛帳" },
  { key: "purchases", label: "仕入帳" },
  { key: "expenses", label: "経費帳" },
  { key: "staff_payments", label: "スタッフ支払台帳" },
  { key: "trial_balance", label: "試算表" },
];

function jstToday(): string {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Tokyo" });
}

export default function ExportsPage() {
  const { withReauth } = useSession();
  const toast = useToast();
  const today = jstToday();
  const [from, setFrom] = useState(`${today.slice(0, 4)}-01-01`);
  const [to, setTo] = useState(today);
  const [year, setYear] = useState(Number(today.slice(0, 4)));
  const [sjis, setSjis] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const q = `from=${from}&to=${to}`;

  async function dl(key: string, path: string, reauth = false) {
    setBusy(key);
    setErr(null);
    try {
      const run = () => downloadFile(path);
      await (reauth ? withReauth(run) : run());
      toast.show({ message: "ファイルを保存しました" });
    } catch (e) {
      setErr(e);
      toast.show({ message: errorMessage(e), tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <RequireCaps caps={["accounting.view"]}>
      <PageHeader title="ファイル出力" subtitle="帳簿・仕訳・年間損益を CSV / Excel / PDF で保存します。金額は仕訳の集計と必ず一致します。" />
      <ErrorBox error={err} />
      <section className="card space-y-3">
        <h2 className="text-lg font-bold">期間</h2>
        <div className="flex flex-wrap items-center gap-2">
          <input type="date" className="input" style={{ minHeight: 48, width: 180 }} value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} />
          <span>〜</span>
          <input type="date" className="input" style={{ minHeight: 48, width: 180 }} value={to} onChange={(e) => e.target.value && setTo(e.target.value)} />
          <button className="btn btn-sm" onClick={() => { setFrom(`${year}-01-01`); setTo(`${year}-12-31`); }}>
            {year}年の1年分
          </button>
          <button className="btn btn-sm" onClick={() => { const m = today.slice(0, 7); setFrom(`${m}-01`); setTo(today); }}>
            今月
          </button>
          <label className="flex items-center gap-2 ml-2 text-sm">
            <input type="checkbox" checked={sjis} onChange={(e) => setSjis(e.target.checked)} style={{ width: 20, height: 20 }} />
            CSV を Shift_JIS（Excel の古い版・会計ソフト向け）で出す
          </label>
          <span className="text-sm" style={{ color: "var(--color-muted)" }}>
            {fmtDate(from)} 〜 {fmtDate(to)}
          </span>
        </div>
      </section>

      <section className="card space-y-3 mt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-bold">帳簿</h2>
          <button className="btn btn-primary" disabled={busy !== null} onClick={() => dl("all", `/api/accounting/ledgers.xlsx?${q}`)}>
            {busy === "all" ? "作成中…" : "全帳簿を 1 つの Excel で"}
          </button>
        </div>
        <div className="scroll-x">
          <table className="table">
            <thead>
              <tr>
                <th>帳簿</th>
                <th>CSV</th>
                <th>Excel</th>
              </tr>
            </thead>
            <tbody>
              {LEDGERS.map((l) => (
                <tr key={l.key}>
                  <td className="font-semibold">{l.label}</td>
                  <td>
                    <button className="btn btn-sm" disabled={busy !== null} onClick={() => dl(l.key + "csv", `/api/accounting/ledgers/${l.key}.csv?${q}${sjis ? "&sjis=1" : ""}`)}>
                      CSV
                    </button>
                  </td>
                  <td>
                    <button className="btn btn-sm" disabled={busy !== null} onClick={() => dl(l.key + "xlsx", `/api/accounting/ledgers/${l.key}.xlsx?${q}`)}>
                      Excel
                    </button>
                  </td>
                </tr>
              ))}
              <tr>
                <td className="font-semibold">仕訳（借方・貸方の列）</td>
                <td>
                  <button className="btn btn-sm" disabled={busy !== null} onClick={() => dl("journals", `/api/accounting/journals.csv?${q}`)}>
                    CSV
                  </button>
                </td>
                <td className="text-sm" style={{ color: "var(--color-muted)" }}>
                  freee の「他社会計ソフト仕訳のインポート」で列を割り当てて取り込めます（公式テンプレートと同じ並びではありません）。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          総勘定元帳（科目別）は「帳簿」画面で科目を選んで出力してください。
        </p>
      </section>

      <section className="card space-y-3 mt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-bold">年間損益・月次損益・決算整理</h2>
          <YearPicker value={year} onChange={setYear} />
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn" disabled={busy !== null} onClick={() => dl("plx", `/api/closing/years/${year}/pl.xlsx`)}>
            {year}年分 年間損益 Excel（月次・年間・決算整理一覧）
          </button>
          <button className="btn" disabled={busy !== null} onClick={() => dl("plp", `/api/closing/years/${year}/pl.pdf`)}>
            {year}年分 年間損益 PDF
          </button>
          <Link href="/accounting/yearend" className="btn btn-sm">
            年間決算の画面へ
          </Link>
        </div>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          棚卸・減価償却・決算整理を入れてから出すと、申告に使う数字になります。年度を締める前の出力は暫定です。
        </p>
      </section>

      <section className="card space-y-3 mt-4">
        <h2 className="text-lg font-bold">申告書類（収支内訳書・青色申告決算書・確定申告書）</h2>
        <p>
          転記用 PDF・Excel・入力ガイド PDF は
          <Link href="/accounting/tax-return" className="underline mx-1">
            確定申告
          </Link>
          の画面で、計算した版から出力します（経営者本人・パスワードの再確認が必要）。
        </p>
        <ul className="text-sm space-y-1" style={{ color: "var(--color-muted)" }}>
          <li>・XTX（e-Tax の申告データ）は、公式ソフトでの取込試験が完了するまで公開していません。</li>
          <li>・確定申告書等作成コーナーは他社ソフトの .xtx を取り込めません（取り込めるのは作成コーナー自身の .data だけ）。CSV を申告書として取り込むこともできません。作成コーナーには「国税庁 入力ガイド」を見ながら数字を転記してください。</li>
          <li>・申告はご本人が国税庁のシステムで行います。NOXIA から送信することはありません。</li>
        </ul>
        <div className="flex flex-wrap gap-2">
          <Link href="/accounting/tax-return" className="btn btn-primary">
            確定申告の画面へ
          </Link>
          <Link href="/accounting/tax-return/guide" className="btn">
            国税庁 入力ガイド
          </Link>
        </div>
      </section>
    </RequireCaps>
  );
}
