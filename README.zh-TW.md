# tinglongWu — 台語「聽攏有」(thiann lóng ū)：「我全都聽懂了」。即時語音翻譯，100% 離線、可驗證。

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

Architecture / 架構 / 架构: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [zh-TW](docs/ARCHITECTURE.zh-TW.md) · [zh-CN](docs/ARCHITECTURE.zh-CN.md)

完全本機、離線的即時語音翻譯器：**系統音訊輸出 -> 擷取 -> 語音辨識 -> 翻譯 -> 文字**。
資料不離開你的電腦：不用雲端 API、沒有遙測，音訊與逐字稿都不會寫入磁碟。

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你過得怎麼樣？
```

## 快速安裝（Windows）

適用 Windows 10/11，有 NVIDIA 顯示卡最佳（沒有也能跑，只是較慢）。請先安裝 [Python 3.12](https://www.python.org/downloads/windows/)，安裝時勾選 **Add python.exe to PATH**。

1. 在 GitHub 按 **Code > Download ZIP**，下載後對 ZIP 按右鍵 > **全部解壓縮**。
2. 開啟資料夾，雙擊 **`install.bat`**，耐心等待（約 10–30 分鐘，最多下載約 10 GB；中斷後可重跑）。
3. 雙擊桌面新出現的捷徑 **聽攏有 tinglongWu**。

移除：刪除資料夾與桌面捷徑即可。`install.ps1 -DryRun` 只顯示會做什麼、不安裝。Qwen 引擎使用 `requirements-qwen.txt`（CUDA 版 llama-cpp-python）；若沒有合適的預編譯 wheel，安裝程式會自動改用 OPUS 引擎。

## 安裝／使用分離（請先閱讀）

同一個 repo、兩個程式，邊界明確：

| | `tlwu-setup`（套件 `tinglongwu_setup`） | `tlwu`（套件 `tlwu`） |
|---|---|---|
| 網路 | **會連網**，是唯一的連網程式碼 | **永不連網**，沒有任何下載／網路程式碼 |
| 工作 | 安裝鎖版相依、下載並以 SHA256 釘選模型、建立／離線安裝離線包 | 擷取音訊、辨識、翻譯、輸出 |
| 時機 | 有網路時執行一次 | 每次執行，可完全離線 |

`tests/test_no_network_code.py` 會靜態掃描 `tinglongwu/`，若匯入 requests、urllib.request、http.client、huggingface_hub、argostranslate、socket（防護模組 `tinglongwu/offline.py` 除外）或 `tinglongwu_setup` 即失敗。

```
tlwu-setup --deps                                  # 安裝鎖版相依
tlwu-setup --whisper small --langs ru --to zh      # 下載並釘選模型
tlwu-setup --bundle tlwu-bundle.zip                 # 打包 wheel + 模型，供隔離網路的電腦使用
tlwu-setup --from-bundle tlwu-bundle.zip            # 在離線電腦上安裝並驗證，無需網路
```
離線包與平台相關：請在與目標機相同的作業系統、CPU 架構與 Python 次版本上建立。

## 隱私模型

- 音訊只存在記憶體；逐字稿只輸出到 stdout，不寫任何紀錄。
- `import tinglongwu` 時（早於任何 HF/ctranslate2 匯入）強制設定離線環境變數：`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`HF_HUB_DISABLE_TELEMETRY`、`DO_NOT_TRACK`、`ARGOS_*`。
- 執行期**網路防護**會攔截 `socket.connect/connect_ex/getaddrinfo/gethostbyname`，任何非 loopback 目標一律丟出例外，永遠開啟。
- 模型只從本機 `models/` 載入（`--models-dir` 或環境變數 `TLWU_MODELS`）；缺模型時會報錯並指向下載腳本，不會自動下載。
- `tlwu-setup` (`tinglongwu_setup`) 是唯一會連網的程式。首次下載時「釘住」（HF commit sha／套件 URL 與每個檔案的 SHA256，存於 `models/manifest.json`），之後每次都驗證（`--verify` 可離線執行）。
- `scripts/verify_offline.py` 執行 tlwu 並以 psutil 輪詢整個行程樹，出現任何非 loopback 連線就失敗。
- 程式內防護只是縱深防禦；下方防火牆測試才是獨立證明。

