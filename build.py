"""AI操作員（動態牆）：把原稿 _src/動態/*.md 產生成靜態網站 public/，也能在本機新增、修改、刪除動態。

用法（在哪個資料夾執行都可以）:
    python build.py                 產生 public/（Cloudflare 每次部署也是先執行這行）
    python build.py --edit          在本機用瀏覽器編輯動態（http://localhost:8000/admin/），改完要 commit、push 才會上線
    python build.py --say 內容      用現在的時間發一則動態，並重新產生 public/

原稿 _src/動態/<年>.md：每則以「## 2026-10-11」或「## 2026-10-11 21:30」開頭，到下一個同樣格式的標題為止，
內文是一般 Markdown；檔案裡的先後順序不重要，網頁上新的排前面。

線上編輯頁 /admin/ 呼叫 worker.js（Cloudflare Worker），透過 GitHub API 改原稿（設定方式見 README.md）。
解析與改寫原稿的規則在那支 JS 裡也有一份，改這裡的 ENTRY_RE / load_entries / save_entry / delete_entry 時要一起改。
"""
import hashlib
import html
import json
import re
import shutil
import sys
import threading
import urllib.parse
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from itertools import groupby
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "_src" / "動態"  # 動態原稿
OUT = ROOT / "public"  # 產生的網站（不進 git；wrangler.jsonc 的 assets.directory）
TITLE = "AI操作員"  # 網站名稱
SITE_URL = "https://notes.abee-lin-tw.workers.dev"  # 網站的正式網址（留言板用來對應留言串）
DISQUS_SHORTNAME = "ai-operator"  # Disqus 網站代號（disqus.com 註冊時取的）；空的就不顯示留言板和每則的留言

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
  main { max-width:760px; margin:0 auto; padding:0 24px 30vh; }
  .month h2 { font-size:.85rem; font-weight:600; color:var(--muted); margin:28px 0 0; }
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
  .back { margin:20px 0 4px; font-size:.9rem; }
  .back a { color:var(--accent); text-decoration:none; }
  .cmt-h { font-size:1rem; margin:36px 0 8px; }
  @media (max-width: 600px) { .bar { padding:10px 16px 0; } main { padding:0 16px 30vh; } }
</style>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__HEADING__</title>
__STYLE__
</head>
<body>
<header><div class="bar">
  <h1>__HEADING__</h1>
  <input type="search" id="q" placeholder="搜尋動態…">
  <nav class="tabs">__TABS__</nav>
</div></header>
<main>
__ENTRIES__
<p class="empty" id="none" hidden></p>
</main>
<script>
__AGO_JS__

const q = document.getElementById("q"), none = document.getElementById("none");
const entries = [...document.querySelectorAll(".entry")], months = [...document.querySelectorAll(".month")];
function filter() {
  const t = q.value.trim().toLowerCase();
  entries.forEach(e => e.hidden = !!t && !(e.querySelector(".when").firstChild.textContent + " "
                                           + e.querySelector(".body").textContent).toLowerCase().includes(t));
  months.forEach(m => m.hidden = !m.querySelector(".entry:not([hidden])"));
  none.textContent = entries.length ? "沒有符合的動態。" : "還沒有動態。";
  none.hidden = months.some(m => !m.hidden);
}
q.addEventListener("input", filter);
filter();
</script>
__COUNT_JS__
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

# 單則動態 public/post/<id>/index.html：全文 + 這則的留言（設定 DISQUS_SHORTNAME 才會產生）
POST_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PTITLE__ · __HEADING__</title>
<meta name="description" content="__DESC__">
__STYLE__
</head>
<body>
<header><div class="bar">
  <h1>__HEADING__</h1>
  <nav class="tabs">__TABS__</nav>
