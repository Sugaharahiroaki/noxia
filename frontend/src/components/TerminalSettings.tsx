"use client";

import { useEffect, useState } from "react";
import { Empty, ErrorBox, Field, SaveStatus, Spinner, type SaveState } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { CardTerminalDevice, TerminalConfig, TerminalMode } from "@/lib/types";
import { useLoad } from "@/lib/useLoad";

const MODE_TEXT: Record<TerminalMode, { text: string; tone: "ok" | "warn" | "danger" | "muted" }> = {
  off: { text: "未接続: 会計画面では、端末に金額を手で入れて決済し、結果を記録します。", tone: "muted" },
  mock: { text: "模擬端末（テスト用）: 会計画面で「承認／拒否」を選びます。実際の決済はしません。", tone: "warn" },
  mock_disabled: { text: "模擬端末はこのサーバーでは使えません（本番サーバーの安全設定）。", tone: "danger" },
  incomplete: { text: "Square の設定が足りません（アクセストークンとロケーションID）。", tone: "danger" },
  sandbox: { text: "Square Sandbox（練習用）に接続します。実際のお金は動きません。", tone: "warn" },
  production: { text: "Square 本番に接続します。端末で実際にカード決済されます。", tone: "ok" },
  production_blocked: {
    text: "本番の設定ですが、サーバー側で送信が止められています（CARD_TERMINAL_LIVE_ENABLED）。運用開始時にサーバー管理者が有効にします。",
    tone: "danger",
  },
};