### 防火牆封鎖測試

Windows（系統管理員 PowerShell）：
```powershell
New-NetFirewallRule -DisplayName "tlwu-block" -Direction Outbound -Action Block -Program (Get-Command python).Source
python -m tinglongwu --file talk.wav      # 必須仍可運作
Remove-NetFirewallRule -DisplayName "tlwu-block"
```
Linux：`unshare -n -r python -m tinglongwu --file talk.wav`（測試請用 `--file`），或 nftables：`nft add rule inet filter output meta skuid $UID drop`。
macOS：`pf` 規則（`block drop out quick all user $USER`，以 `pfctl -f` 載入），或用 Little Snitch／LuLu 禁止該 Python 連網。

接著執行 `python scripts/verify_offline.py --file talk.wav`。

## 安裝

Python 3.10+（以 3.12 測試）。
```
tlwu-setup --deps  # or: pip install -r requirements.txt && pip install -e . --no-deps    # 精確鎖版
```
- **Windows**：不需額外安裝（WASAPI 擷取預設喇叭）。
- **Linux**：PipeWire（含 pipewire-pulse）或 PulseAudio。預設擷取預設輸出的 monitor；用 `pactl list sources short` 查看，以 `--capture-device <子字串>` 指定。
- **macOS**：安裝 [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)，在「音訊 MIDI 設定」建立多重輸出裝置（喇叭＋BlackHole）並設為輸出。tlwu 會擷取名稱含 `BlackHole` 的裝置。ScreenCaptureKit 為未來選項。

### 模型（一次性，需要網路）
```
tlwu-setup --whisper small --langs ru --to zh
```
翻譯使用 Argos Translate 打包的 OPUS-MT 模型，直接以 CTranslate2 執行（不需安裝 Argos／torch）。路線：來源語言 -> 英文 -> 中文；來源為英文時略過英文步驟。

任何有 OPUS-MT/Argos 套件的語言組合都能用：`--langs` 下載「來源->英文」，`--to` 下載「英文->目標」（例如 `--langs ru,ja --to zh,ko`），只抓你需要的。缺少某組合時，tlwu 會停止並指出該組合與確切的下載指令。

**選用 M2M-100 引擎（`--engine m2m`）**：不經英文中介，直接翻譯，**MIT 授權**（可商用）。`tlwu-setup --m2m` 會下載 facebook/m2m100_1.2B 並以 `ct2-transformers-converter` 轉換；轉換需要 `torch` 與 `transformers`，它們不是執行期相依套件：請在拋棄式 venv 安裝，並用 `TLWU_CONVERTER_PYTHON` 指向它。僅從 `models/m2m-1.2B/` 載入。

**Qwen LLM 引擎（預設 `--engine auto`）**：本機 Qwen3 模型（llama.cpp、Q4_K_M GGUF）直接由來源語言翻成中文（不經英文中介、關閉思考模式、防重複迴圈），再依 `--zh-variant` 以 OpenCC 轉換。準確度遠高於 OPUS，極短句也不會出現亂碼。`tlwu-setup --qwen` 會把 Qwen3-8B 與 Qwen3-4B（約 7.5 GB）下載到模型目錄；另需安裝 `llama-cpp-python`（CUDA 版）。`auto` 會在 Whisper 載入後檢查剩餘 VRAM：剩 5.8 GB 以上用 Qwen3-8B，3.5 GB 以上用 Qwen3-4B，否則用 OPUS（CPU 模式或 Qwen 載入失敗時亦同）；啟動時會印一行說明選了哪個引擎。可用 `--engine qwen`（8B）、`qwen4b`、`opus`、`m2m` 強制指定。


