/**
 * f1-proxy -- Cloudflare Worker in front of livetiming.formula1.com/static.
 *
 * Why it exists:
 *   1. The VPS IP range is blocked by livetiming, so the API fetches through this Worker.
 *   2. Since 14-15 Oct 2026 every /static/<year>/... object answers 401 unless the request
 *      carries an F1 TV (Access or higher) subscription token as `Authorization: Bearer`.
 *      The token lives here, never on the VPS.
 *
 * Because the Worker attaches a paid account's token, it must not be an open relay:
 * every request needs `X-Proxy-Key: <PROXY_KEY>`.
 *
 * Bindings (wrangler.toml / `wrangler secret put`):
 *   PROXY_KEY   secret, required   shared with the API (`F1_PROXY_KEY` there)
 *   F1TV_TOKEN  secret, optional   subscriptionToken; fallback when KV has none
 *   ADMIN_KEY   secret, optional   enables PUT /_admin/token (rotate without a deploy)
 *   F1TV        KV, optional       holds the rotated token under key "token"
 *
 * Routes:
 *   GET  /<year>/...            proxied to livetiming /static/<year>/...
 *   GET  /SessionInfo.json      root live files (still public upstream)
 *   GET  /_status               token source + expiry (needs X-Proxy-Key)
 *   PUT  /_admin/token          body = raw token (needs X-Admin-Key)
 */

export const ORIGIN = "https://livetiming.formula1.com/static/";
const ROOT_FILES = new Set(["SessionInfo.json", "StreamingStatus.json", "ArchiveStatus.json"]);
const YEAR_PATH = /^(\d{4})\/[^?#]+$/;

const UPSTREAM_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
  Origin: "https://www.formula1.com",
  Referer: "https://www.formula1.com/",
};

const TTL_ROOT_S = 30;
const TTL_CURRENT_YEAR_S = 300;
const TTL_PAST_YEAR_S = 7 * 86400;

/** Constant-time string compare (no early exit on the first differing byte). */
export function safeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/** `exp` of a JWT as epoch seconds, or null when the token is not a decodable JWT. */
export function jwtExpiry(token) {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  try {
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const padded = b64 + "=".repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded));
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

/**
 * Map the request path to an upstream path, or null when it is not one we proxy.
 * `pathname` is percent-encoded (São_Paulo arrives as S%C3%A3o_Paulo) and stays so.
 */
export function upstreamPath(pathname) {
  const path = pathname.replace(/^\/+/, "");
  let decoded;
  try {
    decoded = decodeURIComponent(path);
  } catch {
    return null;
  }
  if (decoded.includes("..") || decoded.includes("\\") || decoded.includes("//")) return null;
  if (ROOT_FILES.has(decoded)) return path;
  if (YEAR_PATH.test(decoded)) return path;
  return null;
}

export function ttlFor(path, nowYear) {
  const m = /^(\d{4})\//.exec(path);
  if (!m) return TTL_ROOT_S;
  return Number(m[1]) < nowYear ? TTL_PAST_YEAR_S : TTL_CURRENT_YEAR_S;
}

async function currentToken(env) {
  if (env.F1TV) {
    const kv = await env.F1TV.get("token");
    if (kv) return { token: kv, source: "kv" };
  }
  if (env.F1TV_TOKEN) return { token: env.F1TV_TOKEN, source: "secret" };
  return { token: null, source: "none" };
}

/**
 * JSON response. Errors carry `X-Proxy-Error` so the API can tell the Worker's own
 * refusals (bad key, bad path) from livetiming's 403 "not published".
 */
function json(status, body, extra = {}) {
  const headers = { "Content-Type": "application/json", "Cache-Control": "no-store", ...extra };
  if (status >= 400 && body.error) headers["X-Proxy-Error"] = body.error;
  return new Response(JSON.stringify(body), { status, headers });
}

function expiryHeaders(exp) {
  return exp ? { "X-F1TV-Token-Expires": new Date(exp * 1000).toISOString() } : {};
}

