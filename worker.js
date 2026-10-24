// Cloudflare Worker：網站的靜態檔案在 public/（由 build.py 產生），這支程式只處理線上編輯頁 /admin/ 的 API：
//   GET  /admin/api/entries   讀取所有動態
//   POST /admin/api/save      新增或修改一則（{id, ver, date, time, body}，新增時 id 為空）
//   POST /admin/api/delete    刪除一則（{id, ver}）
//   POST /admin/api/upload    上傳一張圖片（{data}，base64），回傳內文用的網址 {url}
// 透過 GitHub API 讀寫 _src/動態/*.md、新增 _src/img/ 的圖片，每次存檔就是一個 commit，Workers Builds 會自動重新部署。
// 解析與改寫原稿的規則和 build.py 的 ENTRY_RE / load_entries / save_entry / delete_entry / save_image 相同，要一起改。
//
// 設定：GITHUB_REPO、ACCESS_TEAM、ACCESS_AUD 寫在 wrangler.jsonc 的 vars；
// GITHUB_TOKEN（只能讀寫這個 repo 內容的 fine-grained token）要在 Cloudflare 後台加成 Secret（見 README.md）。
// 每個請求都會驗證 Cloudflare Access 的登入憑證（JWT），所以就算某個網址（例如預覽網址）沒被 Access 擋住，
// 沒登入的人也改不了。

const SRC = "_src/動態";
const IMG = "_src/img";
const MAX_IMAGE = 10 * 1024 * 1024;  // 上傳圖片的大小上限（build.py 也有一份）
// 一則動態的開頭：「## 2026-10-11」或「## 2026-10-11 21:30」
const ENTRY_RE = /^##[ \t]+(\d{4}-\d{2}-\d{2})(?:[ \tT]+(\d{1,2}:\d{2}))?[ \t]*$/gm;

class UserError extends Error {}

const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"},
});

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    // wrangler.jsonc 的 run_worker_first 只讓 /admin/api/* 進到這裡；其他網址直接由 public/ 的靜態檔案回應
    if (!path.startsWith("/admin/api/")) return env.ASSETS.fetch(request);
    const action = path.slice("/admin/api/".length);
    try {
      if (!await accessEmail(request, env)) return json({error: "沒有登入，或登入已過期，請重新整理頁面"}, 403);
      const gh = github(env);
      if (request.method === "GET" && action === "entries") {
        const entries = (await loadEntries(gh)).map(({id, date, time, body, ver}) => ({id, date, time, body, ver}));
        return json({mode: "online", entries});
      }
      if (request.method === "POST" && ["save", "delete", "upload"].includes(action)) {
        // 只接受編輯頁送來的 JSON（其他網站沒辦法跨站送這種請求）
        if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) return json({error: "拒絕"}, 403);
        const data = await request.json().catch(() => { throw new UserError("資料格式不對"); });
        if (action === "upload") return json({ok: true, url: await saveImage(gh, data)});
        if (action === "save") await saveEntry(gh, data);
        else await deleteEntry(gh, data);
        return json({ok: true});
      }
      return json({error: "找不到"}, 404);
    } catch (err) {
      if (err instanceof UserError) return json({error: err.message}, 400);
      return json({error: "伺服器錯誤：" + err.message}, 500);
    }
  },
};

// ---- Cloudflare Access 登入驗證 ----

const b64bytes = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "=")),
                                      c => c.charCodeAt(0));
const b64text = s => new TextDecoder().decode(b64bytes(s));
let certs = {at: 0, keys: []};