### GPU（選用，NVIDIA）
`--device auto|cpu|cuda`（預設 `auto`：CTranslate2 偵測到 NVIDIA GPU 且函式庫可載入就用 CUDA，否則用 CPU）。GPU 上 Whisper 用 float16、OPUS/M2M 用 int8_float16；CPU 用 int8。需要的東西（一次性，需要網路）：
```
tlwu-setup --gpu     # = pip install nvidia-cublas-cu12 nvidia-cudnn-cu12（CUDA 12 cuBLAS + cuDNN 9，不必安裝 CUDA Toolkit）
```
另需支援 CUDA 12 以上的 NVIDIA 驅動程式。缺少 GPU 或這些函式庫時，tlwu 會印出明確訊息並退回 CPU（`--device cuda` 也一樣）。模型不會離開本機，函式庫只在本機載入。`large-v3` float16 約需 4 GB VRAM，`medium` 約 2.5 GB。

## 使用
```
python -m tinglongwu                          # 即時系統音訊，自動偵測語言，輸出繁體中文
tlwu --lang ru --model small            # 指定來源語言
tlwu --file talk.mp3                    # wav/flac/ogg/mp3/m4a，記憶體內解碼
tlwu --zh-variant cn                    # 簡體輸出（預設 tw = 繁體，OpenCC s2twp）
tlwu --to ko --no-vad --chunk 8        # target Korean via ru->en->ko (needs en-ko), fixed blocks
tlwu --file talk.mp3 --transcript-only > hyp.txt     # 只輸出原文，每段一行
python scripts/wer.py ref.txt hyp.txt --lang ru --partial   # WER（中日用 CER）；--partial 對齊到最相符的參考片段並回報涵蓋率
```
選項：`--model tiny|base|small|medium|large-v3|large-v3-turbo`、`--lang`、`--to`、`--chunk`（最長段落秒數，預設 4）、`--no-vad`、`--models-dir`、`--mic`、`--capture-device`（音訊裝置子字串）、`--device auto|cpu|cuda`（運算裝置）、`--engine auto|qwen|qwen4b|opus|m2m`、`--no-merge`、`--max-wait`（秒）、`--stream`、`--stream-src`、`--debug-latency`。

**串流草稿**（`--stream`，預設關閉、實驗性）：句子還在說時，以 Whisper（greedy）重辨目前累積的音訊，並立刻翻譯整段暫時結果（沿用同一翻譯引擎），原地只顯示一行灰色譯文。`--stream-src` 另顯示原文行（已確認前綴正常亮度、其餘灰色；LocalAgreement 概念）。`--debug-latency` 於 stderr 記錄「段落音訊起點 → 第一次譯文草稿／最終行」秒數。段落結束後由正式輸出取代草稿。需互動式終端機（ANSI）；草稿讓位給正式 ASR/MT，會多吃 GPU；搭配 `--file` 時以真實速度播放。

可用 VRAM 小於 9 GB 的 GPU（例如 8 GB 顯卡同時載入 Qwen3-8B）上，Whisper 會以 int8_float16 載入（該情況下每次推論比 fp16 快約 4 倍；可用環境變數 `TLWU_ASR_COMPUTE` 覆寫）。音樂／靜音時 Whisper 常見的字幕署名幻覺（「Продолжение следует」、「Subtitles by」、「字幕由」等；清單在 `tinglongwu/hallucination.py`）會被丟棄，且輸出一律把「妳」改成「你」。

**句子合併**：VAD 切出的段落常在句中結束。tlwu 會暫存 ASR 文字，在句尾（`.?!…。！？`）才翻譯；或在 `--max-wait` 秒（預設 1.5）／約 45 字後強制翻譯，先到者為準；`--no-merge` 則沿用逐段翻譯。即時模式下，句子中斷的文字最多多等 `--max-wait` 秒，句子已結束則不增加延遲。預設為低延遲（`--chunk 4`）。上下文：Qwen 引擎會看到前 2 句（原文＋譯文），讓被切開的句子讀起來連貫；Whisper 以前一段文字（≤100 字）作為 `initial_prompt`（出現重複／亂碼時自動丟棄）。Qwen 輸出會自動清理（多餘底線、markdown、引號、「Translation:」標籤）。Ctrl+C 約 1 秒內結束。

## 圖形介面（浮動字幕）

