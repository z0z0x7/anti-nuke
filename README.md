# Discord 防炸群機器人

Python（discord.py）寫的防炸群（Anti-nuke）機器人，部署在 Railway。

## 功能

偵測到有人短時間內做出危險操作時，自動處置並通知伺服器擁有者。

| 偵測項目 | 預設門檻 |
|---|---|
| 🗑️ 刪除頻道（看審核日誌） | 60 秒內刪 3 個 |
| ➕ 建立頻道（看審核日誌） | 60 秒內建 5 個 |
| 💬 大量傳訊息 | 10 秒內 10 則 |
| 📢 跨頻道洗版 | 10 秒內在 4 個不同頻道發言 |

**觸發後的處置：**

- **一般成員**：先記下他的身分組再全部拿掉，加上「🔒 隔離中」身分組（只看得到 `#🔒隔離區`），然後禁言 60 分鐘，並刪除他剛剛洗版的訊息
- **機器人**：直接踢出（機器人自帶的身分組拿不掉）
- **Webhook 洗版**：刪除那個 Webhook
- 刪頻道、建頻道觸發時，日誌會列出被刪的頻道名稱、他建了哪些頻道。他建的頻道**不會自動刪除**（怕誤判），確認是洗版再手動刪
- 每次處置都會發到日誌頻道並 @ 擁有者。日誌頻道不能用時，改成私訊擁有者
- 誤判的話，用 `/antinuke restore` 一鍵還原身分組並解除禁言

伺服器擁有者、白名單裡的人和機器人本身不會被處理。

## 指令

| 指令 | 說明 | 誰能用 |
|---|---|---|
| `/antinuke setup log_channel:#頻道` | 開啟防炸群，建立隔離身分組和隔離區 | 擁有者 |
| `/antinuke status` | 查看目前設定、白名單、隔離中的人 | 管理員 |
| `/antinuke enable` | 開啟或關閉防炸群 | 擁有者 |
| `/antinuke limit rule count seconds` | 調整觸發門檻 | 擁有者 |
| `/antinuke timeout minutes` | 調整禁言時間（最多 28 天） | 擁有者 |
| `/antinuke whitelist add / remove` | 管理白名單 | 擁有者 |
| `/antinuke restore member` | 還原被隔離的人 | 擁有者、白名單 |

改設定的指令只開放給擁有者，這樣管理員帳號被盜時也沒辦法把防護關掉。

## 一、建立 Discord 機器人

1. 到 <https://discord.com/developers/applications> → **New Application**
2. 左側 **Bot** → **Reset Token** → 複製 Token（**不要給任何人看**）
   - 目前不需要開任何 Privileged Intents
3. 左側 **OAuth2 → URL Generator**：
   - Scopes 勾 `bot`、`applications.commands`
   - Bot Permissions 勾 `Administrator`
4. 打開下方產生的網址，把機器人邀進你的伺服器

> 拿 ID 的方法：Discord「使用者設定 → 進階」打開「開發者模式」，之後對伺服器、身分組、頻道按右鍵就能「複製 ID」。

## 二、上傳到 GitHub

1. 到 <https://github.com/new> 建一個 repo（建議設為 **Private**）
2. 點 **uploading an existing file**
3. 在 Finder 打開這個資料夾，按 **⌘ + Shift + .** 顯示隱藏檔案，**⌘ + A** 全選後拖進上傳頁面
   - 要拖資料夾「裡面的東西」，不要拖整個資料夾，不然會多一層
   - 建議用 Chrome、Edge 或 Firefox
4. 按 **Commit changes**

檔案位置應該長這樣：

```
main.py
config_store.py
utils.py
requirements.txt
railway.json
.gitignore
cogs/
  __init__.py
  antinuke.py
```

## 三、部署到 Railway

1. 到 <https://railway.com> → **Login with GitHub**
2. **New Project → Deploy from GitHub repo** → 選這個 repo（第一次要授權 GitHub）
3. 點服務方塊 → **Variables** → 新增：

   | 名稱 | 內容 |
   |---|---|
   | `DISCORD_TOKEN` | 機器人的 Token（必填） |
   | `GUILD_ID` | 伺服器 ID（選填。有填的話，斜線指令馬上出現；沒填最多要等 1 小時） |

4. 按上方的 **Deploy**
5. **Deployments → View Logs** 看到 `已登入：` 和 `已同步 … 個斜線指令` 就成功了

之後只要在 GitHub 上傳新檔案，Railway 就會自動重新部署。這個機器人不用開網頁，**不需要** Generate Domain。

> 💰 Railway 沒有永久免費方案。試用額度用完後要升級 Hobby 方案（大約每月 5 美元，含等值用量），實際價格以官網 Pricing 頁面為準。

## 四、在 Discord 裡設定

1. **伺服器設定 → 身分組**：把機器人的身分組**拖到最上面**。機器人動不了身分組比它高的人
2. 執行 `/antinuke setup`，選一個日誌頻道
3. 用 `/antinuke whitelist add` 把信任的管理員和其他機器人加入白名單（例如開單時建頻道、關單時刪頻道的開單機器人）。**沒加入白名單的機器人觸發時會被直接踢出**
4. 建議用一個不在白名單裡的小帳號，快速刪 3 個測試頻道，確認會被隔離，再用 `/antinuke restore` 還原

> 機器人會自動建立這三個東西，**請不要刪除**：
> - `🔒 隔離中` 身分組
> - `#🔒隔離區` 頻道
> - `#防炸群-設定` 頻道：設定存在這裡，Railway 重新部署也不會遺失

## 注意事項

- 審核日誌大約有 1～2 秒延遲，對方可能在被擋下前已經刪掉幾個頻道。被刪的頻道**不會自動復原**，請到「伺服器設定 → 審核日誌」查看刪了哪些
- Discord 不能禁言有「管理員」權限的人。機器人會先拿掉身分組再禁言，所以 @everyone 不要給管理員權限
- 如果另一個機器人也有防洗版功能（例如 `/automod`），兩邊可能同時處理同一個人，短的禁言時間可能蓋掉長的。不過隔離身分組不受影響，對方仍然看不到其他頻道
