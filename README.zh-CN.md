# tinglongWu — 台语“听拢有”(thiann lóng ū)：“我全都听懂了”。实时语音翻译，100% 离线、可验证。

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

Architecture / 架構 / 架构: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [zh-TW](docs/ARCHITECTURE.zh-TW.md) · [zh-CN](docs/ARCHITECTURE.zh-CN.md)

完全本地、离线的实时语音翻译器：**系统音频输出 -> 采集 -> 语音识别 -> 翻译 -> 文本**。
数据不会离开你的电脑：不使用云端 API、没有遥测，音频和转写文本都不会写入磁盘。

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你过得怎么样？
```

## 安装／使用分离（请先阅读）

同一个仓库、两个程序，边界明确：

| | `tlwu-setup`（包 `tinglongwu_setup`） | `tlwu`（包 `tlwu`） |
|---|---|---|
| 网络 | **会联网**，是唯一的联网代码 | **永不联网**，没有任何下载／网络代码 |
| 工作 | 安装锁定版本依赖、下载并用 SHA256 固定模型、构建／离线安装离线包 | 采集音频、识别、翻译、输出 |
| 时机 | 有网络时运行一次 | 每次运行，可完全离线 |

`tests/test_no_network_code.py` 会静态扫描 `tinglongwu/`，若导入 requests、urllib.request、http.client、huggingface_hub、argostranslate、socket（防护模块 `tinglongwu/offline.py` 除外）或 `tinglongwu_setup` 即失败。

```
tlwu-setup --deps                                  # 安装锁定版本依赖
tlwu-setup --whisper small --langs ru --to zh      # 下载并固定模型
tlwu-setup --bundle tlwu-bundle.zip                 # 打包 wheel + 模型，供隔离网络的电脑使用
tlwu-setup --from-bundle tlwu-bundle.zip            # 在离线电脑上安装并校验，无需网络
```
离线包与平台相关：请在与目标机相同的操作系统、CPU 架构和 Python 次版本上构建。

## 隐私模型

- 音频只存在于内存；转写文本只输出到 stdout，不写任何日志。
- `import tinglongwu` 时（早于任何 HF/ctranslate2 导入）强制设置离线环境变量：`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`HF_HUB_DISABLE_TELEMETRY`、`DO_NOT_TRACK`、`ARGOS_*`。
- 运行时**网络防护**会拦截 `socket.connect/connect_ex/getaddrinfo/gethostbyname`，任何非 loopback 目标一律抛出异常，始终开启。
- 模型只从本地 `models/` 加载（`--models-dir` 或环境变量 `TLWU_MODELS`）；缺少模型时报错并指向下载脚本，不会自动下载。
- `tlwu-setup` (`tinglongwu_setup`) 是唯一会联网的程序。首次下载时“固定”（HF commit sha／包 URL 以及每个文件的 SHA256，存于 `models/manifest.json`），之后每次都校验（`--verify` 可离线运行）。
- `scripts/verify_offline.py` 运行 tlwu 并用 psutil 轮询整个进程树，出现任何非 loopback 连接即失败。
- 进程内防护只是纵深防御；下面的防火墙测试才是独立证明。

### 防火墙封锁测试

Windows（管理员 PowerShell）：
```powershell
New-NetFirewallRule -DisplayName "tlwu-block" -Direction Outbound -Action Block -Program (Get-Command python).Source
python -m tinglongwu --file talk.wav      # 必须仍能工作
Remove-NetFirewallRule -DisplayName "tlwu-block"
```
Linux：`unshare -n -r python -m tinglongwu --file talk.wav`（测试请用 `--file`），或 nftables：`nft add rule inet filter output meta skuid $UID drop`。
macOS：`pf` 规则（`block drop out quick all user $USER`，用 `pfctl -f` 加载），或用 Little Snitch／LuLu 禁止该 Python 联网。

然后运行 `python scripts/verify_offline.py --file talk.wav`。

## 安装

Python 3.10+（以 3.12 测试）。
```
tlwu-setup --deps  # or: pip install -r requirements.txt && pip install -e . --no-deps    # 精确锁定版本
```
- **Windows**：无需额外安装（WASAPI 采集默认扬声器）。
- **Linux**：PipeWire（含 pipewire-pulse）或 PulseAudio。默认采集默认输出的 monitor；用 `pactl list sources short` 查看，用 `--capture-device <子串>` 指定。
- **macOS**：安装 [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)，在“音频 MIDI 设置”中创建多输出设备（扬声器＋BlackHole）并设为输出。tlwu 会采集名称包含 `BlackHole` 的设备。ScreenCaptureKit 为未来选项。

### 模型（一次性，需要网络）
```
tlwu-setup --whisper small --langs ru --to zh
```
翻译使用 Argos Translate 打包的 OPUS-MT 模型，直接用 CTranslate2 运行（无需安装 Argos／torch）。路线：源语言 -> 英文 -> 中文；源语言为英文时跳过英文步骤。

