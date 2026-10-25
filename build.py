"""AI操作員（動態牆）：把原稿 _src/動態/*.md 產生成靜態網站 public/，也能在本機新增、修改、刪除動態。

用法（在哪個資料夾執行都可以）:
    python build.py                 產生 public/（Cloudflare 每次部署也是先執行這行）
    python build.py --edit          在本機用瀏覽器編輯動態（http://localhost:8000/admin/），改完要 commit、push 才會上線
    python build.py --say 內容      用現在的時間發一則動態，並重新產生 public/

原稿 _src/動態/<年>.md：每則以「## 2026-10-11」或「## 2026-10-11 21:30」開頭，到下一個同樣格式的標題為止，
內文是一般 Markdown；檔案裡的先後順序不重要，網頁上新的排前面。

編輯頁上傳的圖片存在 _src/img/<年>/，產生網站時複製到 public/img/，內文用 ![](/img/<年>/<檔名>) 引用。

線上編輯頁 /admin/ 呼叫 worker.js（Cloudflare Worker），透過 GitHub API 改原稿（設定方式見 README.md）。
解析與改寫原稿的規則在那支 JS 裡也有一份，改這裡的 ENTRY_RE / load_entries / save_entry / delete_entry / save_image
時要一起改。
"""
import base64
import binascii
import email.utils
import hashlib
import html
import json
import re
import shutil
import sys
import threading
import urllib.parse
import webbrowser
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "_src" / "動態"  # 動態原稿
IMG = ROOT / "_src" / "img"  # 編輯頁上傳的圖片
OUT = ROOT / "public"  # 產生的網站（不進 git；wrangler.jsonc 的 assets.directory）
TITLE = "AI操作員"  # 網站名稱
SITE_URL = "https://notes.abee-lin-tw.workers.dev"  # 網站的正式網址（留言板用來對應留言串，RSS 的連結也用這個）
DISQUS_SHORTNAME = "ai-operator"  # Disqus 網站代號（disqus.com 註冊時取的）；空的就不顯示留言板和每則的留言
CF_ANALYTICS_TOKEN = ""  # Cloudflare Web Analytics 的 token；空的就不加統計碼（編輯頁一律不加）
HOME_SIZE = 50  # 首頁放最新的幾則，更早的到月份頁看
FEED_SIZE = 30  # RSS 放最新的幾則
MAX_IMAGE = 10 * 1024 * 1024  # 上傳圖片的大小上限（worker.js 也有一份）
TZ = timezone(timedelta(hours=8))  # 台灣時間：RSS 的發文時間、圖片放哪一年的資料夾

STYLE = """<style>
  :root { --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --code:#f5f5f4; --accent:#b91c1c; --hl:#fef3c7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1c1917; --fg:#e7e5e4; --muted:#a8a29e; --line:#44403c; --code:#292524; --accent:#f87171; --hl:#3f2a12; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.8 system-ui, "Segoe UI", "Microsoft JhengHei", sans-serif; }
  header { position:sticky; top:0; z-index:1; background:var(--bg); border-bottom:1px solid var(--line); }
  .bar { max-width:760px; margin:0 auto; padding:14px 24px 0; display:flex; flex-wrap:wrap; gap:8px 16px;
         align-items:center; justify-content:space-between; }
  h1 { font-size:1.3rem; margin:0; }
  input[type=search] { flex:1; max-width:280px; min-width:160px; padding:5px 10px; font:inherit; font-size:.9rem;
         border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--fg); }
  .tabs { flex-basis:100%; display:flex; }
  .tab { padding:2px 14px 8px; margin-bottom:-1px; color:var(--muted); text-decoration:none; border-bottom:2px solid transparent; }
  .tab:hover { color:var(--fg); }
  .tab[aria-current] { color:var(--accent); font-weight:600; border-bottom-color:var(--accent); }
  .tab .n { font-size:.8rem; font-weight:normal; margin-left:4px; opacity:.8; }
  .tab.rss { margin-left:auto; font-size:.85rem; }
  main { max-width:760px; margin:0 auto; padding:0 24px 30vh; }
  .month h2, .week h3 { font-size:.85rem; font-weight:600; color:var(--muted); margin:28px 0 0; }
  .page-h { font-size:1.1rem; margin:24px 0 0; }
  .page-h .n, .months .n { font-size:.85rem; font-weight:normal; color:var(--muted); margin-left:8px; }
  .pager { display:flex; justify-content:space-between; gap:16px; margin:20px 0 0; font-size:.9rem; }
  .pager a, .months a { color:var(--accent); text-decoration:none; }
  .pager a:hover, .months a:hover { text-decoration:underline; }
  .year h2 { font-size:1rem; margin:28px 0 10px; }
  .months { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); gap:8px; }
  .months li { padding:8px 14px; border:1px solid var(--line); border-radius:8px; }
  .hits-n { color:var(--muted); font-size:.9rem; margin:24px 0 0; }
  mark { background:var(--hl); color:inherit; border-radius:2px; }
  .entry { position:relative; margin-left:5px; padding:12px 8px 14px 22px; border-left:2px solid var(--line);
           scroll-margin-top:120px; overflow-wrap:anywhere; }
  .entry::before { content:""; position:absolute; left:-7px; top:20px; width:12px; height:12px; border-radius:50%;
                   background:var(--bg); border:2px solid var(--accent); }
  .entry:target { background:var(--hl); border-radius:0 8px 8px 0; }
  .when { font-size:.85rem; color:var(--muted); }
  .when a { color:inherit; text-decoration:none; }
  .when a:hover { color:var(--accent); text-decoration:underline; }
  .ago:not(:empty)::before { content:" · "; }
  .body > * { margin:.4em 0 0; }
  .body > :first-child { margin-top:2px; }
  .body a { color:var(--accent); }
  .body img { max-width:100%; height:auto; border-radius:6px; }
  .body blockquote { margin-left:0; margin-right:0; padding:2px 14px; border-left:3px solid var(--line); color:var(--muted); }
  .body code { background:var(--code); padding:1px 5px; border-radius:4px; font-size:.92em; }
  .body pre { background:var(--code); padding:12px 16px; border-radius:6px; overflow-x:auto; }
  .body pre code { padding:0; background:none; }
  .empty { color:var(--muted); padding:24px 0; }
  .intro { color:var(--muted); margin:24px 0 8px; }
  .foot { margin-top:6px; font-size:.85rem; }
  .foot a { color:var(--muted); text-decoration:none; }
  .foot a:hover { color:var(--accent); text-decoration:underline; }
  .cmt-toggle::after { content:" ▾"; }
  .cmt-toggle.open::after { content:" ▴"; }
  .entry > #disqus_thread { margin-top:12px; }
  .back { margin:20px 0 4px; font-size:.9rem; }
  .back a { color:var(--accent); text-decoration:none; }
  .cmt-h { font-size:1rem; margin:36px 0 8px; }
  @media (max-width: 600px) { .bar { padding:10px 16px 0; } main { padding:0 16px 30vh; } }
</style>
"""

# 動態列表的頁面：首頁 public/index.html（最新 HOME_SIZE 則）、月份頁 public/<年>/<月>/index.html、
# 彙整 public/archive/index.html；上方的搜尋框搜的是全部動態
PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PTITLE__</title>
__META__
__STYLE__
</head>
<body>
<header><div class="bar">
  <h1>__HEADING__</h1>
  <input type="search" id="q" placeholder="搜尋全部動態…">
  <nav class="tabs">__TABS__</nav>
