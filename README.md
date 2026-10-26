# AI操作員（動態牆）

網址：https://notes.abee-lin-tw.workers.dev/

原稿放在 `_src/動態/<年>.md`。`build.py` 會把原稿產生成網站，放在 `public/`。`public/` 不進 git。每次 push，Cloudflare 都會先執行 `build.py`，再部署成 Worker。`worker.js` 只負責線上編輯頁的存檔 API，其他網址都直接回傳 `public/` 裡的網頁。

## 平常怎麼發文

- **手機或任何電腦：** 打開 https://notes.abee-lin-tw.workers.dev/admin/ ，用 email 驗證碼登入，就能新增、修改、刪除動態。每次存檔都會直接 commit 到 GitHub，網站約 1 分鐘後更新。
  - 輸入框上方有工具列：粗體、斜體、刪除線、小標題、引用、清單、連結、程式碼、分隔線，以及預覽開關。快捷鍵是 Ctrl+B、Ctrl+I、Ctrl+K，Ctrl+Z 可以復原。
  - 打字時，輸入框下面會即時預覽 Markdown 排版後的樣子。預覽用的是 `vendor/marked.umd.js`（開源的 marked，MIT 授權），網站本身則由 Python-Markdown 產生，少數寫法可能略有不同。
  - **放圖片：** 按工具列的圖片按鈕選檔，或直接把截圖貼上（Ctrl+V）、把圖片拖進輸入框。詳見下面的「圖片」。
- **這台電腦：** 先 `git pull`，把線上的修改拉下來，再執行下列其中一個指令：
  - `python build.py --edit`：用瀏覽器編輯，網址是 http://localhost:8000/admin/
  - `python build.py --say 內容`：用現在的時間快速發一則

  改完要 commit、push 才會上線。
- **直接改原稿：** 每則以 `## 2026-10-17` 或 `## 2026-10-17 21:30` 開頭，內文用一般的 Markdown 寫。改完執行 `python build.py`，就能在 `public/index.html` 預覽。

## 網站的頁面

- **首頁 `/`：** 最新 50 則（`build.py` 的 `HOME_SIZE`），依月份分段。最下面有「更早的動態」，連到下一則所在的月份頁。
- **月份頁 `/2026/10/`：** 那個月的全部動態，依週分段（週一到週日，只算同一個月的日子，例如「10/1 – 10/4」）。上下有前後月份的連結。
- **彙整 `/archive/`：** 每年每個月各有幾則，點了到月份頁。
- **單則頁 `/post/<日期時間>/`：** 一則動態和它的留言，上面有「回到 2026 年 10 月的動態」。
- **搜尋：** 每頁上方的搜尋框搜的是全部動態，不只這一頁。第一次打字時才載入搜尋資料 `search.js`；空白隔開多個字，要全部符合。結果最多列 100 則，點了到月份頁的那一則。
- **舊連結：** 以前分享的 `/#2026-10-11` 這種連結，那則不在首頁的話，會自動跳到它的月份頁。
- **編輯頁：** 先列最新 50 則，下面有「顯示更早的動態」。

## 圖片

- **存在哪裡：** 圖片 commit 進這個 repo 的 `_src/img/<年>/`，產生網站時複製到 `public/img/`。不用 R2 之類的另外的服務，也不用綁信用卡。
- **上傳前會先處理：** 在瀏覽器裡縮小到寬 1600、高 3200 以內，轉成 WebP（Safari 轉成 JPG）。
  - 手機照片通常只剩幾百 KB。重新轉檔也會去掉相片裡的拍攝地點、相機型號等資料。
  - GIF 可能是動圖，照原檔上傳。
  - 上限 10 MB。