`tlwu-gui`（不跳出終端機視窗）或 `tlwu --gui`，會開啟一個小控制視窗（來源語言、目標 繁中／簡中／英文、引擎、語音模型、詞庫檔、「儲存逐字稿」、開始／停止），以及一條無邊框、永遠置頂的字幕條（預設螢幕下方置中，約 70% 寬）。顯示最近 2 行；中文草稿為灰色，final 譯文出現後在原位換成白色。可拖曳移動；Ctrl+滾輪或 +/- 調整字體；右鍵選單：顯示／隱藏原文行、字體大小、複製最後一行、暫停顯示、結束。設定會記在 `%APPDATA%\tinglongwu\gui.json`。僅用標準庫 tkinter，完全離線；不加 `--gui` 時 CLI 行為不變。

## 主題詞庫（`--glossary PATH`）
用純文字檔讓 tlwu 適應特定主題：`PATH` 可為 UTF-8 的 `.txt` 檔或放多個 `.txt` 的資料夾。每行一筆 `原文 = 譯文`；`#` 開頭為註解；沒有 `=` 的行視為提示詞；`a | b = 譯文` 可加入別的原文寫法（例如常聽錯的字）。原文詞會作為 `hotwords` 交給 Whisper；原文出現在當前句子或前 2 行的條目（不分大小寫、容許俄文詞尾變化）會寫進 Qwen 提示，要求使用指定譯法（OPUS/M2M 忽略譯文）。`2.4 = 2.4 GHz` 這類數字條目也會把辨識結果的「2 и 4」「2,4」改成「2.4」。僅限明確指定、完全離線。範例：`docs/examples/glossary_wifi_ru.txt`。
```
tlwu --lang ru --model large-v3-turbo --engine qwen --stream --glossary docs/examples/glossary_wifi_ru.txt
```
重複迴圈（Whisper 反覆輸出同一句）：最終辨識啟用溫度回退，偵測到迴圈時不帶前文提示重新解碼一次，剩餘重複在翻譯前收斂成一份。

## 架構
`tinglongwu/capture`（AudioSource：wasapi、pulse、macos、file、mic）-> `segment.py`（Silero VAD，0.5 秒重疊）-> `asr.py`（Recognizer）-> `translate.py`（Translator）-> `sink.py`（Sink：TextSink、TranscriptSink、TTSSink 樁）。每層都是小介面，可替換。

## 授權與來源
| 元件 | 授權 | 來源 |
|---|---|---|
| tlwu | 專有軟體 | 本專案 |
| faster-whisper | MIT | github.com/SYSTRAN/faster-whisper |
| Whisper 權重（Systran CT2 轉換版） | MIT | huggingface.co/Systran |
| Silero VAD（faster-whisper 內建 ONNX） | MIT | github.com/snakers4/silero-vad |
| CTranslate2 | MIT | github.com/OpenNMT/CTranslate2 |
| OPUS-MT 模型（經 Argos 套件） | CC-BY 4.0 | github.com/Helsinki-NLP/Opus-MT、argosopentech/argospm-index |
| SentencePiece | Apache-2.0 | github.com/google/sentencepiece |
| OpenCC（opencc-python-reimplemented） | Apache-2.0 | github.com/yichen0831/opencc-python |
| soundcard | BSD-3 | github.com/bastibe/SoundCard |
| soundfile / libsndfile | BSD-3 / LGPL-2.1 | github.com/bastibe/python-soundfile |
| PyAV（FFmpeg 函式庫） | BSD-3 / LGPL | github.com/PyAV-Org/PyAV |
| numpy、scipy、onnxruntime | BSD-3、BSD-3、MIT | |
| psutil、huggingface_hub | BSD-3、Apache-2.0 | |
| NLLB-200 | CC-BY-NC 4.0（非商用） | 未隨附，無程式碼亦無安裝選項 |
| Piper TTS（路線圖） | MIT | github.com/rhasspy/piper |

再散布模型前請自行確認上游授權。

## 基準測試

以 `scripts/bench/`（開發工具，不屬於產品）量測翻譯品質與速度。

- 硬體：RTX 3050 8 GB。資料：FLORES-200 devtest（1012 句，俄文 -> 繁體中文，使用原文文字；50 句的列為前 50 句）。指標：chrF（sacrebleu）與 COMET（Unbabel/wmt22-comet-da）。延遲不含模型載入。Qwen 引擎為 Q4_K_M GGUF、2048 context。

