"use client";

import { useEffect, useState } from "react";
import { PrintDialog } from "@/components/Printing";
import { ErrorBox, Field, SaveStatus, Spinner, Toggle, type SaveState } from "@/components/ui";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { PrinterConfig } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

type Editable = Omit<PrinterConfig, "connection" | "shop_name_default" | "updated_at">;

/**
 * レシートプリンターの設定（店舗設定・POS 設定のどちらからも同じ部品）。
 * Bluetooth の接続（ペアリング・プリンターの選択）は iPad とエプソンのアプリ側で行う。ここでは「何を出すか」と印字する店の情報だけ。
 */
export function PrinterSettingsTab() {
  const { can, withReauth } = useSession();
  const canEdit = can("settings.edit");
  const q = useLoad<PrinterConfig>("/api/printing/config");
  const [form, setForm] = useState<PrinterConfig | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [test, setTest] = useState(false);

  useEffect(() => {
    if (q.data && !form) setForm(q.data);
  }, [q.data, form]);

  if (!form) return q.error ? <ErrorBox error={q.error} onRetry={() => q.reload(false)} /> : <Spinner />;
  const set = (patch: Partial<Editable>) => setForm((prev) => (prev ? { ...prev, ...patch } : prev));

  async function submit() {
    if (!form) return;
    setSave({ kind: "saving" });
    try {
      const body: Partial<Editable> = {
        enabled: form.enabled,
        printer_name: form.printer_name,
        model_name: form.model_name,
        paper_width: form.paper_width,
        print_estimate: form.print_estimate,
        print_receipt: form.print_receipt,
        print_ryoshusho: form.print_ryoshusho,
        print_closing: form.print_closing,
        shop_name: form.shop_name,
        address1: form.address1,
        address2: form.address2,
        address3: form.address3,
        phone: form.phone,
        invoice_number: form.invoice_number,
        receipt_message: form.receipt_message,
        ryoshusho_proviso: form.ryoshusho_proviso,
        stamp_box_from: form.stamp_box_from === "" ? null : form.stamp_box_from,
      };
      const d = await withReauth(() => api<PrinterConfig>("/api/printing/config", { method: "PUT", body }));
      setForm(d);
      q.setData(d);
      setSave({ kind: "saved" });
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  const ro = !canEdit;
  return (
    <div className="space-y-4" data-testid="printer-settings">
      <section className="card space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-bold">レシートプリンター（Bluetooth）</h2>
          <SaveStatus state={save} />
        </div>
        <ol className="text-base space-y-1 list-decimal pl-6">
          <li>
            iPad に、エプソンの無料アプリ <strong>「Epson TM Print Assistant」</strong> を App Store から入れます。
          </li>
          <li>
            プリンター（例: TM-m30III）をペアリングできる状態にして（操作はプリンターの説明書のとおり）、iPad の「設定 → Bluetooth」で
            「TM-m30III_…」を選びます。
          </li>
          <li>「TM Print Assistant」を開いて使うプリンターを選び、「通信テスト」で確かめます（一度だけ）。</li>
          <li>下の「プリンターを使う」をオンにして保存し、「テスト印刷」で紙が出るか確かめます。</li>
        </ol>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          印刷するたびにエプソンのアプリに切り替わります。印刷が終わったら、画面の左上の「◀ NOXIA」（Safari で開いているときは「◀ Safari」）で戻ります。
          紙切れ・カバーが開いている・つながらない などはエプソンのアプリが画面に出します。iPad の Safari は Bluetooth の機器に直接つなげないため、この形にしています。
        </p>
        <details className="text-sm" style={{ color: "var(--color-muted)" }}>
          <summary className="cursor-pointer">うまく印刷できないとき</summary>
          <ul className="list-disc pl-6 mt-1 space-y-0.5">
            <li>エプソンの案内では、Bluetooth で使うプリンターは「ハンドシェイク（BUSY 条件）」を「受信バッファフル」にしておきます（プリンターの設定。エプソンの「TM Utility」アプリでも変えられます）。</li>
            <li>iPad をプリンターの USB-C で充電していると Bluetooth が使えないことがあります（プリンターの USB 給電の設定を「Source Fixed」にします）。</li>
            <li>エプソンのアプリで「プリンターが選択されていません」と出たら、手順 3 をやり直します。</li>
          </ul>
        </details>
        <Toggle checked={form.enabled} onChange={(v) => !ro && set({ enabled: v })} label="プリンターを使う" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="プリンター名（画面の表示用）">
            <input className="input" value={form.printer_name} disabled={ro} onChange={(e) => set({ printer_name: e.target.value })} maxLength={40} />
          </Field>
          <Field label="機種（メモ）">
            <input className="input" value={form.model_name} disabled={ro} onChange={(e) => set({ model_name: e.target.value })} maxLength={40} placeholder="例: TM-m30III" />
          </Field>
          <Field label="用紙の幅">
            <select className="input" value={form.paper_width} disabled={ro} onChange={(e) => set({ paper_width: Number(e.target.value) as 80 | 58 })}>
              <option value={80}>80mm</option>
              <option value={58}>58mm</option>
            </select>
          </Field>
        </div>
        <div>
          <div className="label">このプリンターから出すもの</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            <Toggle checked={form.print_estimate} onChange={(v) => !ro && set({ print_estimate: v })} label="概算伝票（いま／延長したら）" />
            <Toggle checked={form.print_receipt} onChange={(v) => !ro && set({ print_receipt: v })} label="会計伝票（レシート）" />
            <Toggle checked={form.print_ryoshusho} onChange={(v) => !ro && set({ print_ryoshusho: v })} label="領収書" />
            <Toggle checked={form.print_closing} onChange={(v) => !ro && set({ print_closing: v })} label="精算伝票（締め）" />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canEdit && (
            <button className="btn btn-lg btn-primary" onClick={submit} disabled={save.kind === "saving"}>
              保存する
            </button>
          )}
          <button className="btn btn-lg" onClick={() => setTest(true)} data-testid="printer-test">
            🖨 テスト印刷
          </button>
        </div>
      </section>

      <section className="card space-y-3">
        <h2 className="text-lg font-bold">レシート・領収書に印字する店の情報</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="店名" hint={`空欄なら「${form.shop_name_default}」`}>
            <input className="input" value={form.shop_name} disabled={ro} onChange={(e) => set({ shop_name: e.target.value })} maxLength={60} placeholder={form.shop_name_default} />
          </Field>
          <Field label="電話番号">
            <input className="input" value={form.phone} disabled={ro} onChange={(e) => set({ phone: e.target.value })} maxLength={30} />
          </Field>
          <Field label="住所 1">
            <input className="input" value={form.address1} disabled={ro} onChange={(e) => set({ address1: e.target.value })} maxLength={60} />
          </Field>
          <Field label="住所 2">
            <input className="input" value={form.address2} disabled={ro} onChange={(e) => set({ address2: e.target.value })} maxLength={60} />
          </Field>
          <Field label="住所 3">
            <input className="input" value={form.address3} disabled={ro} onChange={(e) => set({ address3: e.target.value })} maxLength={60} />
          </Field>
          <Field label="適格請求書発行事業者の登録番号" hint="インボイスの登録をしている場合だけ。T と 13 桁の数字。">
            <input className="input" value={form.invoice_number} disabled={ro} onChange={(e) => set({ invoice_number: e.target.value })} maxLength={14} placeholder="T1234567890123" />
          </Field>
          <Field label="レシートの最後に入れる一言">
            <input className="input" value={form.receipt_message} disabled={ro} onChange={(e) => set({ receipt_message: e.target.value })} maxLength={120} />
          </Field>
        </div>
      </section>

      <section className="card space-y-3">
        <h2 className="text-lg font-bold">領収書</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="但し書きの初期値" hint="発行するときに変えられます。">
            <input className="input" value={form.ryoshusho_proviso} disabled={ro} onChange={(e) => set({ ryoshusho_proviso: e.target.value })} maxLength={40} />
          </Field>
          <Field label="収入印紙の枠を出す金額（現金の領収額がこれ以上のとき・空欄 = 出さない）" hint="印紙が必要な金額は税務署・税理士に確認して入れてください。カード払いの分は数えません。">
            <input
              className="input num"
              inputMode="numeric"
              value={form.stamp_box_from ?? ""}
              disabled={ro}
              onChange={(e) => set({ stamp_box_from: e.target.value.replace(/[^0-9]/g, "") || null })}
            />
          </Field>
        </div>
      </section>

      {test && <PrintDialog title="テスト印刷" path="/api/printing/test" onClose={() => setTest(false)} />}
    </div>
  );
}
