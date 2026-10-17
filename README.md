# AI操作員（動態牆）

原稿放在 `_src/動態/<年>.md`。`build.py` 會把原稿產生成網站，放在 `public/`。`public/` 不進 git。每次 push，Cloudflare Pages 都會自動執行 `build.py` 並重新部署。

## 平常怎麼發文

- **手機或任何電腦：** 打開 `https://<專案名稱>.pages.dev/admin/`，用 email 驗證碼登入，就能新增、修改、刪除動態。每次存檔都會直接 commit 到 GitHub，網站約 1 分鐘後更新。
- **這台電腦：** 先 `git pull`，把線上的修改拉下來，再執行下列其中一個指令：
  - `python build.py --edit`：用瀏覽器編輯，網址是 http://localhost:8000/admin/
  - `python build.py --say 內容`：用現在的時間快速發一則

  改完要 commit、push 才會上線。
- **直接改原稿：** 每則以 `## 2026-10-17` 或 `## 2026-10-17 21:30` 開頭，內文用一般的 Markdown 寫。改完執行 `python build.py`，就能在 `public/index.html` 預覽。

## Cloudflare 設定（只需要做一次）

### 1. 建 Pages 專案

1. Cloudflare 後台 → Workers & Pages → 建立 → 切到 **Pages** → Connect to Git → 選這個 repo。
2. Framework preset 選 `None`。
3. Build command 填 `python -m pip install -r requirements.txt && python build.py`。
4. Build output directory 填 `public`。
5. 部署。網址就是 `https://<專案名稱>.pages.dev`。

### 2. 產生 GitHub token（讓線上編輯頁能 commit）

1. GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token。
2. Repository access 選 **Only select repositories**，只勾這個 repo。
3. Permissions → Repository permissions → **Contents** 選 **Read and write**，其他都不用開。
4. 到期日自己選，例如一年。token 只會顯示一次，先複製起來。

### 3. 用 Cloudflare Access 保護 `/admin`

1. Pages 專案 → Settings → General → Access policy → **Enable**。這會建立一個 Access 應用程式，保護所有預覽部署的網址（`*.<專案名稱>.pages.dev`）。
   - 第一次使用 Zero Trust 時，要先取一個 team name，再選 Free 方案。
2. 到 Zero Trust → Access → Applications，編輯剛建立的應用程式。
   1. 新增一個 public hostname：網域填 `<專案名稱>.pages.dev`，**Path 填 `admin`**。`/admin/` 和底下的 API 都會被保護，首頁仍然公開。
   2. Policy 設成 Allow，Include → Emails 只填你自己的 email。
   3. 登入方式用 One-time PIN（寄驗證碼到 email）。
3. 記下兩個值：
   - **Application Audience (AUD) Tag**：在這個應用程式的設定頁裡。
   - **Team name**：在 Zero Trust → Settings 裡，也就是 `<team>.cloudflareaccess.com` 的 `<team>`。

### 4. 設定環境變數

到 Pages 專案 → Settings → Variables and Secrets，在 Production 環境加入下面四個變數：

| 名稱 | 值 | 類型 |
|---|---|---|
| `GITHUB_TOKEN` | 第 2 步的 token | **Secret** |
| `GITHUB_REPO` | `divynslin/<repo 名稱>` | Text |
| `ACCESS_TEAM` | 第 3 步的 team name | Text |
| `ACCESS_AUD` | 第 3 步的 AUD Tag | Text |

如果分支不是 `main`，再加一個 `GITHUB_BRANCH`。

### 5. 重新部署，然後測試

環境變數要等下一次部署才會生效。到 Deployments，對最新那次按 **Retry deployment**。部署完成後照下面測一次：

1. 用無痕視窗打開首頁，應該不需要登入。
2. 打開 `/admin/`，應該會先跳出 Cloudflare 登入頁。
3. 登入後發一則測試動態，約 1 分鐘後首頁應該出現。
4. 刪掉這則測試動態。

### 6. 觀看統計

Pages 專案 → Metrics → Web Analytics → Enable。下次部署之後開始統計。

## 之後的維護

- **GitHub token 到期：** 照第 2 步重新產生 token，更新 `GITHUB_TOKEN`，再 Retry deployment。
- **線上編輯頁顯示「沒有登入」：** 重新整理頁面，再登入一次。
- **改了 `build.py` 裡解析原稿的規則：** `functions/admin/api/[[path]].js` 也有同樣一份規則，兩邊要一起改。
