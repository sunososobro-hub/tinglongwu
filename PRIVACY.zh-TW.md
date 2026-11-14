# 隱私聲明

[English](PRIVACY.md)

tinglongWu 在你自己的電腦上翻譯語音。本頁說明哪些資料只留在本機，以及每一處會用到網路的地方。內容對應本儲存庫的程式碼，不是法律保證（見「限制」）。

## 只留在本機的資料
- **音訊與文字都在本機處理，不會上傳。** 音訊預設只存在記憶體中；只有你選了 `--record` 才會存在本機（`transcripts/rec-*/`）。
- **預設不儲存逐字稿。** 只有你主動開啟的 `--save`（GUI：儲存勾選框）與 `--save-result PATH`（說話模式）會把文字寫成本機檔案（`transcripts/`，JSONL）。
- **tlwu 寫到本機的檔案：** `gui.json`（你的設定）、設定資料夾裡的使用者設定檔 `config`（`auto_update`、`update_last_check`、`ui_lang`）、日誌資料夾裡的執行日誌與 `install.log`（安裝自我測試）、`speedprobe.json`（速度測試結果）、`netguard.jsonl`（見下）、只含語言代碼的暫存檔，以及上述自選的逐字稿。
- **模型**存放在安裝目錄的 `models/` 資料夾（或 `TLWU_MODELS`／`--models-dir`）；只會下載，不會上傳。
- **沒有遙測。** 2026-11 稽核未發現。載入時 tlwu 也會設定 `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`HF_HUB_DISABLE_TELEMETRY`、`DO_NOT_TRACK` 等環境變數。

## 所有網路存取
執行期套件 `tinglongwu/` 沒有網路程式碼；tlwu 執行時 Python 層級的網路防護也會擋下非本機連線。可連網的程式碼只在 `tinglongwu_setup/` 與安裝／更新腳本：

| # | 項目 | 何時 | 送出什麼 | 如何關閉 |
|---|------|------|----------|----------|
| 1 | `tlwu-setup` 下載模型：Hugging Face（Whisper、Hy-MT2 GGUF；固定 commit + SHA256）、`raw.githubusercontent.com` 上的 Argos OPUS 索引與 argos-net.com 的套件（只有 `ru-en`、`en-zh` 有固定，其他語言對沒有） | 只在你執行 setup 並回答 yes 時 | 一般 HTTPS 請求（你的 IP、user agent、要的檔案） | 不執行 setup／回答 N |
| 2 | `scripts/mac_install.sh` 下載 Kokoro TTS 模型（第三方 GitHub release，只檢查大小、無雜湊）；Vosk 俄語 TTS 模型：自行從 alphacephei.com 下載，無雜湊 | macOS 安裝／手動 | 一般 HTTPS 請求 | 略過該步驟 |
| 3 | Python 相依套件：PyPI，加上 llama-cpp wheel 的第三方索引（`requirements-llama-cuda.txt`） | `install.ps1`、`tlwu-setup --deps`、`mac_install.sh`、需求變動時的自動更新 | 一般 HTTPS 請求 | 不執行安裝程式 |
| 4 | 自選的自動更新：對 GitHub `main` 分支做 `git fetch`（上限 6 秒）；需求變動時可能執行 `pip install -e .` | 只問一次，預設關 | git 請求 | 回答否，或設 `TLWU_NO_UPDATE=1` |
| 4b | Windows 安裝精靈版的更新檢查（`tinglongwu_setup/selfupdate.py`，由 GUI 另開子程序執行）：GitHub API `releases/latest`（上限 6 秒）；只有在有新版本時，才下載該版本的 `SHA256SUMS.txt` 與 `tinglongwu-setup.exe`（執行前驗證 SHA256） | 安裝時有勾「自動更新」（存成 `auto_update=1`）則每天開 GUI 時檢查一次；否則只在你按「檢查更新」時 | 一般 HTTPS 請求（你的 IP、user agent）；不含你本人或使用內容的任何資訊 | 取消勾選「自動更新」（安裝精靈或 ⚙ 調整），或設 `TLWU_OFFLINE=1`（完全不連線） |
| 5 | 安裝程式下載原始碼：Windows 安裝精靈 `tinglongwu-setup.exe`（內含原始碼；之後 `install.ps1` 在缺 Python 3.12 時從 python.org 下載並驗證 SHA256，再下載 Python 套件）；Windows `scripts/bootstrap_windows.ps1`（從 GitHub git clone，沒有 git 時下載該版本的 GitHub zip）；Linux `scripts/install.sh`（從 GitHub release `curl \| bash`，再 git clone）；macOS `mac_install.sh`（從 GitHub release `curl \| bash`、git clone、Homebrew） | 你執行時 | 一般 HTTPS 請求 | 不執行 |
| 6 | 網路防護回報（`tinglongwu/netreport.py`）：用瀏覽器開啟預填好的 GitHub issue | 只有在連線被擋、且你回答 y 時；會先顯示完整文字，由你自己送出 | 版本、commit、OS 字串、host/port/套件/模式/次數、最多 12 筆 `module:function:line`。不含逐字稿、使用者名稱、家目錄路徑、主機名稱 | 回答 N |
| 6b | 回報問題（GUI 按鈕／`tlwu --report`，`tinglongwu/report.py`）：用瀏覽器開啟預填好的 GitHub issue | 只有你按下時；你在 GitHub 上檢視文字後自己送出 | App 版本、OS、Python、CPU 型號／AVX、核心數、RAM、GPU／VRAM、NVIDIA 驅動、套件版本、目前設定（GUI）或語言對／引擎（CLI）、最近一次安裝自我測試區塊、最多最後 200 行執行日誌（依網址長度裁切）；家目錄路徑與使用者名稱會遮蔽 | 不要按 |
| 7 | 手機版：release manifest 沒有 INTERNET 權限（debug 有） | - | - | - |
| 8 | 僅開發用（不隨附）：`scripts/bench/*`、`scripts/make_lock.py` | - | - | - |

`TLWU_ALLOW_NET=1` 會關閉網路防護，非必要請勿設定。

`netguard.jsonl`（在你的使用者日誌目錄）記錄被擋的連線：時間、host、port、套件、精簡堆疊。不含個資，也不會自動送出。

## Android 手機版（tinglongWu mobile / Google Play 上的 TingLong）
- **麥克風聲音只在手機上處理。**語音辨識與翻譯都在裝置上執行，聲音和文字不上傳，只留在記憶體，不錄音。
- **沒有網路權限。**正式版 app 不申請 Android 的 INTERNET 權限，無法經網路送出任何資料。
- **模型：**用手機瀏覽器下載模型包再用系統檔案選擇器匯入；或 Google Play 版由 Play 商店以 Play Asset Delivery 下載。兩種方式每個檔案都會核對大小與 SHA-256，存在 app 自己的資料夾。
- **手機上會存的：**模型檔與 app 資料夾內的本機執行紀錄。沒有帳號、沒有分析、沒有廣告、沒有廣告 ID。
- **不向開發者或第三方收集或分享任何資料。**安裝、更新與模型包下載由 Google Play 依 Google 的隱私權政策處理。
- **全部刪除：**解除安裝 app 即可（Android 會一併刪除資料夾與模型）。

## 付款
付費版：結帳資料（電子郵件、授權碼、卡片資料）完全由金流服務商處理，tlwu 不儲存付款資料。

## 限制
- 網路防護只擋 Python 層級的 socket。原生函式庫（ctranslate2、onnxruntime、llama.cpp）與其他子行程不在範圍內；獨立驗證請用作業系統防火牆測試（見 README「隱私模型」）。
- 部分下載尚未固定雜湊（第 1、2 列）。
- 作業系統可能寫出含記憶體內容的當機傾印或分頁檔，tlwu 無法控制。

## 聯絡
隱私問題請到 https://github.com/sunososobro-hub/tinglongwu/issues 開 issue，敏感事項請依 [SECURITY.md](SECURITY.md) 私下回報。
