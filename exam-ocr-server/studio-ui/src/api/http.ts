/** Unified Studio HTTP client — auth, timeout, Abort, typed errors. */

import { getStudioToken, promptRelogin } from "../auth/studioToken";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }

  get isConflict(): boolean {
    return this.status === 409;
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }
}

export type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

export type RequestOptions = {
  method?: HttpMethod;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** When true, do not JSON-stringify body (FormData / Blob). */
  rawBody?: BodyInit | null;
  headers?: Record<string, string>;
  /** Skip auth header (unused today; reserved). */
  skipAuth?: boolean;
};

const DEFAULT_TIMEOUT_MS = 120_000;

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  let url = path.startsWith("http") ? path : path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null || v === "") continue;
      qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  return url;
}

function detailMessage(status: number, text: string): { message: string; body: unknown } {
  let body: unknown = text;
  if (!text) return { message: `HTTP ${status}`, body: null };
  try {
    const j = JSON.parse(text);
    body = j;
    const d = j?.detail;
    if (typeof d === "string") {
      try {
        const inner = JSON.parse(d);
        const msg =
          typeof inner?.detail === "string"
            ? inner.detail
            : inner?.detail != null
              ? JSON.stringify(inner.detail)
              : d;
        return { message: msg, body: inner };
      } catch {
        return { message: d, body: j };
      }
    }
    if (d != null) return { message: JSON.stringify(d), body: j };
    if (typeof j?.message === "string") return { message: j.message, body: j };
  } catch {
    /* plain text */
  }
  return { message: text || `HTTP ${status}`, body };
}

/** Conflict (409) — human-friendly Chinese when possible. */
export function formatApiError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) {
      return `冲突：${err.message || "资源状态不允许此操作（例如页范围重叠）"}`;
    }
    if (err.status === 401) {
      return "未授权：请重新登录 Studio";
    }
    return err.message || `请求失败（${err.status}）`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

export async function studioFetch<T = unknown>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const method = opts.method || "GET";
  const url = buildUrl(path, opts.query);
  const headers: Record<string, string> = { ...(opts.headers || {}) };
  if (!opts.skipAuth) {
    const tok = getStudioToken();
    if (tok) headers.Authorization = `Bearer ${tok}`;
  }

  let body: BodyInit | undefined;
  if (opts.rawBody !== undefined && opts.rawBody !== null) {
    body = opts.rawBody;
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = headers["Content-Type"] || "application/json";
    body = JSON.stringify(opts.body);
  }

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), timeoutMs);
  const onOuterAbort = () => ctrl.abort();
  opts.signal?.addEventListener("abort", onOuterAbort);

  try {
    const res = await fetch(url, {
      method,
      headers,
      body,
      signal: ctrl.signal,
    });
    if (res.status === 401) {
      promptRelogin("Studio 令牌无效或已过期");
      throw new ApiError(401, "Unauthorized");
    }
    if (!res.ok) {
      const text = await res.text();
      const { message, body: b } = detailMessage(res.status, text);
      throw new ApiError(res.status, message, b);
    }
    if (res.status === 204) return {} as T;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      return (await res.json()) as T;
    }
    return (await res.text()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, "请求超时或已取消");
    }
    throw e;
  } finally {
    window.clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onOuterAbort);
  }
}