</div></header>
<main>
<p class="back"><a href="../../index.html#__ID__">← 回到動態</a></p>
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
  :root { color-scheme:light dark; --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --line:#e7e5e4; --accent:#b91c1c; --on-accent:#fff; --hl:#fef3c7; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#1c1917; --fg:#e7e5e4; --muted:#a8a29e; --line:#44403c; --accent:#f87171; --on-accent:#1c1917; --hl:#3f2a12; }
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
  .raw { white-space:pre-wrap; overflow-wrap:anywhere; margin-top:2px; }
  .empty { color:var(--muted); padding:24px 0; }
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
</main>
<script>
const $ = id => document.getElementById(id);
const pad = n => String(n).padStart(2, "0");
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let mode = "", msgTimer;

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

// 新增和修改共用的表單；e 是要修改的那則（新增時是 null）
function editor(e, onCancel) {
  const f = document.createElement("form");
  f.className = "edit-form";
  f.innerHTML = `<textarea placeholder="想說什麼？可以用 Markdown：**粗體**、[連結](網址)、> 引用"></textarea>
    <div class="row"><input type="date" required><input type="time" title="不填就只顯示日期"><span class="sp"></span>
    ${onCancel ? '<button type="button" class="cancel">取消</button>' : ""}<button>${e ? "儲存" : "發佈"}</button></div>`;
  const [ta, day, time] = f.querySelectorAll("textarea, input");
  const setNow = () => {
    const d = new Date();
    day.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    time.value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  let touched = !!e;  // 新增時沒動過日期時間，就用按下發佈當下的時間
  if (e) { ta.value = e.body; day.value = e.date; time.value = e.time; } else setNow();
  ta.rows = Math.max(3, ta.value.split("\n").length + 1);
  day.oninput = time.oninput = () => touched = true;
  ta.onkeydown = ev => { if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) f.requestSubmit(); };
  f.onsubmit = async ev => {
    ev.preventDefault();
    if (!ta.value.trim()) return ta.focus();
    if (!touched) setNow();
    const buttons = f.querySelectorAll("button");
    buttons.forEach(b => b.disabled = true);
    try {
      await api("save", {id: e ? e.id : "", ver: e ? e.ver : "", date: day.value, time: time.value, body: ta.value});
      if (!e) { ta.value = ""; touched = false; }
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

function render(entries) {
  const list = $("list");
  if (!entries.length) { list.innerHTML = '<p class="empty">還沒有動態。</p>'; return; }
  const sorted = entries.map(e => ({...e, t: e.date + (e.time ? "T" + e.time : "")}))
                        .sort((a, b) => a.t < b.t ? 1 : a.t > b.t ? -1 : 0);
  list.innerHTML = "";
  let section, ym;
  for (const e of sorted) {
    if (e.t.slice(0, 7) !== ym) {
      ym = e.t.slice(0, 7);
      section = document.createElement("section");
      section.className = "month";
      section.innerHTML = `<h2>${ym.slice(0, 4)} 年 ${+ym.slice(5, 7)} 月</h2>`;
      list.append(section);
    }
    const el = document.createElement("article");
    el.className = "entry";
    const label = `${+e.date.slice(5, 7)}/${+e.date.slice(8, 10)}${e.time ? " " + e.time : ""}`;
    el.innerHTML = `<div class="when"><a href="/#${esc(e.id)}" target="_blank" title="在網站上看這則">${label}</a>
      <span class="tools"><button type="button">編輯</button><button type="button">刪除</button></span></div>
      <div class="raw">${esc(e.body)}</div>`;
    const raw = el.querySelector(".raw"), tools = el.querySelector(".tools");
    const [edit, del] = tools.querySelectorAll("button");
    edit.onclick = () => {
      const f = editor(e, () => { f.remove(); raw.hidden = tools.hidden = false; });
      raw.hidden = tools.hidden = true;
      raw.after(f);
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
}

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
    items = [("動態", "index.html", f'<span class="n">{count}</span>')]
    if DISQUS_SHORTNAME:
        items.append(("留言板", "guestbook/index.html", ""))
    return "".join(
        f'<a class="tab" href="{prefix}{href}"' + (' aria-current="page"' if name == current else "") + f'>{name}{extra}</a>'
        for name, href, extra in items
    )


def build():
    """產生 public/index.html（動態牆，新的排前面）、public/admin/index.html（編輯頁），
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
            "html": markdown.markdown(e["body"], extensions=["extra", "sane_lists", "nl2br"]),
        })
    entries.sort(key=lambda e: e["t"], reverse=True)

    def article(e, when, foot=""):
        return (f'<article class="entry" id="{e["id"]}"><div class="when">{when}'
                f'<span class="ago" data-t="{e["t"]}"></span></div>\n<div class="body">{e["html"]}</div>{foot}</article>\n')

    def comments_link(e):
        # Disqus 的 count.js 會把連結文字換成留言數（文字在 Disqus 後台 Community → Comment Count Link 設定）
        if not DISQUS_SHORTNAME:
            return ""
        return (f'<div class="foot"><a href="post/{e["id"]}/index.html#disqus_thread" '
                f'data-disqus-identifier="entry-{e["id"]}">留言</a></div>')

    months = []
    for ym, group in groupby(entries, key=lambda e: e["t"][:7]):
        y, m = ym.split("-")
        items = "".join(article(e, f'<a href="#{e["id"]}">{e["label"]}</a>', comments_link(e)) for e in group)
        months.append(f'<section class="month"><h2>{y} 年 {int(m)} 月</h2>\n{items}</section>')
    count_js = (f'<script id="dsq-count-scr" src="https://{DISQUS_SHORTNAME}.disqus.com/count.js" async></script>'
                if DISQUS_SHORTNAME else "")
    page = (
        PAGE_TEMPLATE.replace("__STYLE__", STYLE)
        .replace("__HEADING__", html.escape(TITLE))
        .replace("__TABS__", tabs("動態", "", len(entries)))
        .replace("__ENTRIES__", "\n".join(months))
        .replace("__AGO_JS__", AGO_JS)
        .replace("__COUNT_JS__", count_js)
    )
    (OUT / "admin").mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(page, "utf-8")

    def disqus(url, ident, title):
        js = lambda v: json.dumps(v, ensure_ascii=False).replace("</", "<\\/")
        return (DISQUS_EMBED.replace("__URL__", js(url)).replace("__IDENT__", js(ident))
                .replace("__PTITLE__", js(title)).replace("__SHORTNAME__", DISQUS_SHORTNAME))

    # 單則頁面每次整個重建（刪掉的動態，頁面也會一起消失；留言還留在 Disqus）
    shutil.rmtree(OUT / "post", ignore_errors=True)
    guestbook = OUT / "guestbook" / "index.html"
    if DISQUS_SHORTNAME:
        for e in entries:
            text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", e["html"])).split())
            short = text[:30] + ("…" if len(text) > 30 else "")
            post = OUT / "post" / e["id"] / "index.html"
            post.parent.mkdir(parents=True, exist_ok=True)
            post.write_text(
                POST_TEMPLATE.replace("__STYLE__", STYLE)
                .replace("__HEADING__", html.escape(TITLE))
                .replace("__TABS__", tabs("動態", "../../", len(entries)))
                .replace("__PTITLE__", html.escape(short))
                .replace("__DESC__", html.escape(text[:120]))
                .replace("__ID__", e["id"])
                .replace("__ENTRY__", article(e, e["label"]))
                .replace("__DISQUS__", disqus(f"{SITE_URL}/post/{e['id']}/", f"entry-{e['id']}", f"{short} · {TITLE}"))
                .replace("__AGO_JS__", AGO_JS), "utf-8")
        guestbook.parent.mkdir(exist_ok=True)
        guestbook.write_text(
            GUESTBOOK_TEMPLATE.replace("__STYLE__", STYLE)
            .replace("__HEADING__", html.escape(TITLE))
            .replace("__TABS__", tabs("留言板", "../", len(entries)))
            .replace("__DISQUS__", disqus(f"{SITE_URL}/guestbook/", "guestbook", f"留言板 · {TITLE}")), "utf-8")
    else:
        guestbook.unlink(missing_ok=True)
    (OUT / "admin" / "index.html").write_text(ADMIN_TEMPLATE.replace("__HEADING__", html.escape(TITLE)), "utf-8")
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


class Editor(SimpleHTTPRequestHandler):
    """--edit 的本機伺服器：提供 public/ 的檔案，/admin/api/ 處理讀取、新增、修改、刪除（和線上版的 API 相同）。"""
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
        if action not in ("save", "delete"):
            return self.send_json({"error": "找不到"}, 404)
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            with self.lock:
                if action == "save":
                    save_entry(data.get("id"), data.get("ver"), data.get("date"), data.get("time"), data.get("body"))
                else:
                    delete_entry(data.get("id"), data.get("ver"))
                build()
        except ValueError as err:  # 也包含 JSON 格式錯誤
            return self.send_json({"error": str(err)}, 400)
        self.send_json({"ok": True})


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
