// #806 Worker tests, no dependencies: `node --test` in this folder (also run by tests/test_update_counter_worker.py).
import { test } from "node:test";
import assert from "node:assert/strict";
import { counterKey, handle } from "./worker.js";

const RELEASE = JSON.stringify({ tag_name: "v0.3.4", prerelease: false, draft: false, assets: [] });

function fakeKV() {
  const m = new Map();
  return {
    m,
    async get(k) { return m.has(k) ? m.get(k).value : null; },
    async put(k, value, opts = {}) { m.set(k, { value, metadata: opts.metadata }); },
    async list({ prefix }) {
      const keys = [...m].filter(([k]) => k.startsWith(prefix)).map(([name, v]) => ({ name, metadata: v.metadata }));
      return { keys, list_complete: true };
    },
  };
}

const NOW = Date.parse("2026-11-14T09:00:00Z");
function call(path, kv, { status = 200, headers = {}, seen = [] } = {}) {
  const req = new Request("https://tlwu-update.example.workers.dev" + path, { headers });
  const fetch = async (r) => { seen.push(r.url); return new Response(RELEASE, { status }); };
  return handle(req, { COUNTS: kv }, { now: () => NOW, fetch, cache: null });
}

test("latest proxies the GitHub answer unchanged and counts (date, version, os)", async () => {
  const kv = fakeKV();
  const seen = [];
  const res = await call("/v1/latest?v=0.3.3&os=windows", kv, { seen });
  assert.equal(res.status, 200);
  assert.equal(await res.text(), RELEASE);
  assert.deepEqual(seen, ["https://api.github.com/repos/sunososobro-hub/tinglongwu/releases/latest"]);
  await call("/v1/latest?v=0.3.3&os=windows", kv);
  assert.deepEqual([...kv.m.keys()], ["c:2026-11-14:0.3.3:windows"]);
  assert.equal(kv.m.get("c:2026-11-14:0.3.3:windows").value, "2");
});

test("stored keys hold only date/version/os, never IP, user agent or other headers", async () => {
  const kv = fakeKV();
  const headers = { "CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "203.0.113.7", "User-Agent": "secret-ua/9",
                    Cookie: "id=abc" };
  await call("/v1/latest?v=0.3.4&os=linux&id=abc123", kv, { headers });
  const dump = JSON.stringify([...kv.m]);
  for (const bad of ["203.0.113", "secret-ua", "abc123", "id=abc"]) assert.ok(!dump.includes(bad), bad);
  for (const k of kv.m.keys()) assert.match(k, /^c:\d{4}-\d{2}-\d{2}:(\d+\.\d+\.\d+|other):(windows|mac|linux|other)$/);
});

test("unknown version / os collapse to other", () => {
  assert.equal(counterKey("2026-11-14", "x<script>", "beos"), "c:2026-11-14:other:other");
  assert.equal(counterKey("2026-11-14", null, null), "c:2026-11-14:other:other");
  assert.equal(counterKey("2026-11-14", "0.3.4", "mac"), "c:2026-11-14:0.3.4:mac");
});

test("a GitHub error is passed through and not counted", async () => {
  const kv = fakeKV();
  const res = await call("/v1/latest?v=0.3.3&os=windows", kv, { status: 403 });
  assert.equal(res.status, 403);
  assert.equal(kv.m.size, 0);
});

test("stats lists the counters; other paths and methods are refused", async () => {
  const kv = fakeKV();
  await call("/v1/latest?v=0.3.3&os=windows", kv);
  await call("/v1/latest?v=0.3.4&os=mac", kv);
  const rows = await (await call("/v1/stats", kv)).json();
  assert.deepEqual(rows, [{ date: "2026-11-14", version: "0.3.3", os: "windows", n: 1 },
                          { date: "2026-11-14", version: "0.3.4", os: "mac", n: 1 }]);
  assert.equal((await call("/", kv)).status, 404);
  const post = await handle(new Request("https://x/v1/latest", { method: "POST" }), { COUNTS: kv }, { cache: null });
  assert.equal(post.status, 405);
});
