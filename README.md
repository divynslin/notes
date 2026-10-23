# AI操作員（動態牆）

網址：https://notes.abee-lin-tw.workers.dev/

原稿放在 `_src/動態/<年>.md`。`build.py` 會把原稿產生成網站，放在 `public/`。`public/` 不進 git。每次 push，Cloudflare 都會先執行 `build.py`，再部署成 Worker。`worker.js` 只負責線上編輯頁的存檔 API，其他網址都直接回傳 `public/` 裡的網頁。

## 平常怎麼發文

- **手機或任何電腦：** 打開 https://notes.abee-lin-tw.workers.dev/admin/ ，用 email 驗證碼登入，就能新增、修改、刪除動態。每次存檔都會直接 commit 到 GitHub，網站約 1 分鐘後更新。
- **這台電腦：** 先 `git pull`，把線上的修改拉下來，再執行下列其中一個指令：
  - `python build.py --edit`：用瀏覽器編輯，網址是 http://localhost:8000/admin/
  - `python build.py --say 內容`：用現在的時間快速發一則

  改完要 commit、push 才會上線。
- **直接改原稿：** 每則以 `## 2026-10-17` 或 `## 2026-10-17 21:30` 開頭，內文用一般的 Markdown 寫。改完執行 `python build.py`，就能在 `public/index.html` 預覽。

## 留言板（Disqus）

- **開關：** `build.py` 裡的 `DISQUS_SHORTNAME` 填了 Disqus 的網站代號，才會出現下面兩種留言。留空就都不顯示。
  - **留言板：** 網站上方的「留言板」分頁，網址是 `/guestbook/`。
  - **每則動態的留言：** 每則下面有「留言」連結，點了會到這則的專屬頁面 `/post/<日期時間>/`，留言串在那一頁。
- **每則的留言是用日期時間對應的：** 修改動態的**日期或時間**後，它的網址會變，舊留言還留在 Disqus，但不會再顯示在這則下面。只改內文沒關係。
- **留言數的文字：** 在 Disqus 後台 Settings → Community → Comment Count Link 設定，例如「留言」「1 則留言」「{num} 則留言」。
- **管理留言：** 登入 disqus.com，進入網站的管理後台（Moderation），可以刪除留言或封鎖留言者。
- **建議的 Disqus 設定：** 在 Settings → Advanced → Trusted Domains 填 `notes.abee-lin-tw.workers.dev`，避免別人把你的留言板嵌到他們的網站。

## 搜尋引擎

- **首頁和留言板：** 允許搜尋引擎收錄。
- **編輯頁 `/admin/`：** 有 `noindex`，不會被收錄。

## 設定在哪裡

| 設定 | 位置 |
|---|---|
| Worker 名稱、網站檔案的資料夾、`GITHUB_REPO`、`ACCESS_TEAM`、`ACCESS_AUD` | 這個 repo 的 `wrangler.jsonc` |
| 建置指令 | Cloudflare 後台：Worker（notes）→ Settings → Build |
| `GITHUB_TOKEN` | Cloudflare 後台：Worker（notes）→ Settings → Variables and Secrets，類型選 Secret |
| 誰能進 `/admin` | Cloudflare 後台：Zero Trust → Access controls → Applications |

## Cloudflare 設定（只需要做一次）

1. **建置指令：** Worker（notes）→ Settings → Build。
   - Build command 填 `python -m pip install -r requirements.txt && python build.py`。
   - Deploy command 維持 `npx wrangler deploy`。
2. **GitHub token：** GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
   - Repository access 只選 `divynslin/notes`。
   - Permissions → **Contents** 選 **Read and write**。
   - token 只會顯示一次，先複製起來。
3. **加 Secret：** Worker（notes）→ Settings → Variables and Secrets → Add。
   - 類型選 **Secret**，名稱填 `GITHUB_TOKEN`，值貼上第 2 步的 token。
4. **Access 只鎖 `/admin`：** 保護 notes 的 Access 應用程式只能有一個網址：`notes.abee-lin-tw.workers.dev`，Path 填 `admin`。
   - 不能保護整個 `notes.abee-lin-tw.workers.dev`，否則首頁也要登入。
   - Policy 只允許你自己的 email，登入方式用 One-time PIN。
   - 這個應用程式的 AUD Tag 要填進 `wrangler.jsonc` 的 `ACCESS_AUD`。
5. **測試：** push 之後等部署完成。
   1. 用無痕視窗打開首頁，應該不需要登入。
   2. 打開 `/admin/`，應該會先跳出 Cloudflare 登入頁。
   3. 登入後發一則測試動態，約 1 分鐘後首頁應該出現。
   4. 刪掉這則測試動態。

## 之後的維護

- **GitHub token 到期：** 重新產生 token，再到 Variables and Secrets 更新 `GITHUB_TOKEN`。
- **線上編輯頁顯示錯誤：**

  | 編輯頁上的訊息 | 原因 |
  |---|---|
  | 還沒設定 ACCESS_TEAM / ACCESS_AUD | `wrangler.jsonc` 的 `ACCESS_AUD` 還是空的 |
  | 還沒設定 GITHUB_TOKEN / GITHUB_REPO | 還沒加 `GITHUB_TOKEN` Secret |
  | 讀不到 Access 公鑰 | `wrangler.jsonc` 的 `ACCESS_TEAM` 填錯 |
  | 沒有登入，或登入已過期 | 先重新整理頁面再登入一次；還是不行的話，`ACCESS_AUD` 填錯，或 Access 沒有保護 `admin` 路徑 |
  | GitHub 回應 401 | token 錯了或過期了 |
  | GitHub 回應 403 或 404 | token 沒有這個 repo 的 Contents 寫入權限 |

- **改了 `build.py` 裡解析原稿的規則：** `worker.js` 也有同樣一份規則，兩邊要一起改。