// 驗證通過就回傳登入的 email，否則回傳 null
async function accessEmail(request, env) {
  if (!env.ACCESS_TEAM || !env.ACCESS_AUD) throw new Error("還沒設定 ACCESS_TEAM / ACCESS_AUD");
  const team = env.ACCESS_TEAM.trim().replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const issuer = "https://" + (team.includes(".") ? team : `${team}.cloudflareaccess.com`);
  const token = request.headers.get("Cf-Access-Jwt-Assertion")
    || (request.headers.get("Cookie") || "").match(/(?:^|;\s*)CF_Authorization=([^;]+)/)?.[1];
  const [h, p, s] = (token || "").split(".");
  if (!s) return null;
  let header, payload;
  try { header = JSON.parse(b64text(h)); payload = JSON.parse(b64text(p)); } catch { return null; }
  if (header.alg !== "RS256") return null;
  // Access 的公鑰每 6 週輪替：快取 1 小時，遇到不認得的 kid 就重新抓
  if (Date.now() - certs.at > 3600e3 || !certs.keys.some(k => k.kid === header.kid)) {
    const r = await fetch(`${issuer}/cdn-cgi/access/certs`);
    if (!r.ok) throw new Error(`讀不到 Access 公鑰（${r.status}），請檢查 ACCESS_TEAM`);
    certs = {at: Date.now(), keys: (await r.json()).keys || []};
  }
  const jwk = certs.keys.find(k => k.kid === header.kid);
  if (!jwk) return null;
  const key = await crypto.subtle.importKey("jwk", {kty: jwk.kty, n: jwk.n, e: jwk.e, alg: "RS256", ext: true},
                                            {name: "RSASSA-PKCS1-v1_5", hash: "SHA-256"}, false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64bytes(s), new TextEncoder().encode(`${h}.${p}`));
  const auds = [].concat(payload.aud);
  if (!ok || payload.iss !== issuer || !auds.includes(env.ACCESS_AUD) || !(payload.exp * 1000 > Date.now())) return null;
  return payload.email || null;
}

// ---- GitHub 讀寫 ----

function toB64(bytes) {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function github(env) {
  if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) throw new Error("還沒設定 GITHUB_TOKEN / GITHUB_REPO");
  const branch = env.GITHUB_BRANCH || "main";
  const base = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/`;
  const call = (path, {method = "GET", body, accept = "application/vnd.github+json"} = {}) =>
    fetch(base + path.split("/").map(encodeURIComponent).join("/") + (method !== "PUT" ? `?ref=${encodeURIComponent(branch)}` : ""), {
      method,
      body: body && JSON.stringify(body),
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`, Accept: accept, "User-Agent": "notes-editor",
        "X-GitHub-Api-Version": "2022-11-28", ...(body ? {"Content-Type": "application/json"} : {}),
      },
    });
  const fail = async r => new Error(`GitHub 回應 ${r.status}：${(await r.text()).slice(0, 200)}`);
  return {
    // 原稿資料夾裡的 .md：[{name, path, sha}]，sha 就是檔案版本
    async list() {
      const r = await call(SRC);
      if (r.status === 404) return [];
      if (!r.ok) throw await fail(r);
      return (await r.json()).filter(f => f.type === "file" && f.name.endsWith(".md"));
    },
    async read(path) {
      const r = await call(path, {accept: "application/vnd.github.raw+json"});
      if (!r.ok) throw await fail(r);
      return (await r.text()).replace(/^﻿/, "").replace(/\r\n/g, "\n");
    },
    async write(path, text, sha, message) {
      const r = await call(path, {method: "PUT", body: {message, content: toB64(new TextEncoder().encode(text)), branch,
                                                        ...(sha ? {sha} : {})}});
      if (r.status === 409 || r.status === 422) throw new UserError("檔案剛被改過，請重新整理頁面再試一次");
      if (!r.ok) throw await fail(r);
    },
    // 新增檔案（內容是 base64）；已經有同名的檔案就不動（圖片的檔名來自內容，同名就是同一張圖）
    async create(path, b64, message) {
      if ((await call(path, {method: "HEAD"})).ok) return;
      const r = await call(path, {method: "PUT", body: {message, content: b64, branch}});
      if (!r.ok) throw await fail(r);
    },
  };
}

// ---- 解析與改寫原稿（和 build.py 相同） ----

const normTime = hm => hm ? hm.padStart(5, "0") : "";