| engine | chrF | COMET | median s/sentence | peak VRAM (+MiB) |
|---|---|---|---|---|
| opus (default, CPU/GPU) | 23.6 | 0.824 | 0.14 | 424 |
| m2m | 22.9 | 0.819 | 0.34 | 1724 |
| qwen3-4b | 23.5 | 0.849 | 0.46 | 3062 |
| qwen25-7b | 23.5 | 0.840 | 0.72 | 4682 |
| madlad3b | 26.1 | 0.846 | 0.69 | 3552 |
| qwen3-8b | 26.0 | 0.865 | 0.77 | 5148 |
| qwen3-8b + context | 26.3 | 0.867 | 0.80 | 5150 |
| Google Cloud Translation v2 (reference) | 29.8 | 0.879 | - | - |

| direction (first 50 sentences) | engine | chrF | COMET | median s/sentence |
|---|---|---|---|---|
| en->zho_Hant | opus | 29.9 | 0.854 | 0.10 |
| en->zho_Hant | qwen3-4b | 30.7 | 0.881 | 0.54 |
| en->zho_Hant | qwen3-8b | 31.2 | 0.892 | 0.91 |
| en->zho_Hant | Google | 37.2 | 0.900 | - |
| ru->eng | opus | 62.2 | 0.867 | 0.07 |
| ru->eng | qwen3-4b | 62.9 | 0.867 | 0.52 |
| ru->eng | qwen3-8b | 64.3 | 0.870 | 0.81 |
| ru->eng | Google | 67.2 | 0.883 | - |

另以 FLEURS 200 句語音（ASR large-v3-turbo -> 翻譯）測得相同排序（opus 22.7、qwen3-8b 24.9、qwen3-4b 22.3、Google 28.5 chrF）。

重現方式（需要 bench venv、`%USERPROFILE%/tlwu-eval` 下的資料；Google 欄需要 `google_key.txt` 內的 Cloud Translation 金鑰，可加 `--no-google` 略過）：

```
.venv-bench\Scripts\python scripts/bench/run.py --full --engines m2m,qwen3-8b,qwen3-8b+ctx,madlad3b,qwen3-4b,qwen25-7b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair en-zh --engines qwen3-8b,qwen3-4b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair ru-en --engines qwen3-8b,qwen3-4b
```

**Google 在所有測試方向的分數都高於所有離線引擎。** 離線引擎以部分品質換取無須網路、資料不離開本機。50 句樣本僅供參考。

## 已知限制
- **延遲**約為段落長度加處理時間：停頓（VAD）後或達 `--chunk` 秒才出字。降低 `--chunk` 可更快，但準確度下降。
- **英文中介**會損失術語與語氣（兩次有損轉換），人名、性別、敬語較差。
- **Whisper 在靜音、音樂、雜訊時會幻聽**；以靜音閘門、no-speech／logprob 門檻與 `condition_on_previous_text=False` 減輕，無法根除。
- **自動語言偵測**在第一個辨識段落後鎖定；混語串流請用 `--lang`。
- **CPU 速度與模型大小**：CPU（int8）上 `medium`／`large-v3` 可能跟不上即時；有 NVIDIA GPU 請用 `--device cuda`。
- **macOS** 需要虛擬回放裝置（沒有 ScreenCaptureKit 輔助程式就無原生系統音訊擷取）。
- **繁體中文**是以 OpenCC 對簡體模型輸出做事後轉換，用詞僅近似。
- 即時擷取後端已實作，但自動測試只涵蓋 `--file`（無音訊硬體）。

## 同意聲明
擷取通話或會議前，請先取得參與者同意；各地對錄音與逐字轉錄的法規不同。

## 路線圖
麥克風 -> 中文 -> 俄文翻譯 -> Piper 離線 TTS -> 虛擬麥克風（VB-Cable／BlackHole／PipeWire null-sink），並做回音排除避免合成語音被重新擷取。

## 測試
`pip install -r requirements-dev.txt && pytest`（不需模型與網路）。

專有軟體，保留所有權利。(c) 2026 sunososobro-hub。
