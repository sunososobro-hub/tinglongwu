// #806: anonymous update-check counter. The desktop app asks GET /v1/latest?v=<app version>&os=<windows|mac|linux>
// once per day (auto-update on only). This Worker returns the GitHub "latest release" JSON unchanged and adds 1 to a
// counter keyed ONLY by (UTC date, app version, OS). It never reads the client IP, the user agent, cookies or any
// other header, and stores nothing else. GET /v1/stats returns the counters for the stats page.
// Deploy: see README.md next to this file. KV binding: COUNTS.

const REPO = "sunososobro-hub/tinglongwu";
const GITHUB_LATEST = `https://api.github.com/repos/${REPO}/releases/latest`;
const OSES = new Set(["windows", "mac", "linux"]);
const VERSION_RE = /^\d{1,3}\.\d{1,3}\.\d{1,4}$/;
const KEEP_DAYS = 400; // counters expire after ~13 months
const CACHE_S = 300; // GitHub answer cached 5 min (keeps us far below the unauthenticated rate limit)

// The only data a counter key holds. Unknown values collapse to "other" so the key space stays small.
export function counterKey(date, version, os) {
  const v = VERSION_RE.test(version || "") ? version : "other";
  const o = OSES.has(os) ? os : "other";
  return `c:${date}:${v}:${o}`;
}

export function utcDate(now) {
  return new Date(now).toISOString().slice(0, 10);
}

export async function bump(kv, key) {
  // KV has no atomic increment; read-modify-write may lose a count under a burst, fine for an estimate.
  const n = parseInt((await kv.get(key)) || "0", 10) + 1;
  await kv.put(key, String(n), { metadata: { n }, expirationTtl: KEEP_DAYS * 86400 });
  return n;
}

export async function stats(kv) {
  const rows = [];
  let cursor;
  do {
    const page = await kv.list({ prefix: "c:", cursor });
    for (const k of page.keys) {
      const [, date, version, os] = k.name.split(":");
      rows.push({ date, version, os, n: (k.metadata && k.metadata.n) || 0 });
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return rows;
}

async function latest(fetchImpl, cache) {
  const req = new Request(GITHUB_LATEST, {
    headers: { "User-Agent": "tlwu-update-counter", Accept: "application/vnd.github+json" },
  });
  let res = cache && (await cache.match(req));
  if (!res) {
    res = await fetchImpl(req);
    if (res.ok && cache) {
      const copy = new Response(res.clone().body, res);
      copy.headers.set("Cache-Control", `max-age=${CACHE_S}`);
      await cache.put(req, copy);
    }
  }
  return res;
}

export async function handle(request, env, deps = {}) {
  const url = new URL(request.url);
  const now = deps.now ? deps.now() : Date.now();
  const fetchImpl = deps.fetch || fetch;
  const cache = deps.cache === undefined ? (typeof caches !== "undefined" ? caches.default : null) : deps.cache;
  if (request.method !== "GET") return new Response("method not allowed", { status: 405 });
  if (url.pathname === "/v1/stats") {
    return Response.json(await stats(env.COUNTS), { headers: { "Access-Control-Allow-Origin": "*" } });
  }
  if (url.pathname !== "/v1/latest") return new Response("not found", { status: 404 });
  const gh = await latest(fetchImpl, cache);
  const body = await gh.text();
  if (gh.ok) {
    // Count only answered checks; a failed counter write must never break the update check.
    try {
      await bump(env.COUNTS, counterKey(utcDate(now), url.searchParams.get("v"), url.searchParams.get("os")));
    } catch (e) {
      /* ignore */
    }
  }
  return new Response(body, { status: gh.status, headers: { "Content-Type": "application/json" } });
}

export default { fetch: (request, env) => handle(request, env) };