- **檔名：** 由圖片內容算出來，例如 `img/2026/7a92f1db2ffa8db3.webp`。同一張圖傳兩次只會存一份。
- **內文的寫法：** `![](/img/2026/檔名.webp)`。編輯頁會自動插入這行，也可以在 `![]` 的中括號裡寫一句說明。
- **線上編輯時：** 每傳一張圖就是一個 commit，例如「上傳圖片 2026/xxx.webp」。網站約 1 分鐘後才看得到那張圖；在那之前，編輯頁的預覽會先顯示你電腦或手機上的圖。
- **刪除：** 刪掉動態不會刪掉圖片。真的要刪，到 GitHub 刪 `_src/img/` 裡的檔案。

## RSS

- 網址是 `/feed.xml`，網站上方分頁列的右邊有「RSS」連結。放最新的 30 則全文（`build.py` 的 `FEED_SIZE`）。
- 每則的連結是單則頁面 `/post/<日期時間>/`。修改動態的日期或時間，RSS 閱讀器會把它當成新的一則。

## 觀看統計（Cloudflare Web Analytics）

- **開關：** `build.py` 的 `CF_ANALYTICS_TOKEN` 填了 token，首頁、單則頁、留言板就會載入 Cloudflare 的統計碼。留空就不載入。編輯頁 `/admin/` 一律不載入。
- **看數字：** Cloudflare 後台 → Web Analytics → 點網站名稱。
- **注意：** 不記錄個人資料、不用 cookie，但 Brave 之類會擋廣告追蹤的瀏覽器會擋掉統計碼，所以實際人數會比顯示的多一些。
- **重新設定的步驟：**
  1. Cloudflare 後台左邊選單找 Web Analytics，按 Add a site。
  2. Hostname 填 `notes.abee-lin-tw.workers.dev`，按 Done。
  3. 頁面上會出現一段 JS Snippet，裡面 `"token": "…"` 引號裡的那串就是 token，填進 `CF_ANALYTICS_TOKEN`，commit、push。

## 留言板（Disqus）

- **開關：** `build.py` 裡的 `DISQUS_SHORTNAME` 填了 Disqus 的網站代號，才會出現下面兩種留言。留空就都不顯示。
  - **留言板：** 網站上方的「留言板」分頁，網址是 `/guestbook/`。
  - **每則動態的留言：** 每則下面有「留言」連結，點了就在那一則下面展開它的留言。Disqus 一頁只能放一個留言框，所以同時只會展開一則。
    - 每則也有專屬頁面 `/post/<日期時間>/`，用新分頁打開連結、或分享單則時會用到。兩邊是同一串留言。
- **每則的留言是用日期時間對應的：** 修改動態的**日期或時間**後，它的網址會變，舊留言還留在 Disqus，但不會再顯示在這則下面。只改內文沒關係。
- **留言數的文字：** 在 Disqus 後台 Settings → Community → Comment Count Link 設定，例如「留言」「1 則留言」「{num} 則留言」。
- **管理留言：** 登入 disqus.com，進入網站的管理後台（Moderation），可以刪除留言或封鎖留言者。
- **建議的 Disqus 設定：** 在 Settings → Advanced → Trusted Domains 填 `notes.abee-lin-tw.workers.dev`，避免別人把你的留言板嵌到他們的網站。

## 搜尋引擎

- **首頁、月份頁、彙整、單則頁和留言板：** 允許搜尋引擎收錄。
- **編輯頁 `/admin/`：** 有 `noindex`，不會被收錄。

## 設定在哪裡

| 設定 | 位置 |
|---|---|
| Worker 名稱、網站檔案的資料夾、`GITHUB_REPO`、`ACCESS_TEAM`、`ACCESS_AUD` | 這個 repo 的 `wrangler.jsonc` |
| 網站名稱、標語、網址、Disqus、觀看統計的 token、首頁和 RSS 的則數 | 這個 repo 的 `build.py` 開頭（`TITLE`、`TAGLINE`…） |
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
  | 圖片上傳失敗：看不懂這個圖片格式 | 瀏覽器打不開這種圖（例如電腦上的 HEIC），先轉成 JPG 或 PNG |

- **改了 `build.py` 裡解析原稿的規則：** `worker.js` 也有同樣一份規則，兩邊要一起改。