async function loadEntries(gh) {
  const files = (await gh.list()).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
  const entries = [], ids = new Set();
  for (const f of files) {
    const text = await gh.read(f.path);
    const heads = [...text.matchAll(ENTRY_RE)];
    heads.forEach((h, i) => {
      const end = i + 1 < heads.length ? heads[i + 1].index : text.length;
      const body = text.slice(h.index + h[0].length, end).trim();
      if (!body) return;
      const date = h[1], time = normTime(h[2]);
      const base = date + (time ? "-" + time.replace(":", "") : "");
      let id = base, n = 1;
      while (ids.has(id)) id = `${base}-${++n}`;
      ids.add(id);
      entries.push({id, date, time, body, ver: f.sha, path: f.path, text, span: [h.index, end]});
    });
  }
  return entries;
}

async function findEntry(gh, id, ver) {
  const e = (await loadEntries(gh)).find(e => e.id === id);
  if (!e || (ver && e.ver !== ver)) throw new UserError("這則動態已經被改過，請重新整理頁面");
  return e;
}

const heading = (date, time) => `${date}${time ? " " + time : ""}`;

async function saveEntry(gh, {id, ver, date, time, body}) {
  body = String(body || "").replace(/\r\n/g, "\n").trim();
  date = String(date || "");
  time = String(time || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || (time && !/^\d{1,2}:\d{2}$/.test(time))) throw new UserError("日期或時間的格式不對");
  if (!body) throw new UserError("內容是空的");
  time = normTime(time);
  let path, text, sha, start, end;
  if (id) {
    const e = await findEntry(gh, id, ver);
    ({path, text} = e);
    sha = e.ver;
    [start, end] = e.span;
  } else {
    path = `${SRC}/${date.slice(0, 4)}.md`;
    const f = (await gh.list()).find(f => f.path === path);
    text = f ? await gh.read(path) : "";
    sha = f && f.sha;
    const m = text.search(ENTRY_RE);
    start = end = m >= 0 ? m : text.length;  // 檔案開頭如果有說明文字，留在原處
  }
  let head = text.slice(0, start);
  const tail = text.slice(end);
  if (head && !head.endsWith("\n\n")) head = head.replace(/\n+$/, "") + "\n\n";
  const block = `## ${heading(date, time)}\n${body}\n`;
  await gh.write(path, head + block + (tail ? "\n" : "") + tail, sha, `${id ? "修改" : "新增"}動態 ${heading(date, time)}`);
}

async function deleteEntry(gh, {id, ver}) {
  const e = await findEntry(gh, id, ver);
  const [start, end] = e.span;
  await gh.write(e.path, e.text.slice(0, start) + e.text.slice(end), e.ver, `刪除動態 ${heading(e.date, e.time)}`);
}

// ---- 圖片（和 build.py 的 image_ext / save_image 相同） ----

// 看檔案開頭判斷圖片格式（不相信瀏覽器說的類型）；不是 JPG、PNG、GIF、WebP 就回傳空字串
function imageExt(bytes) {
  const head = String.fromCharCode(...bytes.subarray(0, 12));
  return head.startsWith("\xff\xd8\xff") ? "jpg" : head.startsWith("\x89PNG") ? "png" : head.startsWith("GIF8") ? "gif"
    : head.startsWith("RIFF") && head.slice(8, 12) === "WEBP" ? "webp" : "";
}

// 存一張圖片，回傳內文用的網址 /img/<年>/<檔名>；檔名取自圖片內容的 SHA-1，同一張圖傳兩次只會存一份
async function saveImage(gh, {data}) {
  let bytes;
  try { bytes = b64bytes(String(data || "")); } catch { throw new UserError("圖片資料不對"); }
  if (bytes.length > MAX_IMAGE) throw new UserError(`圖片太大，上限 ${MAX_IMAGE / 1024 / 1024} MB`);
  const ext = imageExt(bytes);
  if (!ext) throw new UserError("只能上傳 JPG、PNG、GIF、WebP 圖片");
  const hash = [...new Uint8Array(await crypto.subtle.digest("SHA-1", bytes))].map(b => b.toString(16).padStart(2, "0")).join("");
  const year = new Date(Date.now() + 8 * 3600e3).getUTCFullYear();  // 台灣時間的年份
  const name = `${year}/${hash.slice(0, 16)}.${ext}`;
  await gh.create(`${IMG}/${name}`, toB64(bytes), `上傳圖片 ${name}`);
  return `/img/${name}`;
}