async function handleStatus(env) {
  const { token, source } = await currentToken(env);
  const exp = jwtExpiry(token);
  const now = Math.floor(Date.now() / 1000);
  return json(200, {
    token_source: source,
    token_expires: exp ? new Date(exp * 1000).toISOString() : null,
    hours_left: exp ? Math.round(((exp - now) / 3600) * 10) / 10 : null,
    expired: exp ? exp <= now : null,
  });
}

async function handleTokenUpdate(request, env) {
  if (!env.ADMIN_KEY || !env.F1TV) return json(404, { error: "token_rotation_disabled" });
  if (!safeEqual(request.headers.get("X-Admin-Key") || "", env.ADMIN_KEY)) {
    return json(403, { error: "forbidden" });
  }
  const token = (await request.text()).trim().replace(/^Bearer\s+/i, "");
  const exp = jwtExpiry(token);
  if (!exp) return json(400, { error: "not_a_jwt", detail: "expected the F1 TV subscriptionToken" });
  if (exp <= Math.floor(Date.now() / 1000)) return json(400, { error: "token_already_expired" });
  await env.F1TV.put("token", token);
  return json(200, { stored: true, token_expires: new Date(exp * 1000).toISOString() });
}

async function handleProxy(request, env, ctx, path) {
  const upstreamUrl = ORIGIN + path;
  const cacheKey = new Request(upstreamUrl, { method: "GET" });
  const cache = globalThis.caches?.default;

  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      const res = new Response(hit.body, hit);
      res.headers.set("X-Proxy-Cache", "HIT");
      return res;
    }
  }

  const { token } = await currentToken(env);
  const headers = new Headers(UPSTREAM_HEADERS);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const upstream = await fetch(upstreamUrl, { method: "GET", headers, redirect: "follow" });
  const exp = jwtExpiry(token);

  if (upstream.status === 401) {
    // Never cached: the next request after a rotation must see the new token.
    return json(
      401,
      {
        error: token ? "f1tv_token_rejected" : "f1tv_token_missing",
        detail: "livetiming /static requires an F1 TV subscription token",
        token_expires: exp ? new Date(exp * 1000).toISOString() : null,
      },
      { "X-Upstream-Auth": token ? "rejected" : "missing", ...expiryHeaders(exp) },
    );
  }

  const outHeaders = new Headers();
  const ct = upstream.headers.get("Content-Type");
  if (ct) outHeaders.set("Content-Type", ct);
  for (const [k, v] of Object.entries(expiryHeaders(exp))) outHeaders.set(k, v);

  if (upstream.status !== 200) {
    // 403/404 mean "not published"; 5xx an outage. Pass through, never cache.
    outHeaders.set("Cache-Control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: outHeaders });
  }

  const ttl = ttlFor(decodeURIComponent(path), new Date().getUTCFullYear());
  outHeaders.set("Cache-Control", `private, max-age=${ttl}`);
  outHeaders.set("X-Proxy-Cache", "MISS");
  const body = await upstream.arrayBuffer();

  if (cache) {
    const toCache = new Response(body, { status: 200, headers: outHeaders });
    toCache.headers.set("Cache-Control", `public, max-age=${ttl}`);
    ctx.waitUntil(cache.put(cacheKey, toCache));
  }
  return new Response(body, { status: 200, headers: outHeaders });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/_admin/token") {
      return request.method === "PUT" ? handleTokenUpdate(request, env) : json(405, { error: "method" });
    }

    if (!env.PROXY_KEY) return json(500, { error: "proxy_key_not_configured" });
    if (!safeEqual(request.headers.get("X-Proxy-Key") || "", env.PROXY_KEY)) {
      return json(403, { error: "forbidden" });
    }

    if (request.method !== "GET" && request.method !== "HEAD") return json(405, { error: "method" });
    if (url.pathname === "/_status") return handleStatus(env);

    const path = upstreamPath(url.pathname);
    if (!path) return json(404, { error: "not_proxied" });
    return handleProxy(request, env, ctx, path);
  },
};