任何有 OPUS-MT/Argos 包的语言组合都能用：`--langs` 下载“源语言->英文”，`--to` 下载“英文->目标”（例如 `--langs ru,ja --to zh,ko`），只下载你需要的。缺少某组合时，tlwu 会停止并指出该组合和确切的下载命令。

**可选 M2M-100 引擎（`--engine m2m`）**：不经英文中转，直接翻译，**MIT 许可**（可商用）。`tlwu-setup --m2m` 会下载 facebook/m2m100_1.2B 并用 `ct2-transformers-converter` 转换；转换需要 `torch` 和 `transformers`，它们不是运行时依赖：请在一次性 venv 中安装，并用 `TLWU_CONVERTER_PYTHON` 指向它。仅从 `models/m2m-1.2B/` 加载。


### GPU（可选，NVIDIA）
`--device auto|cpu|cuda`（默认 `auto`：CTranslate2 检测到 NVIDIA GPU 且库可加载就用 CUDA，否则用 CPU）。GPU 上 Whisper 用 float16、OPUS/M2M 用 int8_float16；CPU 用 int8。需要的东西（一次性，需要网络）：
```
tlwu-setup --gpu     # = pip install nvidia-cublas-cu12 nvidia-cudnn-cu12（CUDA 12 cuBLAS + cuDNN 9，无需安装 CUDA Toolkit）
```
另需支持 CUDA 12 及以上的 NVIDIA 驱动。缺少 GPU 或这些库时，tlwu 会打印明确提示并回退到 CPU（`--device cuda` 也一样）。模型不会离开本机，库只在本机加载。`large-v3` float16 约需 4 GB VRAM，`medium` 约 2.5 GB。

## 使用
```
python -m tinglongwu                          # 实时系统音频，自动检测语言，输出繁体中文
tlwu --lang ru --model small            # 指定源语言
tlwu --file talk.mp3                    # wav/flac/ogg/mp3/m4a，内存中解码
tlwu --zh-variant cn                    # 简体输出（默认 tw = 繁体，OpenCC s2twp）
tlwu --to ko --no-vad --chunk 8        # target Korean via ru->en->ko (needs en-ko), fixed blocks
tlwu --file talk.mp3 --transcript-only > hyp.txt     # 只输出原文，每段一行
python scripts/wer.py ref.txt hyp.txt --lang ru --partial   # WER（中日用 CER）；--partial 对齐到最匹配的参考片段并报告覆盖率
```
选项：`--model tiny|base|small|medium|large-v3|large-v3-turbo`、`--lang`、`--to`、`--chunk`（最长段落秒数，默认 4）、`--no-vad`、`--models-dir`、`--mic`、`--capture-device`（音频设备子串）、`--device auto|cpu|cuda`（计算设备）、`--engine opus|m2m`、`--no-merge`、`--max-wait`（秒）、`--stream`、`--stream-src`、`--debug-latency`。

**流式草稿**（`--stream`，默认关闭、实验性）：句子还在说时，用 Whisper（greedy）重识别当前累积的音频，并立刻翻译整段暂时结果（沿用同一翻译引擎），原地只显示一行灰色译文。`--stream-src` 另显示原文行（已确认前缀正常亮度、其余灰色；LocalAgreement 思路）。`--debug-latency` 在 stderr 记录“段落音频起点 → 首次译文草稿/最终行”的秒数。段落结束后由正式输出取代草稿。需交互式终端（ANSI）；草稿让位于正式 ASR/MT，会多占 GPU；配合 `--file` 时按真实速度播放。

可用显存小于 9 GB 的 GPU（例如 8 GB 显卡同时加载 Qwen3-8B）上，Whisper 以 int8_float16 加载（该情况下每次推理比 fp16 快约 4 倍；可用环境变量 `TLWU_ASR_COMPUTE` 覆盖）。音乐/静音时 Whisper 常见的字幕署名幻觉（“Продолжение следует”、“Subtitles by”、“字幕由”等；列表在 `tinglongwu/hallucination.py`）会被丢弃，输出中的“妳”一律改为“你”。

**句子合并**：VAD 切出的段落常在句中结束。tlwu 会缓存 ASR 文字，在句尾（`.?!…。！？`）才翻译；或在 `--max-wait` 秒（默认 1.5）／约 45 词后强制翻译，先到者为准；`--no-merge` 则沿用逐段翻译。实时模式下，句子中断的文字最多多等 `--max-wait` 秒，句子已结束则不增加延迟。默认为低延迟（`--chunk 4`）。上下文：Qwen 引擎会看到前 2 句（原文+译文），让被切开的句子读起来连贯；Whisper 以前一段文字（≤100 字）作为 `initial_prompt`（出现重复/乱码时自动丢弃）。Qwen 输出会自动清理（多余下划线、markdown、引号、“Translation:”标签）。Ctrl+C 约 1 秒内退出。