export function TerminalSettingsTab() {
  const { can, withReauth } = useSession();
  const canEdit = can("settings.edit");
  const cfgQ = useLoad<TerminalConfig>("/api/terminals/config");
  const devQ = useLoad<CardTerminalDevice[]>("/api/terminals/devices");
  const [form, setForm] = useState<TerminalConfig | null>(null);
  const [token, setToken] = useState("");
  const [sigKey, setSigKey] = useState("");
  const [save, setSave] = useState<SaveState>({ kind: "idle" });
  const [newName, setNewName] = useState("");
  const [devErr, setDevErr] = useState<string | null>(null);
  const [devBusy, setDevBusy] = useState(false);

  useEffect(() => {
    if (cfgQ.data && !form) setForm(cfgQ.data);
  }, [cfgQ.data, form]);

  if (!form) return cfgQ.error ? <ErrorBox error={cfgQ.error} onRetry={() => cfgQ.reload(false)} /> : <Spinner />;
  const set = (patch: Partial<TerminalConfig>) => setForm((prev) => (prev ? { ...prev, ...patch } : prev));
  const saved = cfgQ.data ?? form;
  const mode = MODE_TEXT[saved.mode];
  const color = { ok: "var(--color-ok)", warn: "var(--color-warn)", danger: "var(--color-danger)", muted: "var(--color-muted)" }[mode.tone];
  const origin = typeof window !== "undefined" ? window.location.origin : "";

  async function submit() {
    if (!form) return;
    setSave({ kind: "saving" });
    try {
      const body: Record<string, unknown> = {
        provider: form.provider,
        square_environment: form.square_environment,
        square_location_id: form.square_location_id,
        square_webhook_url: form.square_webhook_url,
        note: form.note,
      };
      if (token) body.square_access_token = token;
      if (sigKey) body.square_webhook_signature_key = sigKey;
      const d = await withReauth(() => api<TerminalConfig>("/api/terminals/config", { method: "PUT", body }));
      setForm(d);
      cfgQ.setData(d);
      setToken("");
      setSigKey("");
      setSave({ kind: "saved" });
      devQ.reload();
    } catch (e) {
      setSave({ kind: "error", error: e });
    }
  }

  async function device(fn: () => Promise<unknown>) {
    setDevBusy(true);
    setDevErr(null);
    try {
      await withReauth(fn);
      await devQ.reload();
    } catch (e) {
      setDevErr(errorMessage(e));
    } finally {
      setDevBusy(false);
    }
  }

  const devices = (devQ.data ?? []).filter((d) => d.provider === saved.provider);
  const otherDevices = (devQ.data ?? []).filter((d) => d.provider !== saved.provider);

  return (
    <div className="space-y-4">
      <section className="card space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-bold">カード決済端末との連携</h2>
          <SaveStatus state={save} />
        </div>
        <p className="text-base font-bold" style={{ color }} data-testid="terminal-mode">
          いまの動き: {mode.text}
        </p>
        <p className="text-sm" style={{ color: "var(--color-muted)" }}>
          会計画面の「決済端末に送る」で、金額が NOXIA のサーバーから決済会社を通って店の端末に届きます。PC からでも iPad からでも同じように使えます。
          カード番号は NOXIA には届かず、保存もしません。
        </p>

        <Field label="接続する決済会社">
          <select className="input" value={form.provider} disabled={!canEdit} onChange={(e) => set({ provider: e.target.value as TerminalConfig["provider"] })}>
            <option value="none">未接続（端末に手入力）</option>
            {(form.mock_allowed || form.provider === "mock") && <option value="mock">模擬端末（テスト用・実際の決済なし）</option>}
            <option value="square">Square ターミナル</option>
          </select>
        </Field>

        {form.provider === "square" && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="環境" hint="まず Sandbox（練習用）で動きを確認してから本番に切り替えます。">
              <select className="input" value={form.square_environment} disabled={!canEdit} onChange={(e) => set({ square_environment: e.target.value as TerminalConfig["square_environment"] })}>
                <option value="sandbox">Sandbox（練習用）</option>
                <option value="production">本番</option>
              </select>
            </Field>
            <Field label="ロケーションID" hint="Square の管理画面 → 店舗（ロケーション）で確認できます。">
              <input className="input" value={form.square_location_id} disabled={!canEdit} onChange={(e) => set({ square_location_id: e.target.value })} />
            </Field>
            <Field label="アクセストークン" hint={form.square_access_token_masked ? `保存済み（${form.square_access_token_masked}）。変えるときだけ入力します。` : "Square Developer の管理画面で発行します。"}>
              <input className="input" type="password" autoComplete="off" value={token} disabled={!canEdit} onChange={(e) => setToken(e.target.value)} placeholder={form.square_access_token_masked || ""} />
            </Field>
            <Field label="通知の署名キー（任意）" hint={form.square_webhook_signature_key_masked ? `保存済み（${form.square_webhook_signature_key_masked}）` : "通知を使うときだけ。使わなくても会計画面が数秒おきに結果を確認します。"}>
              <input className="input" type="password" autoComplete="off" value={sigKey} disabled={!canEdit} onChange={(e) => setSigKey(e.target.value)} placeholder={form.square_webhook_signature_key_masked || ""} />
            </Field>
            <Field label="Square に登録した通知先URL（任意）" hint={form.webhook_path ? `登録する URL の例: ${origin.startsWith("https://") ? origin : "https://（公開するドメイン）"}${form.webhook_path}` : undefined}>
              <input className="input" value={form.square_webhook_url} disabled={!canEdit} onChange={(e) => set({ square_webhook_url: e.target.value })} placeholder="https://..." />
            </Field>
          </div>
        )}

        {canEdit && (
          <button className="btn btn-lg btn-primary" onClick={submit} disabled={save.kind === "saving"}>
            保存する
          </button>
        )}
      </section>

      {saved.provider !== "none" && (
        <section className="card space-y-3">
          <h2 className="text-lg font-bold">店の決済端末</h2>
          <ErrorBox error={devQ.error} onRetry={() => devQ.reload(false)} />
          {devices.length === 0 && <Empty>端末がまだありません。下で追加してください。</Empty>}
          <div className="space-y-2">
            {devices.map((d) => (
              <div key={d.id} className="card-2 flex flex-wrap items-center gap-3">
                <div className="flex-1 min-w-[12rem]">
                  <div className="text-lg font-bold">{d.name}</div>
                  <div className="text-sm" style={{ color: d.status === "paired" ? "var(--color-ok)" : "var(--color-muted)" }}>
                    {d.is_active ? d.status_label : "使わない"}
                    {d.device_id && ` ・ ${d.device_id}`}
                  </div>
                  {d.status === "pairing" && d.pairing_code && (
                    <div className="text-base">
                      端末の画面で「サインイン → デバイスコードを使う」を選び、コード <strong className="num text-2xl">{d.pairing_code}</strong> を入力してください。
                    </div>
                  )}
                </div>
                {canEdit && d.provider === "square" && d.status !== "paired" && (
                  <button className="btn" disabled={devBusy} onClick={() => device(() => api(`/api/terminals/devices/${d.id}/pair`, { method: "POST", body: {} }))}>
                    {d.status === "pairing" ? "コードを出し直す" : "ペアリングコードを出す"}
                  </button>
                )}
                {d.status === "pairing" && (
                  <button className="btn btn-primary" disabled={devBusy} onClick={() => device(() => api(`/api/terminals/devices/${d.id}/pair`))}>
                    つながったか確認
                  </button>
                )}
                {canEdit && (
                  <button className="btn btn-sm" disabled={devBusy} onClick={() => device(() => api(`/api/terminals/devices/${d.id}`, { method: "PATCH", body: { is_active: !d.is_active } }))}>
                    {d.is_active ? "使わない" : "使う"}
                  </button>
                )}
              </div>
            ))}
          </div>
          {otherDevices.length > 0 && (
            <p className="text-sm" style={{ color: "var(--color-muted)" }}>
              ほかの接続先で登録した端末 {otherDevices.length} 台は、いまの接続先では使いません。
            </p>
          )}
          {canEdit && (
            <div className="flex gap-2">
              <input className="input flex-1" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="端末名（例: レジ横）" maxLength={40} />
              <button
                className="btn btn-lg"
                disabled={devBusy || !newName.trim()}
                onClick={() =>
                  device(async () => {
                    await api("/api/terminals/devices", { method: "POST", body: { name: newName } });
                    setNewName("");
                  })
                }
              >
                ＋ 端末を追加
              </button>
            </div>
          )}
          {devErr && (
            <p className="text-base font-bold" role="alert" style={{ color: "var(--color-danger)" }}>
              {devErr}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
