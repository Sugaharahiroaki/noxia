/**
 * API クライアント。
 * - 同一オリジンの /api を Next.js が Django にプロキシする（Cookie セッション + CSRF）。
 * - 店舗スコープの API には X-Store-Id を付ける（サーバー側で所属検証される）。
 * - 更新系は Idempotency-Key を付けて二重登録を防ぐ。
 */

export class ApiError extends Error {
  code: string;
  status: number;
  details?: unknown;
  constructor(code: string, message: string, status: number, details?: unknown) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

let storeIdGetter: () => number | null = () => null;
export function setStoreIdGetter(fn: () => number | null) {
  storeIdGetter = fn;
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

let csrfReady: Promise<void> | null = null;
async function ensureCsrf(): Promise<string | null> {
  let token = readCookie("csrftoken");
  if (token) return token;
  if (!csrfReady) {
    csrfReady = fetch("/api/auth/csrf", { credentials: "same-origin" }).then(() => undefined).finally(() => {
      csrfReady = null;
    });
  }
  await csrfReady;
  token = readCookie("csrftoken");
  return token;
}

export function newIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`;
}

export type ApiOptions = {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  idempotencyKey?: string;
  storeId?: number | null | "none";
  signal?: AbortSignal;
};

export const NETWORK_MESSAGE = "通信できません。接続を確認してください。直前の操作が保存されたかどうかは、再接続後に画面を更新して確認してください。";

export async function api<T = unknown>(path: string, opts: ApiOptions = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const token = await ensureCsrf();
    if (token) headers["X-CSRFToken"] = token;
  }
  const storeId = opts.storeId === undefined ? storeIdGetter() : opts.storeId;
  if (storeId && storeId !== "none") headers["X-Store-Id"] = String(storeId);
  if (opts.idempotencyKey) headers["Idempotency-Key"] = opts.idempotencyKey;

  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
      cache: "no-store",
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError("network", NETWORK_MESSAGE, 0);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
    const code = err?.code ?? (res.status === 401 ? "auth_required" : res.status === 403 ? "forbidden" : "error");
    const message = err?.message ?? `サーバーエラー (${res.status})`;
    if (code === "auth_required" && typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("yorumiru:auth-required"));
    }
    throw new ApiError(code, message, res.status, err?.details);
  }
  return data as T;
}

/** CSV などのファイルをダウンロードする（認証ヘッダ付き） */
export async function downloadFile(path: string, filename?: string): Promise<void> {
  const headers: Record<string, string> = {};
  const storeId = storeIdGetter();
  if (storeId) headers["X-Store-Id"] = String(storeId);
  let res: Response;
  try {
    res = await fetch(path, { headers, credentials: "same-origin" });
  } catch {
    throw new ApiError("network", NETWORK_MESSAGE, 0);
  }
  if (!res.ok) {
    let msg = `ダウンロードに失敗しました (${res.status})`;
    let code = "error";
    try {
      const j = await res.json();
      msg = j?.error?.message ?? msg;
      code = j?.error?.code ?? code;
    } catch {
      /* ignore */
    }
    throw new ApiError(code, msg, res.status);
  }
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const m = cd.match(/filename="?([^";]+)"?/);
  const name = filename ?? (m ? m[1] : "download.csv");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export type UploadOptions = {
  /** 二重登録防止キー（同じ操作の再送では同じ値を使う） */
  idempotencyKey?: string;
  /** 送信の進み具合（0〜1） */
  onProgress?: (ratio: number) => void;
  signal?: AbortSignal;
  method?: "POST" | "PUT";
};

/** 証憑・契約書・CSV などのファイルを送る（multipart。CSRF・店舗ヘッダ・Idempotency-Key・進み具合） */
export async function uploadFile<T = unknown>(path: string, file: File | Blob, fields: Record<string, string> = {}, opts: UploadOptions = {}): Promise<T> {
  const form = new FormData();
  form.append("file", file, file instanceof File ? file.name : "upload.jpg");
  for (const [k, v] of Object.entries(fields)) if (v !== "") form.append(k, v);
  const token = await ensureCsrf();
  const storeId = storeIdGetter();
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(opts.method ?? "POST", path);
    xhr.withCredentials = true;
    xhr.setRequestHeader("Accept", "application/json");
    if (token) xhr.setRequestHeader("X-CSRFToken", token);
    if (storeId) xhr.setRequestHeader("X-Store-Id", String(storeId));
    if (opts.idempotencyKey) xhr.setRequestHeader("Idempotency-Key", opts.idempotencyKey);
    if (opts.onProgress) xhr.upload.onprogress = (e) => e.lengthComputable && opts.onProgress!(e.loaded / e.total);
    if (opts.signal) {
      if (opts.signal.aborted) return reject(new DOMException("aborted", "AbortError"));
      opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.onerror = () => reject(new ApiError("network", NETWORK_MESSAGE, 0));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    xhr.onload = () => {
      let data: unknown = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) return resolve(data as T);
      const err = (data as { error?: { code?: string; message?: string; details?: unknown } } | null)?.error;
      const code = err?.code ?? (xhr.status === 401 ? "auth_required" : "error");
      if (code === "auth_required" && typeof window !== "undefined") window.dispatchEvent(new CustomEvent("yorumiru:auth-required"));
      reject(new ApiError(code, err?.message ?? `アップロードに失敗しました (${xhr.status})`, xhr.status, err?.details));
    };
    xhr.send(form);
  });
}

/**
 * 認証付きで画像・PDF を取り、ブラウザ内で表示できる一時 URL を返す（<img src> は X-Store-Id を付けられないため）。
 * 使い終わったら URL.revokeObjectURL で捨てる。
 */
export async function fetchBlobUrl(path: string): Promise<{ url: string; contentType: string }> {
  const headers: Record<string, string> = {};
  const storeId = storeIdGetter();
  if (storeId) headers["X-Store-Id"] = String(storeId);
  let res: Response;
  try {
    res = await fetch(path, { headers, credentials: "same-origin", cache: "no-store" });
  } catch {
    throw new ApiError("network", NETWORK_MESSAGE, 0);
  }
  if (!res.ok) {
    let msg = `ファイルを取得できませんでした (${res.status})`;
    let code = "error";
    try {
      const j = await res.json();
      msg = j?.error?.message ?? msg;
      code = j?.error?.code ?? code;
    } catch {
      /* ignore */
    }
    throw new ApiError(code, msg, res.status);
  }
  const blob = await res.blob();
  return { url: URL.createObjectURL(blob), contentType: res.headers.get("Content-Type") ?? blob.type };
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return "不明なエラーが発生しました。";
}