## 主题词库（`--glossary PATH`）
用纯文本文件让 tlwu 适应特定主题：`PATH` 可为 UTF-8 的 `.txt` 文件或放多个 `.txt` 的文件夹。每行一条 `原文 = 译文`；`#` 开头为注释；没有 `=` 的行视为提示词；`a | b = 译文` 可加入其他原文写法（例如常听错的词）。原文词会作为 `hotwords` 交给 Whisper；原文出现在当前句子或前 2 行的条目（不区分大小写、容许俄语词尾变化）会写入 Qwen 提示，要求使用指定译法（OPUS/M2M 忽略译文）。`2.4 = 2.4 GHz` 这类数字条目也会把识别结果中的“2 и 4”“2,4”改成“2.4”。仅限显式指定、完全离线。示例：`docs/examples/glossary_wifi_ru.txt`。
```
tlwu --lang ru --model large-v3-turbo --engine qwen --stream --glossary docs/examples/glossary_wifi_ru.txt
```
重复循环（Whisper 反复输出同一句）：最终识别启用温度回退，检测到循环时不带前文提示重新解码一次，剩余重复在翻译前收敛成一份。

## 架构
`tinglongwu/capture`（AudioSource：wasapi、pulse、macos、file、mic）-> `segment.py`（Silero VAD，0.5 秒重叠）-> `asr.py`（Recognizer）-> `translate.py`（Translator）-> `sink.py`（Sink：TextSink、TranscriptSink、TTSSink 桩）。每层都是小接口，可替换。

## 许可证与来源
| 组件 | 许可证 | 来源 |
|---|---|---|
| tlwu | 专有软件 | 本项目 |
| faster-whisper | MIT | github.com/SYSTRAN/faster-whisper |
| Whisper 权重（Systran CT2 转换版） | MIT | huggingface.co/Systran |
| Silero VAD（faster-whisper 内置 ONNX） | MIT | github.com/snakers4/silero-vad |
| CTranslate2 | MIT | github.com/OpenNMT/CTranslate2 |
| OPUS-MT 模型（经 Argos 包） | CC-BY 4.0 | github.com/Helsinki-NLP/Opus-MT、argosopentech/argospm-index |
| SentencePiece | Apache-2.0 | github.com/google/sentencepiece |
| OpenCC（opencc-python-reimplemented） | Apache-2.0 | github.com/yichen0831/opencc-python |
| soundcard | BSD-3 | github.com/bastibe/SoundCard |
| soundfile / libsndfile | BSD-3 / LGPL-2.1 | github.com/bastibe/python-soundfile |
| PyAV（FFmpeg 库） | BSD-3 / LGPL | github.com/PyAV-Org/PyAV |
| numpy、scipy、onnxruntime | BSD-3、BSD-3、MIT | |
| psutil、huggingface_hub | BSD-3、Apache-2.0 | |
| NLLB-200 | CC-BY-NC 4.0（非商用） | 未随附，无代码亦无安装选项 |
| Piper TTS（路线图） | MIT | github.com/rhasspy/piper |

再分发模型前请自行确认上游许可证。

## 已知限制
- **延迟**约为段落长度加处理时间：停顿（VAD）后或达到 `--chunk` 秒才出字。减小 `--chunk` 可更快，但准确率下降。
- **英文中转**会损失术语和语气（两次有损转换），人名、性别、敬语较差。
- **Whisper 在静音、音乐、噪声时会幻听**；用静音闸门、no-speech／logprob 阈值和 `condition_on_previous_text=False` 缓解，无法根除。
- **自动语言检测**在第一个识别段落后锁定；混合语言流请用 `--lang`。
- **CPU 速度与模型大小**：CPU（int8）上 `medium`／`large-v3` 可能跟不上实时；有 NVIDIA GPU 请用 `--device cuda`。
- **macOS** 需要虚拟回环设备（没有 ScreenCaptureKit 辅助程序就没有原生系统音频采集）。
- **繁体中文**是用 OpenCC 对简体模型输出做事后转换，用词仅近似。
- 实时采集后端已实现，但自动化测试只覆盖 `--file`（无音频硬件）。

## 同意声明
采集通话或会议前，请先取得参与者同意；各地对录音和转写的法规不同。

## 路线图
麦克风 -> 中文 -> 俄文翻译 -> Piper 离线 TTS -> 虚拟麦克风（VB-Cable／BlackHole／PipeWire null-sink），并做回声排除，避免合成语音被重新采集。

## 测试
`pip install -r requirements-dev.txt && pytest`（不需要模型和网络）。

专有软件，保留所有权利。(c) 2026 sunososobro-hub。
