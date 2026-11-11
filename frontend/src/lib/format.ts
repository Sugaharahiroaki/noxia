/** 表示用の整形（計算はしない。金額の計算はサーバー側で確定したものを表示する） */

export function yen(value: string | number | null | undefined, opts: { sign?: boolean } = {}): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  const abs = Math.abs(n);
  const body = "¥" + Math.round(abs).toLocaleString("ja-JP");
  if (n < 0) return "−" + body;
  if (opts.sign && n > 0) return "+" + body;
  return body;
}

export function num(value: string | number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  return n.toLocaleString("ja-JP", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function toInt(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isNaN(n) ? 0 : Math.round(n);
}

const TZ = "Asia/Tokyo";

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: TZ });
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  const [y, m, d] = value.split("-");
  if (!y || !m || !d) return value;
  return `${y}/${Number(m)}/${Number(d)}`;
}

export function fmtMinutes(min: number): string {
  const h = Math.floor(min / 60);
  const m = min % 60;
  return h ? `${h}時間${m ? m + "分" : ""}` : `${m}分`;
}

export function fmtRemaining(seconds: number): string {
  const s = Math.abs(Math.trunc(seconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  const body = m >= 60 ? `${Math.floor(m / 60)}時間${m % 60}分` : `${m}分${String(sec).padStart(2, "0")}秒`;
  return seconds < 0 ? `超過 ${body}` : `残り ${body}`;
}

export function todayJst(): string {
  const now = new Date();
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(now);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function addDays(dateStr: string, days: number): string {
  const d = new Date(dateStr + "T00:00:00+09:00");
  d.setUTCDate(d.getUTCDate() + days);
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

export function monthStart(dateStr: string): string {
  return dateStr.slice(0, 7) + "-01";
}

export const LINE_TYPE_LABELS: Record<string, string> = {
  course_set: "セット",
  extension: "延長",
  course_change: "コース変更",
  nomination: "指名",
  douhan: "同伴",
  product: "商品",
  discount: "値引き",
};

export const ROLE_LABELS: Record<string, string> = {
  operator: "運営者",
  owner: "会社オーナー",
  admin: "会社管理者",
  manager: "店舗管理者",
  staff: "スタッフ",
  cast: "キャスト",
  bookkeeper: "記帳代行担当者",
  tax_advisor: "税理士等",
};
