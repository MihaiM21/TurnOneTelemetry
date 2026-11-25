// Run: node --test infra/cloudflare/f1-proxy/test/index.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { safeEqual, jwtExpiry, upstreamPath, ttlFor, ORIGIN } from "../src/index.js";

const b64url = (o) => Buffer.from(JSON.stringify(o)).toString("base64url");
const jwt = (exp) => `${b64url({ alg: "HS256" })}.${b64url({ exp })}.sig`;
const future = Math.floor(Date.now() / 1000) + 3 * 86400;
const ctx = { waitUntil: () => {} };

function withFetch(impl, fn) {
  const orig = globalThis.fetch;
  globalThis.fetch = impl;
  return fn().finally(() => { globalThis.fetch = orig; });
}

const req = (path, headers = {}, init = {}) =>
  new Request(`https://f1-proxy.example.workers.dev${path}`, { headers, ...init });

test("safeEqual", () => {
  assert.equal(safeEqual("abc", "abc"), true);
  assert.equal(safeEqual("abc", "abd"), false);
  assert.equal(safeEqual("abc", "abcd"), false);
  assert.equal(safeEqual("", undefined), false);
});

test("jwtExpiry", () => {
  assert.equal(jwtExpiry(jwt(future)), future);
  assert.equal(jwtExpiry("not-a-jwt"), null);
  assert.equal(jwtExpiry(null), null);
});

test("upstreamPath allows season paths and root files only", () => {
  assert.equal(upstreamPath("/2026/Index.json"), "2026/Index.json");
  assert.equal(upstreamPath("/2024/2024-11-03_S%C3%A3o_Paulo_Grand_Prix/2024-11-03_Race/CarData.z.jsonStream"),
    "2024/2024-11-03_S%C3%A3o_Paulo_Grand_Prix/2024-11-03_Race/CarData.z.jsonStream");
  assert.equal(upstreamPath("/SessionInfo.json"), "SessionInfo.json");
  assert.equal(upstreamPath("/2026/../secret"), null);
  assert.equal(upstreamPath("/2026/%2e%2e/x"), null);
  assert.equal(upstreamPath("/etc/passwd"), null);
  assert.equal(upstreamPath("/"), null);
});

test("ttlFor", () => {
  assert.equal(ttlFor("2025/Index.json", 2026), 7 * 86400);
  assert.equal(ttlFor("2026/Index.json", 2026), 300);
  assert.equal(ttlFor("SessionInfo.json", 2026), 30);
});

test("rejects requests without the proxy key", async () => {
  const env = { PROXY_KEY: "k", F1TV_TOKEN: jwt(future) };
  const res = await worker.fetch(req("/2026/Index.json"), env, ctx);
  assert.equal(res.status, 403);
  assert.equal(res.headers.get("X-Proxy-Error"), "forbidden");
  const wrong = await worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "x" }), env, ctx);
  assert.equal(wrong.status, 403);
});

test("refuses to run without PROXY_KEY configured", async () => {
  const res = await worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "" }), {}, ctx);
  assert.equal(res.status, 500);
});

test("forwards the token as a bearer header and passes the body", async () => {
  const token = jwt(future);
  let seen;
  await withFetch(async (url, init) => {
    seen = { url, auth: init.headers.get("Authorization") };
    return new Response('{"Meetings":[]}', { status: 200, headers: { "Content-Type": "application/json" } });
  }, async () => {
    const res = await worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "k" }), { PROXY_KEY: "k", F1TV_TOKEN: token }, ctx);
    assert.equal(res.status, 200);
    assert.equal(await res.text(), '{"Meetings":[]}');
    assert.ok(res.headers.get("X-F1TV-Token-Expires"));
  });
  assert.equal(seen.url, ORIGIN + "2026/Index.json");
  assert.equal(seen.auth, `Bearer ${token}`);
});

test("KV token wins over the secret", async () => {
  const kvToken = jwt(future + 100);
  let auth;
  const env = { PROXY_KEY: "k", F1TV_TOKEN: jwt(future), F1TV: { get: async () => kvToken } };
  await withFetch(async (_u, init) => { auth = init.headers.get("Authorization"); return new Response("{}"); },
    () => worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "k" }), env, ctx));
  assert.equal(auth, `Bearer ${kvToken}`);
});

test("an upstream 401 is labelled and not cacheable", async () => {
  await withFetch(async () => new Response("", { status: 401 }), async () => {
    const res = await worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "k" }), { PROXY_KEY: "k", F1TV_TOKEN: jwt(future) }, ctx);
    assert.equal(res.status, 401);
    assert.equal(res.headers.get("X-Upstream-Auth"), "rejected");
    assert.equal(res.headers.get("Cache-Control"), "no-store");
    assert.equal((await res.json()).error, "f1tv_token_rejected");
  });
  await withFetch(async () => new Response("", { status: 401 }), async () => {
    const res = await worker.fetch(req("/2026/Index.json", { "X-Proxy-Key": "k" }), { PROXY_KEY: "k" }, ctx);
    assert.equal(res.headers.get("X-Upstream-Auth"), "missing");
  });
});

test("403/404 pass through uncached", async () => {
  await withFetch(async () => new Response("nope", { status: 403 }), async () => {
    const res = await worker.fetch(req("/2026/x/y/LapCount.jsonStream", { "X-Proxy-Key": "k" }), { PROXY_KEY: "k" }, ctx);
    assert.equal(res.status, 403);
    assert.equal(res.headers.get("Cache-Control"), "no-store");
    assert.equal(res.headers.get("X-Proxy-Error"), null);
  });
});

test("/_status reports expiry", async () => {
  const res = await worker.fetch(req("/_status", { "X-Proxy-Key": "k" }), { PROXY_KEY: "k", F1TV_TOKEN: jwt(future) }, ctx);
  const body = await res.json();
  assert.equal(body.token_source, "secret");
  assert.equal(body.expired, false);
  assert.ok(body.hours_left > 70);
});

test("PUT /_admin/token stores a valid JWT, rejects junk and expired tokens", async () => {
  const store = {};
  const env = { PROXY_KEY: "k", ADMIN_KEY: "a", F1TV: { get: async (k) => store[k], put: async (k, v) => { store[k] = v; } } };
  const put = (body, key = "a") => worker.fetch(req("/_admin/token", { "X-Admin-Key": key }, { method: "PUT", body }), env, ctx);
  assert.equal((await put(jwt(future), "wrong")).status, 403);
  assert.equal((await put("garbage")).status, 400);
  assert.equal((await put(jwt(1000))).status, 400);
  const ok = await put(`Bearer ${jwt(future)}`);
  assert.equal(ok.status, 200);
  assert.equal(store.token, jwt(future));
});

test("token rotation is disabled without ADMIN_KEY + KV", async () => {
  const res = await worker.fetch(req("/_admin/token", {}, { method: "PUT", body: "x" }), { PROXY_KEY: "k" }, ctx);
  assert.equal(res.status, 404);
});