</div></header>
<main>
<div id="page">
__ENTRIES__
</div>
<div id="hits" hidden></div>
</main>
<script>
const PREFIX = "__PREFIX__";  // 回到網站根目錄的相對路徑
__AGO_JS__
__SEARCH_JS__</script>
__COMMENTS_JS__
</body>
</html>
"""

AGO_JS = """// 把 <span class="ago" data-t="2026-10-11T21:30"> 填成「2 小時前」「昨天」，依打開網頁當下的時間計算
function ago(t) {
  const now = new Date(), d = new Date(t.length > 10 ? t : t + "T00:00"), min = Math.floor((now - d) / 6e4);
  if (min < 0) return "";
  if (t.length > 10 && min < 60) return min < 1 ? "剛剛" : min + " 分鐘前";
  if (t.length > 10 && min < 1440) return Math.floor(min / 60) + " 小時前";
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate())
                         - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  return days === 0 ? "今天" : days === 1 ? "昨天" : days < 30 ? days + " 天前"
       : days < 365 ? Math.floor(days / 30) + " 個月前" : Math.floor(days / 365) + " 年前";
}
document.querySelectorAll(".ago[data-t]").forEach(el => el.textContent = ago(el.dataset.t));
"""

# 搜尋全部動態：第一次打字時才載入 search.js（每則的 id、日期、純文字），結果列在原本的內容的位置
SEARCH_JS = r"""
const q = document.getElementById("q"), page = document.getElementById("page"), hits = document.getElementById("hits");
const escHtml = s => s.replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const escRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
let loading;
// 用 <script> 載入（不用 fetch），直接雙擊用 file:// 打開也能搜尋
const loadIndex = () => loading = loading || new Promise((ok, no) => {
  const s = document.createElement("script");
  s.src = PREFIX + "search.js";
  s.onload = ok;
  s.onerror = () => { loading = null; s.remove(); no(); };
  document.head.append(s);
});
// 從第一個符合的字前面一點開始取一段，符合的字標上底色
function snippet(text, words) {
  const lower = text.toLowerCase(), at = words.map(w => lower.indexOf(w)).filter(i => i >= 0);
  const from = at.length ? Math.max(0, Math.min(...at) - 30) : 0, to = from + 140;
  const marked = text.slice(from, to).split(new RegExp(`(${words.map(escRe).join("|")})`, "i"))
                     .map((s, k) => k % 2 ? `<mark>${escHtml(s)}</mark>` : escHtml(s)).join("");
  return (from ? "…" : "") + marked + (to < text.length ? "…" : "");
}
function showHits(failed) {
  const words = q.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  page.hidden = words.length > 0;
  hits.hidden = !words.length;
  if (!words.length) return;
  if (failed) return hits.innerHTML = '<p class="hits-n">搜尋資料載入失敗，請重新整理頁面再試一次。</p>';
  const found = window.SEARCH.filter(([id, when, text]) => words.every(w => (when + " " + text).toLowerCase().includes(w)));
  hits.innerHTML = `<p class="hits-n">${found.length ? `找到 ${found.length} 則` : "沒有符合的動態。"}`
    + `${found.length > 100 ? "，列出最新的 100 則" : ""}</p>`
    + found.slice(0, 100).map(([id, when, text]) => `<article class="entry"><div class="when">`
      + `<a href="${PREFIX}${id.slice(0, 4)}/${id.slice(5, 7)}/index.html#${id}">${when}</a></div>`
      + `<div class="body"><p>${snippet(text, words)}</p></div></article>`).join("");
}
q.addEventListener("input", async () => {
  if (q.value.trim() && !window.SEARCH) {
    try { await loadIndex(); } catch { return showHits(true); }
  }
  showHits();
});
if (q.value.trim()) q.dispatchEvent(new Event("input"));  // 按上一頁回來時，瀏覽器可能還留著搜尋的字
"""

# 首頁才有：舊的連結 /#2026-10-11 那則已經不在首頁的話，跳到它那個月的頁面
HASH_JS = r"""
const want = decodeURIComponent(location.hash.slice(1));
if (/^\d{4}-\d{2}-\d{2}/.test(want) && !document.getElementById(want)) {
  location.replace(`${want.slice(0, 4)}/${want.slice(5, 7)}/index.html#${want}`);
}
"""

# Disqus 留言串（留言板和每則動態共用）；每個留言串用 identifier 區分，網址只是 Disqus 顯示用
DISQUS_EMBED = """<div id="disqus_thread"></div>
<noscript>要開啟 JavaScript 才看得到留言。</noscript>
<script>
var disqus_config = function () {
  this.page.url = __URL__;
  this.page.identifier = __IDENT__;
  this.page.title = __PTITLE__;
  this.language = "zh_TW";
};
(function () {
  var s = document.createElement("script");
  s.src = "https://__SHORTNAME__.disqus.com/embed.js";
  s.setAttribute("data-timestamp", +new Date());
  (document.head || document.body).appendChild(s);
})();
</script>"""

# 動態牆上展開留言：點「留言」就把唯一的 Disqus 留言串搬到那則下面、換成那則的留言
# （一個網頁只能有一個 Disqus，所以同時只展開一則；用新分頁打開連結則照常到單則頁面）
INLINE_COMMENTS_JS = """<script>
let thread, openLink;
document.addEventListener("click", ev => {
  const a = ev.target.closest("a.cmt-toggle");
  if (!a || ev.button || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
  ev.preventDefault();
  if (a === openLink) {  // 再點一次：收起或重新展開
    thread.hidden = !thread.hidden;
    a.classList.toggle("open", !thread.hidden);
    return;
  }
  if (!thread) {
    thread = document.createElement("div");
    thread.id = "disqus_thread";
  }
  if (openLink) openLink.classList.remove("open");
  openLink = a;
  a.classList.add("open");
  thread.hidden = false;
  a.closest(".entry").append(thread);
  const config = function () {
    this.page.url = a.dataset.url;
    this.page.identifier = a.dataset.disqusIdentifier;
    this.page.title = a.dataset.title;
    this.language = "zh_TW";
  };
  if (window.DISQUS) {
    DISQUS.reset({reload: true, config: config});
  } else {
    window.disqus_config = config;
    const s = document.createElement("script");
    s.src = "https://__SHORTNAME__.disqus.com/embed.js";
    s.setAttribute("data-timestamp", +new Date());
    document.head.appendChild(s);
  }
});
</script>
<script id="dsq-count-scr" src="https://__SHORTNAME__.disqus.com/count.js" async></script>"""

# 單則動態 public/post/<id>/index.html：全文 + 這則的留言（設定 DISQUS_SHORTNAME 才會產生）
POST_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PTITLE__ · __HEADING__</title>
<meta name="description" content="__DESC__">
__META__
__STYLE__
</head>
<body>
<header><div class="bar">
  <h1>__HEADING__</h1>
  <nav class="tabs">__TABS__</nav>
</div></header>
<main>
<p class="back"><a href="../../__BACK__#__ID__">← 回到__BACKNAME__的動態</a></p>
__ENTRY__
<h2 class="cmt-h">留言</h2>
__DISQUS__
</main>
<script>
__AGO_JS__</script>
</body>
</html>
"""

# 留言板 public/guestbook/index.html：留言由 Disqus 代管（設定 DISQUS_SHORTNAME 才會產生）
GUESTBOOK_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>留言板 · __HEADING__</title>
__META__
__STYLE__
</head>
<body>
<header><div class="bar">
  <h1>__HEADING__</h1>
  <nav class="tabs">__TABS__</nav>
</div></header>
<main>
<p class="intro">歡迎留言交流。留言由 Disqus 提供，可以用 Disqus、Google、Facebook 等帳號登入後留言。</p>
__DISQUS__
</main>
</body>
</html>
"""

# RSS 訂閱 public/feed.xml：最新的 FEED_SIZE 則全文，網址都要寫完整
FEED_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
<title>__HEADING__</title>
<link>__SITE__/</link>
<atom:link href="__SITE__/feed.xml" rel="self" type="application/rss+xml"/>
<description>__HEADING__的動態</description>
<language>zh-TW</language>
__ITEMS__
</channel>
</rss>
"""

# 編輯頁 public/admin/index.html：本機（--edit）和線上（Cloudflare Access 保護）共用，
# 資料都從 /admin/api/ 讀寫——本機由下面的 Editor 處理，線上由 worker.js 處理
ADMIN_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>編輯 · __HEADING__</title>
<style>
  :root { color-scheme:light dark; --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --code:#f5f5f4; --accent:#b91c1c; --on-accent:#fff; --hl:#fef3c7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1c1917; --fg:#e7e5e4; --muted:#a8a29e; --line:#44403c; --code:#292524; --accent:#f87171; --on-accent:#1c1917; --hl:#3f2a12; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:16px/1.8 system-ui, "Segoe UI", "Microsoft JhengHei", sans-serif; }
  header { position:sticky; top:0; z-index:1; background:var(--bg); border-bottom:1px solid var(--line); }
  .bar { max-width:760px; margin:0 auto; padding:12px 24px; display:flex; gap:16px; align-items:baseline; justify-content:space-between; }
  h1 { font-size:1.15rem; margin:0; }
  .bar a { color:var(--accent); font-size:.9rem; }
  main { max-width:760px; margin:0 auto; padding:0 24px 30vh; }
  .banner { margin:16px 0 0; padding:6px 12px; border-radius:8px; background:var(--hl); font-size:.85rem; }
  .msg { min-height:1.6em; margin:6px 0 0; font-size:.9rem; color:var(--accent); }
  .edit-form { display:flex; flex-direction:column; gap:8px; margin:12px 0 4px; }
  .edit-form textarea { width:100%; font:inherit; line-height:1.6; padding:8px 10px; resize:vertical;
                        border:1px solid var(--line); border-radius:8px; background:var(--bg); color:var(--fg); }
  .edit-form .row { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
  .edit-form .sp { flex:1; }
  .edit-form input { font:inherit; font-size:.85rem; padding:3px 6px; border:1px solid var(--line); border-radius:6px;
                     background:var(--bg); color:var(--fg); }
  .edit-form button { font:inherit; font-size:.9rem; padding:3px 16px; border-radius:6px; cursor:pointer;
                      border:1px solid var(--accent); background:var(--accent); color:var(--on-accent); }
  .edit-form button.cancel { background:transparent; color:var(--fg); border-color:var(--line); }
  .edit-form .box textarea { display:block; border-radius:0 0 8px 8px; }
  .toolbar { display:flex; flex-wrap:wrap; gap:2px; padding:3px 4px; border:1px solid var(--line); border-bottom:0;
             border-radius:8px 8px 0 0; }
  .edit-form .toolbar button { width:32px; height:30px; padding:0; display:grid; place-items:center; border:0;
                               border-radius:6px; background:none; color:var(--muted); }
  .edit-form .toolbar button:hover { background:var(--code); color:var(--fg); }
  .edit-form .toolbar button[aria-pressed="true"] { color:var(--accent); }
  .toolbar .end { margin-left:auto; }
  .toolbar .sep { width:1px; margin:6px 4px; background:var(--line); }
  .toolbar svg { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
  button:disabled { opacity:.5; cursor:wait; }
  .month h2 { font-size:.85rem; font-weight:600; color:var(--muted); margin:28px 0 0; }
  .entry { position:relative; margin-left:5px; padding:12px 8px 14px 22px; border-left:2px solid var(--line); }
  .entry::before { content:""; position:absolute; left:-7px; top:20px; width:12px; height:12px; border-radius:50%;
                   background:var(--bg); border:2px solid var(--accent); }
  .when { font-size:.85rem; color:var(--muted); }
  .when a { color:inherit; text-decoration:none; }
  .when a:hover { color:var(--accent); text-decoration:underline; }
  .tools { margin-left:8px; }
  .tools button { font:inherit; font-size:.8rem; padding:0 4px; border:0; background:none; color:var(--muted); cursor:pointer; }
  .tools button:hover { color:var(--accent); text-decoration:underline; }
  .body { overflow-wrap:anywhere; }
  .body > * { margin:.4em 0 0; }
  .body > :first-child { margin-top:2px; }
  .body a { color:var(--accent); }
  .body img { max-width:100%; height:auto; border-radius:6px; }
  .body blockquote { margin-left:0; margin-right:0; padding:2px 14px; border-left:3px solid var(--line); color:var(--muted); }
  .body code { background:var(--code); padding:1px 5px; border-radius:4px; font-size:.92em; }
  .body pre { background:var(--code); padding:12px 16px; border-radius:6px; overflow-x:auto; }
  .body pre code { padding:0; background:none; }
  .preview { border:1px dashed var(--line); border-radius:8px; padding:6px 12px 10px; }
  .preview::before { content:"預覽"; display:block; font-size:.75rem; color:var(--muted); }
  .empty { color:var(--muted); padding:24px 0; }
  .more { margin:24px 0 0 5px; font:inherit; font-size:.9rem; padding:4px 16px; border:1px solid var(--line); border-radius:6px;
          background:none; color:var(--fg); cursor:pointer; }
  .more:hover { border-color:var(--accent); color:var(--accent); }
  @media (max-width: 600px) { .bar { padding:10px 16px; } main { padding:0 16px 30vh; } }
</style>
</head>
<body>
<header><div class="bar"><h1>編輯動態 · __HEADING__</h1><a href="/" target="_blank">看網站 ↗</a></div></header>
<main>
  <div class="banner" id="banner">讀取中…</div>
  <div id="new"></div>
  <p class="msg" id="msg" role="status"></p>
  <div id="list"></div>
  <button type="button" class="more" id="more" hidden></button>
</main>
<script src="/admin/marked.js"></script>
<script>
const $ = id => document.getElementById(id);
const pad = n => String(n).padStart(2, "0");
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let mode = "", msgTimer;
const uploaded = {};  // 剛上傳的圖片網址 → 本機的預覽（線上要等網站更新後，那個網址才有圖）
// Markdown 排版（和網站一樣，單一換行也會換行）。網站實際是用 Python-Markdown 產生，少數寫法可能略有不同
const md = t => (window.marked ? marked.parse(t, {breaks: true}) : esc(t).replace(/\n/g, "<br>"))
  .replace(/src="(\/img\/[^"]+)"/g, (m, u) => uploaded[u] ? `src="${uploaded[u]}"` : m);

function say(text) {
  $("msg").textContent = text;
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => $("msg").textContent = "", 8000);
}

async function api(action, data) {
  let r;
  try {
    r = await fetch("/admin/api/" + action, data ? {method: "POST", headers: {"Content-Type": "application/json"},
                                                    body: JSON.stringify(data)} : {cache: "no-store"});
  } catch {
    throw new Error("連不上伺服器，或登入已過期，請重新整理頁面");
  }
  const j = await r.json().catch(() => null);
  if (!j) throw new Error("登入可能已過期，請重新整理頁面");
  if (!r.ok) throw new Error(j.error || r.status);
  return j;
}

// ---- 工具列：在游標位置或選取的文字套上 Markdown 語法 ----
const ICONS = {
  bold: '<path d="M14 12a4 4 0 0 0 0-8H6v8"/><path d="M15 20a4 4 0 0 0 0-8H6v8Z"/>',
  italic: '<line x1="19" y1="4" x2="10" y2="4"/><line x1="14" y1="20" x2="5" y2="20"/><line x1="15" y1="4" x2="9" y2="20"/>',
  strike: '<path d="M16 4H9a3 3 0 0 0-2.83 4"/><path d="M14 12a4 4 0 0 1 0 8H6"/><line x1="4" y1="12" x2="20" y2="12"/>',
  heading: '<path d="M6 12h12"/><path d="M6 20V4"/><path d="M18 20V4"/>',
  quote: '<path d="M10 7H6a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h3v1a3 3 0 0 1-3 3"/><path d="M20 7h-4a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h3v1a3 3 0 0 1-3 3"/>',
  ul: '<line x1="9" y1="6" x2="21" y2="6"/><line x1="9" y1="12" x2="21" y2="12"/><line x1="9" y1="18" x2="21" y2="18"/><circle cx="4" cy="6" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="18" r="1"/>',
  ol: '<line x1="10" y1="6" x2="21" y2="6"/><line x1="10" y1="12" x2="21" y2="12"/><line x1="10" y1="18" x2="21" y2="18"/><path d="M4 6h1v4"/><path d="M4 10h2"/><path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.09-3.09a2 2 0 0 0-2.82 0L6 21"/>',
  code: '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
  codeblock: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m10 9-3 3 3 3"/><path d="m14 15 3-3-3-3"/>',
  hr: '<line x1="4" y1="12" x2="20" y2="12"/>',
  preview: '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
};
const TOOLS = [["bold", "粗體（Ctrl+B）"], ["italic", "斜體（Ctrl+I）"], ["strike", "刪除線"], "|",
               ["heading", "小標題"], ["quote", "引用"], ["ul", "項目清單"], ["ol", "編號清單"], "|",
               ["link", "連結（Ctrl+K）"], ["image", "圖片（也可以直接貼上，或拖進輸入框）"],
               ["code", "行內程式碼"], ["codeblock", "程式碼區塊"], ["hr", "分隔線"]];
const icon = k => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[k]}</svg>`;
const TOOLBAR = TOOLS.map(t => t === "|" ? '<span class="sep"></span>'
                          : `<button type="button" data-k="${t[0]}" title="${t[1]}" aria-label="${t[1]}">${icon(t[0])}</button>`).join("")
  + `<button type="button" class="end" data-k="preview" title="顯示／隱藏預覽" aria-label="顯示／隱藏預覽">${icon("preview")}</button>`;
let showPreview = true;
try { showPreview = localStorage.getItem("notes-preview") !== "off"; } catch {}

// 把 [start, end) 換成 text，再選取 [from, to)；用 insertText 才能用 Ctrl+Z 復原
function put(ta, start, end, text, from, to) {
  ta.focus();
  ta.setSelectionRange(start, end);
  if (!text || !document.execCommand("insertText", false, text)) ta.setRangeText(text, start, end);
  ta.setSelectionRange(from, to);
}
// 網站用的 Python-Markdown 要求清單、分隔線、程式碼區塊前面空一行，不然不會排版
function blankBefore(v, i) {
  if (i === 0 || v.slice(i - 2, i) === "\n\n") return "";
  return v[i - 1] === "\n" ? (i === 1 ? "" : "\n") : "\n\n";
}
function blankAfter(v, i) {
  if (i === v.length || v.slice(i, i + 2) === "\n\n") return "";
  return v[i] === "\n" ? "\n" : "\n\n";
}
// 前後加上記號（已經有就拿掉）
function wrap(ta, before, after, placeholder) {
  const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
  if (s >= before.length && v.slice(s - before.length, s) === before && v.slice(e, e + after.length) === after) {
    const inner = v.slice(s, e);
    return put(ta, s - before.length, e + after.length, inner, s - before.length, s - before.length + inner.length);
  }
  const inner = v.slice(s, e) || placeholder;
  put(ta, s, e, before + inner + after, s + before.length, s + before.length + inner.length);
}
// 選到的每一行開頭加上記號（每行都已經有就拿掉）
const ANY_LIST = /^\s*([-*+]|\d+\.)\s+/;
function lines(ta, prefix, re, list) {
  const v = ta.value, collapsed = ta.selectionStart === ta.selectionEnd;
  const s = v.lastIndexOf("\n", ta.selectionStart - 1) + 1;
  let e = !collapsed && v[ta.selectionEnd - 1] === "\n" ? ta.selectionEnd - 1 : ta.selectionEnd;
  e = v.indexOf("\n", e);
  if (e < 0) e = v.length;
  const ls = v.slice(s, e).split("\n"), filled = ls.filter(l => l.trim());
  const off = filled.length > 0 && filled.every(l => re.test(l));
  let n = 0;
  const out = ls.map(l => off ? l.replace(re, "")
    : l.trim() || ls.length === 1 ? prefix(n++) + l.replace(list ? ANY_LIST : re, "") : l).join("\n");
  const prev = v.slice(v.lastIndexOf("\n", s - 2) + 1, Math.max(s - 1, 0));
  const lead = list && !off && !ANY_LIST.test(prev) ? blankBefore(v, s) : "";
  const end = s + lead.length + out.length;
  put(ta, s, e, lead + out, collapsed ? end : s + lead.length, end);
}
function link(ta) {
  const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd, sel = v.slice(s, e);
  if (/^https?:\/\/\S+$/.test(sel)) return put(ta, s, e, `[連結文字](${sel})`, s + 1, s + 5);  // 選的是網址：選取「連結文字」
  const text = sel || "連結文字";
  put(ta, s, e, `[${text}](https://)`, s + text.length + 3, s + text.length + 11);  // 選取網址的位置
}
function block(ta, before, after, placeholder) {
  const v = ta.value, s = ta.selectionStart, e = ta.selectionEnd, inner = v.slice(s, e) || placeholder;
  const lead = blankBefore(v, s), tail = e === v.length ? "\n\n" : blankAfter(v, e);
  const text = lead + before + inner + after + tail;
  const from = s + lead.length + before.length;
  if (placeholder && inner === placeholder) put(ta, s, e, text, from, from + inner.length);
  else put(ta, s, e, text, s + text.length, s + text.length);
}
const ACTIONS = {
  bold: ta => wrap(ta, "**", "**", "粗體文字"),
  italic: ta => wrap(ta, "*", "*", "斜體文字"),
  strike: ta => wrap(ta, "<del>", "</del>", "刪除的文字"),
  heading: ta => lines(ta, () => "### ", /^#{1,6}\s+/),
  quote: ta => lines(ta, () => "> ", /^>\s?/),
  ul: ta => lines(ta, () => "- ", /^\s*[-*+]\s+/, true),
  ol: ta => lines(ta, i => `${i + 1}. `, /^\s*\d+\.\s+/, true),
  link,
  code: ta => wrap(ta, "`", "`", "程式碼"),
  codeblock: ta => block(ta, "```\n", "\n```", "程式碼"),
  hr: ta => { ta.setSelectionRange(ta.selectionEnd, ta.selectionEnd); block(ta, "---", "", ""); },
};

// ---- 圖片：先在瀏覽器縮小、轉成 WebP 再上傳（重新轉檔也會去掉相片裡的拍攝地點等資料） ----
const MAX_W = 1600, MAX_H = 3200;  // 手機的長截圖維持原本的寬度
const MAX_IMAGE = 10 * 1024 * 1024;  // 和伺服器的上限相同
const images = list => [...list].filter(f => f.type.startsWith("image/"));
async function shrink(file) {
  if (file.type === "image/gif") return file;  // GIF 可能是動圖，轉檔會變成靜止的，所以照原檔上傳
  const img = new Image(), src = URL.createObjectURL(file);
  try {
    img.src = src;
    await img.decode().catch(() => { throw new Error("看不懂這個圖片格式"); });
    const k = Math.min(1, MAX_W / img.naturalWidth, MAX_H / img.naturalHeight);
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(img.naturalWidth * k));
    c.height = Math.max(1, Math.round(img.naturalHeight * k));
    const g = c.getContext("2d");
    g.fillStyle = "#fff";  // 透明的地方填白色
    g.fillRect(0, 0, c.width, c.height);
    g.imageSmoothingQuality = "high";
    g.drawImage(img, 0, 0, c.width, c.height);
    const encode = type => new Promise(ok => c.toBlob(ok, type, 0.85));
    const out = await encode("image/webp");
    return out && out.type === "image/webp" ? out : await encode("image/jpeg");  // Safari 不能存成 WebP，改存 JPG
  } finally {
    URL.revokeObjectURL(src);
  }
}
const base64 = blob => new Promise((ok, no) => {
  const r = new FileReader();
  r.onload = () => ok(r.result.slice(r.result.indexOf(",") + 1));
  r.onerror = () => no(r.error);
  r.readAsDataURL(blob);
});
// 圖片沒放進輸入框時，不要讓瀏覽器直接打開它（會離開這頁，打到一半的字就不見了）
["dragover", "drop"].forEach(t => document.addEventListener(t, ev => {
  if (ev.dataTransfer.types.includes("Files")) ev.preventDefault();
}));

// 新增和修改共用的表單；e 是要修改的那則（新增時是 null）
function editor(e, onCancel) {
  const f = document.createElement("form");
  f.className = "edit-form";
  f.innerHTML = `<div class="box"><div class="toolbar" role="toolbar">${TOOLBAR}</div>
    <textarea placeholder="想說什麼？可以用上面的按鈕，或直接打 Markdown：**粗體**、[連結](網址)、> 引用"></textarea></div>
    <div class="row"><input type="date" required><input type="time" title="不填就只顯示日期"><span class="sp"></span>
    ${onCancel ? '<button type="button" class="cancel">取消</button>' : ""}<button>${e ? "儲存" : "發佈"}</button></div>
    <div class="preview body" hidden></div><input type="file" accept="image/*" multiple hidden>`;
  const [ta, day, time, pick] = f.querySelectorAll("textarea, input");
  const pv = f.querySelector(".preview"), submit = f.querySelector(".row button:not(.cancel)");
  const update = () => {  // 邊打字邊預覽；輸入框跟著內容長高
    ta.rows = Math.max(3, ta.value.split("\n").length + 1);
    pv.hidden = !showPreview || !ta.value.trim();
    if (!pv.hidden) pv.innerHTML = md(ta.value);
    f.querySelector('[data-k="preview"]').setAttribute("aria-pressed", showPreview);
  };
  f.update = update;
  const bar = f.querySelector(".toolbar");
  bar.onmousedown = ev => ev.preventDefault();  // 按工具列時不讓輸入框失去焦點（手機上鍵盤也不會收起）
  bar.onclick = ev => {
    const b = ev.target.closest("button[data-k]");
    if (!b) return;
    if (b.dataset.k === "preview") {
      showPreview = !showPreview;
      try { localStorage.setItem("notes-preview", showPreview ? "on" : "off"); } catch {}
      document.querySelectorAll(".edit-form").forEach(form => form.update());
      return;
    }
    if (b.dataset.k === "image") return pick.click();
    ACTIONS[b.dataset.k](ta);
    update();
  };
  // 一張一張上傳，在游標的位置插入 ![](/img/…)；上傳完之前不能送出，免得發出去的動態少了圖
  let uploading = 0;
  const addImages = async files => {
    if (!files.length) return say("只能放圖片。");
    uploading++;
    submit.disabled = true;
    try {
      for (const [i, file] of files.entries()) {
        say("圖片上傳中…" + (files.length > 1 ? `（${i + 1}/${files.length}）` : ""));
        const blob = await shrink(file);
        if (blob.size > MAX_IMAGE) throw new Error(`圖片太大，上限 ${MAX_IMAGE / 1024 / 1024} MB`);
        const {url} = await api("upload", {data: await base64(blob)});
        uploaded[url] = uploaded[url] || URL.createObjectURL(blob);
        ta.setSelectionRange(ta.selectionEnd, ta.selectionEnd);
        block(ta, `![](${url})`, "", "");
        update();
      }
      say(`圖片已上傳，按「${e ? "儲存" : "發佈"}」後才會出現在網站上。`);
    } catch (err) {
      say("");
      alert("圖片上傳失敗：" + err.message);
    } finally {
      if (!--uploading) submit.disabled = false;
    }
  };
  pick.onchange = () => { addImages(images(pick.files)); pick.value = ""; };
  // 貼上截圖或「複製圖片」就上傳；從 Word、Excel 複製的內容同時也有文字，那就照常貼上文字
  ta.addEventListener("paste", ev => {
    const files = images(ev.clipboardData.files);
    if (!files.length || ev.clipboardData.getData("text/plain")) return;
    ev.preventDefault();
    addImages(files);
  });
  ta.addEventListener("drop", ev => {
    if (!ev.dataTransfer.files.length) return;
    ev.preventDefault();
    addImages(images(ev.dataTransfer.files));
  });
  const setNow = () => {
    const d = new Date();
    day.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    time.value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  let touched = !!e;  // 新增時沒動過日期時間，就用按下發佈當下的時間
  if (e) { ta.value = e.body; day.value = e.date; time.value = e.time; } else setNow();
  update();
  ta.addEventListener("input", update);
  day.oninput = time.oninput = () => touched = true;
  ta.onkeydown = ev => {
    if (!(ev.ctrlKey || ev.metaKey) || ev.altKey) return;
    const k = ev.key === "Enter" ? "submit" : {b: "bold", i: "italic", k: "link"}[ev.key.toLowerCase()];
    if (!k) return;
    ev.preventDefault();
    if (k === "submit") return f.requestSubmit();
    ACTIONS[k](ta);
    update();
  };
  f.onsubmit = async ev => {
    ev.preventDefault();
    if (uploading) return say("圖片還在上傳，請稍等。");
    if (!ta.value.trim()) return ta.focus();
    if (!touched) setNow();
    const buttons = f.querySelectorAll("button");
    buttons.forEach(b => b.disabled = true);
    try {
      await api("save", {id: e ? e.id : "", ver: e ? e.ver : "", date: day.value, time: time.value, body: ta.value});
      if (!e) { ta.value = ""; touched = false; update(); }
      say(saved());
      await load();
    } catch (err) {
      alert("存檔失敗：" + err.message);
    } finally {
      buttons.forEach(b => b.disabled = false);
    }
  };
  if (onCancel) f.querySelector(".cancel").onclick = onCancel;
  return f;
}

const saved = () => mode === "online" ? "已存檔，網站約 1 分鐘後更新。" : "已存檔，也重新產生了網頁。記得 commit、push 才會上線。";

// 列表先放最新的 PAGE 則，按「顯示更早的動態」再多放 PAGE 則（動態多了，一次全部排版手機會很慢）
const PAGE = 50;
let sorted = [], shown = 0, section, ym;
function render(entries) {
  sorted = entries.map(e => ({...e, t: e.date + (e.time ? "T" + e.time : "")}))
                  .sort((a, b) => a.t < b.t ? 1 : a.t > b.t ? -1 : 0);
  $("list").innerHTML = sorted.length ? "" : '<p class="empty">還沒有動態。</p>';
  section = ym = undefined;
  const n = Math.max(shown, PAGE);  // 存檔後重新整理列表，原本展開到哪裡就維持到哪裡
  shown = 0;
  showMore(n);
}
function showMore(n) {
  for (const e of sorted.slice(shown, shown + n)) {
    if (e.t.slice(0, 7) !== ym) {
      ym = e.t.slice(0, 7);
      section = document.createElement("section");
      section.className = "month";
      section.innerHTML = `<h2>${ym.slice(0, 4)} 年 ${+ym.slice(5, 7)} 月</h2>`;
      $("list").append(section);
    }
    const el = document.createElement("article");
    el.className = "entry";
    const label = `${+e.date.slice(5, 7)}/${+e.date.slice(8, 10)}${e.time ? " " + e.time : ""}`;
    const href = `/${e.date.slice(0, 4)}/${e.date.slice(5, 7)}/#${esc(e.id)}`;  // 那個月的頁面
    el.innerHTML = `<div class="when"><a href="${href}" target="_blank" title="在網站上看這則">${label}</a>
      <span class="tools"><button type="button">編輯</button><button type="button">刪除</button></span></div>
      <div class="body">${md(e.body)}</div>`;
    const body = el.querySelector(".body"), tools = el.querySelector(".tools");
    const [edit, del] = tools.querySelectorAll("button");
    edit.onclick = () => {
      const f = editor(e, () => { f.remove(); body.hidden = tools.hidden = false; });
      body.hidden = tools.hidden = true;
      body.after(f);
      f.querySelector("textarea").focus();
    };
    del.onclick = async () => {
      if (!confirm("確定要刪除這則動態嗎？")) return;
      del.disabled = true;
      try { await api("delete", {id: e.id, ver: e.ver}); say(saved().replace("已存檔", "已刪除")); await load(); }
      catch (err) { alert("刪除失敗：" + err.message); del.disabled = false; }
    };
    section.append(el);
  }
  shown = Math.min(shown + n, sorted.length);
  $("more").hidden = shown >= sorted.length;
  $("more").textContent = `顯示更早的動態（還有 ${sorted.length - shown} 則）`;
}
$("more").onclick = () => showMore(PAGE);

async function load() {
  const data = await api("entries");
  mode = data.mode;
  $("banner").textContent = mode === "online"
    ? "線上編輯：存檔會直接 commit 到 GitHub，網站約 1 分鐘後更新。Ctrl+Enter 送出。"
    : "本機編輯：存檔會改 _src/動態/ 並重新產生 public/，要 commit、push 才會上線。Ctrl+Enter 送出。";
  render(data.entries);
}

$("new").append(editor(null));
load().catch(err => $("banner").textContent = "讀取失敗：" + err.message);
</script>
</body>
</html>
"""

# 一則動態的開頭：「## 2026-10-11」或「## 2026-10-11 21:30」
ENTRY_RE = re.compile(r"^##[ \t]+(\d{4}-\d{2}-\d{2})(?:[ \tT]+(\d{1,2}:\d{2}))?[ \t]*$", re.M | re.A)


def read_src(path: Path):
    return path.read_text("utf-8-sig").replace("\r\n", "\n") if path.exists() else ""


def norm_time(hm):
    return f"{int(hm[:-3]):02d}:{hm[-2:]}" if hm else ""


def load_entries():
    """讀取每一則動態（依檔名、檔案裡的順序）。
    id 是網頁上的錨點（網址 # 後面），同一天/同時間多則就加 -2、-3；
    span 是這則在檔案裡的範圍，ver 是整個檔案的版本（編輯時用來確認檔案沒被別人改過）。"""
    entries, ids = [], set()
    for md in sorted(SRC.glob("*.md")):
        text = read_src(md)
        ver = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        heads = list(ENTRY_RE.finditer(text))
        for h, nxt in zip(heads, heads[1:] + [None]):
            end = nxt.start() if nxt else len(text)
            body = text[h.end():end].strip()
            if not body:
                continue
            day, hm = h.group(1), norm_time(h.group(2))
            base = eid = day + (f"-{hm.replace(':', '')}" if hm else "")
            n = 1
            while eid in ids:
                n += 1
                eid = f"{base}-{n}"
            ids.add(eid)
            entries.append({"id": eid, "date": day, "time": hm, "body": body, "ver": ver,
                            "file": md, "span": (h.start(), end)})
    return entries


def tabs(current, prefix, count):
    """網頁上方的分頁列；prefix 是回到網站根目錄的相對路徑（直接雙擊用 file:// 打開也能換頁）。"""
    items = [("動態", "index.html", f'<span class="n">{count}</span>'), ("彙整", "archive/index.html", "")]
    if DISQUS_SHORTNAME:
        items.append(("留言板", "guestbook/index.html", ""))
    return "".join(
        f'<a class="tab" href="{prefix}{href}"' + (' aria-current="page"' if name == current else "") + f'>{name}{extra}</a>'
        for name, href, extra in items
    ) + f'<a class="tab rss" href="{prefix}feed.xml" title="用 RSS 閱讀器訂閱">RSS</a>'


def head_meta(prefix):
    """公開頁面的 <head> 要多加的：RSS 的位置（讓 RSS 閱讀器自動找到），和 Cloudflare Web Analytics 的統計碼。"""
    meta = f'<link rel="alternate" type="application/rss+xml" title="{html.escape(TITLE)}" href="{prefix}feed.xml">'
    if CF_ANALYTICS_TOKEN:
        meta += ('\n<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
                 f"data-cf-beacon='{json.dumps({'token': CF_ANALYTICS_TOKEN})}'></script>")
    return meta


def img_urls(h, prefix):
    """內文裡的圖片網址 /img/… 換成以 prefix 開頭（相對路徑讓 file:// 也看得到圖；RSS 要完整網址）。"""
    return re.sub(r'(src|href)="/img/', lambda m: f'{m[1]}="{prefix}img/', h)


def week_of(day):
    """day（2026-10-24）所在那一週的範圍，週一到週日，只算同一個月的日子，例如「10/19 – 10/25」「10/1 – 10/4」。"""
    d = date.fromisoformat(day)
    first = d.replace(day=1)
    last = (first + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    start = max(d - timedelta(days=d.weekday()), first)
    end = min(start + timedelta(days=6 - start.weekday()), last)
    return f"{d.month}/{start.day}" + (f" – {d.month}/{end.day}" if end != start else "")


def month_name(ym):
    return f"{ym[:4]} 年 {int(ym[5:])} 月"


def month_href(ym):
    """月份頁相對於網站根目錄的路徑，例如 2026/10/index.html。"""
    return f"{ym[:4]}/{ym[5:]}/index.html"


def build():
    """產生 public/index.html（動態牆，最新 HOME_SIZE 則）、public/<年>/<月>/index.html（每個月的動態，依週分段）、
    public/archive/index.html（彙整）、public/search.js（搜尋用）、public/feed.xml（RSS）、public/img/（圖片）、
    public/admin/index.html（編輯頁），
    有設定 Disqus 時再加上 public/post/<id>/index.html（每則動態的留言）與 public/guestbook/index.html（留言板）。
    每則留言串的 identifier 是「entry-<動態 id>」，動態 id 由日期時間組成，所以改了動態的日期時間，舊留言就對不上了。"""
    import markdown

    SRC.mkdir(parents=True, exist_ok=True)
    (SRC / ".gitkeep").touch(exist_ok=True)  # 讓空資料夾也能進 git
    entries = []
    for e in load_entries():
        _, m, d = e["date"].split("-")
        entries.append({
            "id": e["id"],
            "t": e["date"] + (f"T{e['time']}" if e["time"] else ""),
            "label": f"{int(m)}/{int(d)}" + (f" {e['time']}" if e["time"] else ""),
            "html": markdown.markdown(e["body"], extensions=["extra", "sane_lists", "nl2br"])
                    .replace("<img ", '<img loading="lazy" '),  # 捲到附近才載入圖片
        })
    entries.sort(key=lambda e: e["t"], reverse=True)
    for e in entries:
        e["text"] = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", e["html"])).split())
        e["short"] = (e["text"][:30] + ("…" if len(e["text"]) > 30 else "")) or "（圖片）"

    def article(e, when, foot="", prefix=""):
        return (f'<article class="entry" id="{e["id"]}"><div class="when">{when}'
                f'<span class="ago" data-t="{e["t"]}"></span></div>\n'
                f'<div class="body">{img_urls(e["html"], prefix)}</div>{foot}</article>\n')

    def comments_link(e, prefix):
        # Disqus 的 count.js 會把連結文字換成留言數（文字在 Disqus 後台 Community → Comment Count Link 設定）
        if not DISQUS_SHORTNAME:
            return ""
        return (f'<div class="foot"><a class="cmt-toggle" href="{prefix}post/{e["id"]}/index.html#disqus_thread" '
                f'data-disqus-identifier="entry-{e["id"]}" data-url="{SITE_URL}/post/{e["id"]}/" '
                f'data-title="{html.escape(e["short"])} · {html.escape(TITLE)}">留言</a></div>')

    def items(group, prefix):
        return "".join(article(e, f'<a href="#{e["id"]}">{e["label"]}</a>', comments_link(e, prefix), prefix)
                       for e in group)

    comments_js = INLINE_COMMENTS_JS.replace("__SHORTNAME__", DISQUS_SHORTNAME) if DISQUS_SHORTNAME else ""

    def list_page(path, prefix, title, current, content, extra_js=""):
        """首頁、月份頁、彙整共用的頁面：上方有搜尋全部動態的搜尋框和分頁列。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            PAGE_TEMPLATE.replace("__STYLE__", STYLE)
            .replace("__META__", head_meta(prefix))
            .replace("__PTITLE__", html.escape(title))
            .replace("__HEADING__", html.escape(TITLE))
            .replace("__TABS__", tabs(current, prefix, len(entries)))
            .replace("__PREFIX__", prefix)
            .replace("__AGO_JS__", AGO_JS)
            .replace("__SEARCH_JS__", SEARCH_JS + extra_js)
            .replace("__COMMENTS_JS__", comments_js)
            .replace("__ENTRIES__", content or '<p class="empty">還沒有動態。</p>'), "utf-8")

    # 首頁：最新 HOME_SIZE 則，依月份分段，最下面連到下一則所在的月份頁
    home = [f'<section class="month"><h2>{month_name(ym)}</h2>\n{items(group, "")}</section>'
            for ym, group in groupby(entries[:HOME_SIZE], key=lambda e: e["t"][:7])]
    if len(entries) > HOME_SIZE:
        ym = entries[HOME_SIZE]["t"][:7]
        home.append(f'<nav class="pager"><span></span><a href="{month_href(ym)}">更早的動態：{month_name(ym)} →</a></nav>')
    list_page(OUT / "index.html", "", TITLE, "動態", "\n".join(home), HASH_JS)

    # 月份頁：那個月的全部動態，依週分段；上下有前後月份的連結。每次整個重建（刪掉的動態，頁面也會一起消失）
    for d in OUT.glob("[0-9][0-9][0-9][0-9]"):
        shutil.rmtree(d)
    months = [(ym, list(group)) for ym, group in groupby(entries, key=lambda e: e["t"][:7])]
    for k, (ym, group) in enumerate(months):
        newer, older = months[k - 1][0] if k else "", months[k + 1][0] if k + 1 < len(months) else ""
        pager = ('<nav class="pager">'
                 + (f'<a href="../../{month_href(newer)}">← {month_name(newer)}</a>' if newer else "<span></span>")
                 + (f'<a href="../../{month_href(older)}">{month_name(older)} →</a>' if older else "<span></span>")
                 + "</nav>") if newer or older else ""
        weeks = "\n".join(f'<section class="week"><h3>{week}</h3>\n{items(g, "../../")}</section>'
                          for week, g in groupby(group, key=lambda e: week_of(e["t"][:10])))
        list_page(OUT / month_href(ym), "../../", f"{month_name(ym)} · {TITLE}", "彙整",
                  f'<h2 class="page-h">{month_name(ym)}<span class="n">{len(group)} 則</span></h2>\n'
                  f"{pager}\n{weeks}\n{pager}")

    # 彙整：每年每個月各有幾則
    years = "\n".join(
        f'<section class="year"><h2>{y} 年</h2><ul class="months">'
        + "".join(f'<li><a href="../{month_href(ym)}">{int(ym[5:])} 月</a><span class="n">{len(g)} 則</span></li>'
                  for ym, g in ms) + "</ul></section>"
        for y, ms in groupby(months, key=lambda m: m[0][:4]))
    list_page(OUT / "archive" / "index.html", "../", f"彙整 · {TITLE}", "彙整",
              years and f'<h2 class="page-h">彙整<span class="n">共 {len(entries)} 則</span></h2>\n{years}')

    # 搜尋用的資料：每則的 id、完整日期、純文字
    data = [[e["id"], f"{e['t'][:4]}/{e['label']}", e["text"] or "（圖片）"] for e in entries]
    (OUT / "search.js").write_text(
        "window.SEARCH = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", "utf-8")

    feed = []
    for e in entries[:FEED_SIZE]:
        url = (f"{SITE_URL}/post/{e['id']}/" if DISQUS_SHORTNAME
               else f"{SITE_URL}/{month_href(e['t'][:7]).removesuffix('index.html')}#{e['id']}")
        when = email.utils.format_datetime(datetime.fromisoformat(e["t"]).replace(tzinfo=TZ))
        feed.append(f"<item>\n<title>{html.escape(e['short'])}</title>\n<link>{url}</link>\n"
                    f'<guid isPermaLink="true">{url}</guid>\n<pubDate>{when}</pubDate>\n'
                    f"<description>{html.escape(img_urls(e['html'], SITE_URL + '/'))}</description>\n</item>")
    (OUT / "feed.xml").write_text(
        FEED_TEMPLATE.replace("__HEADING__", html.escape(TITLE)).replace("__SITE__", SITE_URL)
        .replace("__ITEMS__", "\n".join(feed)), "utf-8")

    # 圖片整個重新複製（_src/img/ 刪掉的圖，網站上也跟著消失）
    shutil.rmtree(OUT / "img", ignore_errors=True)
    if IMG.exists():
        shutil.copytree(IMG, OUT / "img")

    def disqus(url, ident, title):
        js = lambda v: json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
        return (DISQUS_EMBED.replace("__URL__", js(url)).replace("__IDENT__", js(ident))
                .replace("__PTITLE__", js(title)).replace("__SHORTNAME__", DISQUS_SHORTNAME))

    # 單則頁面每次整個重建（刪掉的動態，頁面也會一起消失；留言還留在 Disqus）
    shutil.rmtree(OUT / "post", ignore_errors=True)
    guestbook = OUT / "guestbook" / "index.html"
    if DISQUS_SHORTNAME:
        for e in entries:
            text, short = e["text"], e["short"]
            post = OUT / "post" / e["id"] / "index.html"
            post.parent.mkdir(parents=True, exist_ok=True)
            post.write_text(
                POST_TEMPLATE.replace("__STYLE__", STYLE)
                .replace("__META__", head_meta("../../"))
                .replace("__HEADING__", html.escape(TITLE))
                .replace("__TABS__", tabs("動態", "../../", len(entries)))
                .replace("__PTITLE__", html.escape(short))
                .replace("__DESC__", html.escape(text[:120]))
                .replace("__BACK__", month_href(e["t"][:7]))
                .replace("__BACKNAME__", f" {month_name(e['t'][:7])}")
                .replace("__ID__", e["id"])
                .replace("__ENTRY__", article(e, e["label"], prefix="../../"))
                .replace("__DISQUS__", disqus(f"{SITE_URL}/post/{e['id']}/", f"entry-{e['id']}", f"{short} · {TITLE}"))
                .replace("__AGO_JS__", AGO_JS), "utf-8")
        guestbook.parent.mkdir(exist_ok=True)
        guestbook.write_text(
            GUESTBOOK_TEMPLATE.replace("__STYLE__", STYLE)
            .replace("__META__", head_meta("../"))
            .replace("__HEADING__", html.escape(TITLE))
            .replace("__TABS__", tabs("留言板", "../", len(entries)))
            .replace("__DISQUS__", disqus(f"{SITE_URL}/guestbook/", "guestbook", f"留言板 · {TITLE}")), "utf-8")
    else:
        guestbook.unlink(missing_ok=True)
    (OUT / "admin").mkdir(exist_ok=True)
    (OUT / "admin" / "index.html").write_text(ADMIN_TEMPLATE.replace("__HEADING__", html.escape(TITLE)), "utf-8")
    shutil.copyfile(ROOT / "vendor" / "marked.umd.js", OUT / "admin" / "marked.js")  # 編輯頁預覽用的 Markdown 排版（MIT 授權）
    print(f"已產生: {OUT / 'index.html'}（{len(entries)} 則動態）")


def find_entry(eid, ver):
    e = next((e for e in load_entries() if e["id"] == eid), None)
    if not e or (ver and e["ver"] != ver):
        raise ValueError("這則動態已經被改過，請重新整理頁面")
    return e


def save_entry(eid, ver, day, hm, body):
    """新增（eid 為空）或修改一則動態，直接改寫原稿。新的一則放在 <年>.md 原本第一則之前。"""
    body = (body or "").replace("\r\n", "\n").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or "", re.A) or (hm and not re.fullmatch(r"\d{1,2}:\d{2}", hm, re.A)):
        raise ValueError("日期或時間的格式不對")
    if not body:
        raise ValueError("內容是空的")
    hm = norm_time(hm)
    if eid:
        e = find_entry(eid, ver)
        path, (start, end) = e["file"], e["span"]
        text = read_src(path)
    else:
        path = SRC / f"{day[:4]}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = read_src(path)
        m = ENTRY_RE.search(text)
        start = end = m.start() if m else len(text)  # 檔案開頭如果有說明文字，留在原處
    head, tail = text[:start], text[end:]
    if head and not head.endswith("\n\n"):
        head = head.rstrip("\n") + "\n\n"
    block = f"## {day}{' ' + hm if hm else ''}\n{body}\n"
    path.write_text(head + block + ("\n" if tail else "") + tail, "utf-8")
    return path, block


def delete_entry(eid, ver):
    e = find_entry(eid, ver)
    start, end = e["span"]
    text = read_src(e["file"])
    e["file"].write_text(text[:start] + text[end:], "utf-8")


def image_ext(data):
    """看檔案開頭判斷圖片格式（不相信瀏覽器說的類型）；不是 JPG、PNG、GIF、WebP 就回傳空字串。"""
    return ("jpg" if data[:3] == b"\xff\xd8\xff" else "png" if data[:4] == b"\x89PNG" else "gif" if data[:4] == b"GIF8"
            else "webp" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else "")


def save_image(data):
    """存一張編輯頁上傳的圖片（base64），回傳內文用的網址 /img/<年>/<檔名>。
    檔名取自圖片內容的 SHA-1，同一張圖傳兩次只會存一份。"""
    try:
        raw = base64.b64decode(str(data or ""), validate=True)
    except binascii.Error:
        raise ValueError("圖片資料不對") from None
    if len(raw) > MAX_IMAGE:
        raise ValueError(f"圖片太大，上限 {MAX_IMAGE // 1024 // 1024} MB")
    ext = image_ext(raw)
    if not ext:
        raise ValueError("只能上傳 JPG、PNG、GIF、WebP 圖片")
    name = f"{datetime.now(TZ).year}/{hashlib.sha1(raw).hexdigest()[:16]}.{ext}"
    path = IMG / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return f"/img/{name}"


class Editor(SimpleHTTPRequestHandler):
    """--edit 的本機伺服器：提供 public/ 的檔案，/admin/api/ 處理讀取、新增、修改、刪除、上傳圖片（和線上版的 API 相同）。"""
    lock = threading.Lock()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUT), **kwargs)

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if not self.path.startswith("/admin/api/"):
            self.send_header("Cache-Control", "no-store")  # 存檔後重新整理就看得到新的網頁
        super().end_headers()

    def do_GET(self):
        if urllib.parse.urlsplit(self.path).path == "/admin/api/entries":
            entries = [{k: e[k] for k in ("id", "date", "time", "body", "ver")} for e in load_entries()]
            return self.send_json({"mode": "local", "entries": entries})
        super().do_GET()

    def do_POST(self):
        # 只接受這個頁面自己送來的 JSON（其他網站沒辦法跨站送這種請求）
        origin = self.headers.get("Origin")
        if self.headers.get_content_type() != "application/json" or (
                origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host")):
            return self.send_json({"error": "拒絕"}, 403)
        action = urllib.parse.urlsplit(self.path).path.removeprefix("/admin/api/")
        if action not in ("save", "delete", "upload"):
            return self.send_json({"error": "找不到"}, 404)
        result = {"ok": True}
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with self.lock:
                if action == "save":
                    save_entry(data.get("id"), data.get("ver"), data.get("date"), data.get("time"), data.get("body"))
                elif action == "delete":
                    delete_entry(data.get("id"), data.get("ver"))
                else:
                    result["url"] = save_image(data.get("data"))
                build()
        except ValueError as err:  # 也包含 JSON 格式錯誤
            return self.send_json({"error": str(err)}, 400)
        self.send_json(result)


def edit(port: int = 8000):
    build()
    server = ThreadingHTTPServer(("127.0.0.1", port), Editor)
    url = f"http://localhost:{port}/admin/"
    print(f"編輯模式: {url}（Ctrl+C 結束）")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def main():
    if "--edit" in sys.argv:
        edit()
    elif "--say" in sys.argv:
        text = " ".join(a for a in sys.argv[sys.argv.index("--say") + 1:] if not a.startswith("--"))
        if not text.strip():
            sys.exit("用法: python build.py --say 要發的內容")
        now = datetime.now()
        path, block = save_entry(None, None, f"{now:%Y-%m-%d}", f"{now:%H:%M}", text)
        print(f"已新增到 {path}:\n{block}")
        build()
    else:
        build()


if __name__ == "__main__":
    main()
