# tinglongWu — 台语“听拢有”(thiann lóng ū)：“我全都听懂了”。实时语音翻译，运行时离线、可验证（安装与下载模型需要网络；进程内防护只是纵深防御，不是证明，见“隐私模型”）。

[English](README.md) | [繁體中文](README.zh-TW.md) | [简体中文](README.zh-CN.md)

全新 Linux / macOS / Windows 机器上的一键安装测试： [![install-test](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml/badge.svg)](https://github.com/sunososobro-hub/tinglongwu/actions/workflows/install-test.yml)

Architecture / 架構 / 架构: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [zh-TW](docs/ARCHITECTURE.zh-TW.md) · [zh-CN](docs/ARCHITECTURE.zh-CN.md)

完全本地、离线的实时语音翻译器：**系统音频输出 -> 采集 -> 语音识别 -> 翻译 -> 文本**。
会采集电脑播放的所有声音（系统输出，不是单个程序）。运行时完全不使用网络；只有安装／首次下载模型才会用到网络。
数据不会离开你的电脑：不使用云端 API、没有遥测，音频和转写文本都不会写入磁盘。

```
[RU] Привет, как дела?
[EN] Hello, how are you?
[ZH] 你好，你过得怎么样？
```

## 一行命令安装（Windows / Mac / Linux）

每条命令都会安装最新 GitHub Release 的确切版本（`main` 为稳定版，`dev` 为开发版）。可重复运行。

| 系统 | 命令 |
|---|---|
| Windows（PowerShell） | `irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 \| iex` |
| macOS（终端） | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh \| bash` |
| Linux（bash） | `curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh \| bash` |

每个 release 都附 `SHA256SUMS.txt`；想在运行前验证，可先下载脚本比对。Linux 安装程序在任何 `apt` 操作前都会问 y/N，且不会下载模型（菜单每次下载前都会问）。Windows 命令会把源码下载到 `%USERPROFILE%\tinglongwu`，再运行与下方相同的 `install.ps1`（执行策略绕过只作用于该子进程）。

## 快速安装（Windows）

适用 Windows 10/11，有 NVIDIA 显卡最佳（没有也能运行，只是较慢）。请先安装 [Python 3.12](https://www.python.org/downloads/windows/)，安装时勾选 **Add python.exe to PATH**。

1. 在 GitHub 点 **Code > Download ZIP**，下载后右键 ZIP > **全部解压缩**。
2. 打开文件夹，双击 **`install.bat`**，耐心等待（约 10–30 分钟，最多下载约 10 GB；中断后可重跑）。
3. 双击桌面新出现的快捷方式 **聽攏有 tinglongWu**。

卸载：删除文件夹和桌面快捷方式即可。`install.ps1 -DryRun` 只显示将执行的操作、不安装。Qwen 引擎使用 `requirements-qwen.txt`（CUDA 版 llama-cpp-python）；若没有合适的预编译 wheel，安装程序会自动改用 OPUS 引擎。

## Mac 一行安装

Apple Silicon 与 Intel Mac 均可（macOS 12 以上）。在“终端”粘贴：

```
curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/mac_install.sh | bash
```

脚本检查 Homebrew（没有只会打印官方安装命令，不会静默安装）、安装 Python 3.12／git／BlackHole 2ch 与 16ch、克隆到 `~/tinglongwu`、建立环境、安装英文语音（Kokoro）并下载模型、安装 `tlwu-menu` 命令；可重复运行（即修复／更新）。最后会提示唯一的手动步骤：在“音频 MIDI 设置”创建多输出设备（扬声器/耳机 + BlackHole 2ch）并设为系统输出；Meet/Zoom 麦克风选 **BlackHole 16ch**（2ch 只用于“听”，16ch 只用于“说”，避免自己的语音回灌）。之后运行 `tlwu-menu`；各平台菜单启动时都会静默 `git pull --ff-only` 自动更新（约 6 秒上限，离线不影响，`TLWU_NO_UPDATE=1` 可关闭）。尚未在真实 Mac 上测试。

## 发行版（运行前请先阅读）

Windows 发行 zip **未签名**，由维护者在本机用 Nuitka 构建，**不可复现构建**。其 SHA256 公布在 release 说明中：运行前请用 `Get-FileHash tinglongwu-win64.zip` 比对。哈希只证明你拿到的是维护者发布的那个文件。若要真正的验证，请从源码安装（`install.bat` 或下面的步骤）：源码、锁版依赖和模型哈希都可审计。

## 验证状态

✅ 已在实机验证，🧪 尚未验证（链接至实测 issue）。

| | Windows | macOS | Linux |
|---|---|---|---|
| 一行安装 | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| 聆听 | ✅ | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| 朗读（虚拟麦克风） | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/6) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ |
| 会议 | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/4) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | ✅ (own side) |
| 防火墙封锁离线 | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/8) | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/7) |
| exe | [🧪](https://github.com/sunososobro-hub/tinglongwu/issues/9) | — | — |

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

- 音频只存在于内存；转写文本只输出到 stdout，不写任何日志（只有明确指定 `--save`、`--save-result PATH` 才会存档）。
- `import tinglongwu` 时（早于任何 HF/ctranslate2 导入）强制设置离线环境变量：`HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`、`HF_HUB_DISABLE_TELEMETRY`、`DO_NOT_TRACK`、`ARGOS_*`。
- 运行时**网络防护**会拦截 `socket.connect/connect_ex/getaddrinfo/gethostbyname`，任何非 loopback 目标一律抛出异常，始终开启。
- 模型只从本地 `models/` 加载（`--models-dir` 或环境变量 `TLWU_MODELS`）；缺少模型时报错并指向下载脚本，不会自动下载。
- `tlwu-setup` (`tinglongwu_setup`) 是唯一会联网的程序。首次下载时“固定”（HF commit sha／包 URL 以及每个文件的 SHA256，版本库内的固定在 `tinglongwu_setup/manifest.json`，本机记录存于 `models/manifest.json`），之后每次都校验（`--verify` 可离线运行）。
- `scripts/verify_offline.py` 运行 tlwu 并用 psutil 轮询整个进程树，出现任何非 loopback 连接即失败。
- 进程内防护只是纵深防御，**不是证明**：它只拦截 Python 层的 socket（`connect`、`connect_ex`、`sendto`、名称解析）。原生库自己打开的 socket（ctranslate2、onnxruntime、llama.cpp 及其他 C 扩展）和子进程**不在**拦截范围。下面的防火墙测试才是真正的独立证明。

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

带哈希校验的安装（推荐）：`requirements.lock.txt` 列出所有依赖（含间接依赖）及 PyPI 公布的全部 SHA256，pip 会拒绝被篡改或替换的文件：
```
pip install --require-hashes -r requirements.lock.txt && pip install -e . --no-deps
```
`requirements.txt`（只有 `==` 锁版、无哈希）仍可使用。用 `python scripts/make_lock.py` 重新生成 lock。（`httpx2`/`httpcore2` 不是仿冒包：它们是 Pydantic 维护的 httpx 分支，`huggingface_hub` 2.x 依赖它们。）
- **Windows**：无需额外安装（WASAPI 采集默认扬声器）。
- **Linux**：PipeWire（含 pipewire-pulse）或 PulseAudio。默认采集默认输出的 monitor；用 `pactl list sources short` 查看，用 `--capture-device <子串>` 指定。
- **macOS**：安装 [BlackHole 2ch](https://github.com/ExistentialAudio/BlackHole)，在“音频 MIDI 设置”中创建多输出设备（扬声器＋BlackHole）并设为输出。tlwu 会采集名称包含 `BlackHole` 的设备。ScreenCaptureKit 为未来选项。

### 模型（一次性，需要网络）
```
tlwu-setup --whisper small --langs ru --to zh
```
翻译使用 Argos Translate 打包的 OPUS-MT 模型，直接用 CTranslate2 运行（无需安装 Argos／torch）。路线：源语言 -> 英文 -> 中文；源语言为英文时跳过英文步骤。

任何有 OPUS-MT/Argos 包的语言组合都能用：`--langs` 下载“源语言->英文”，`--to` 下载“英文->目标”（例如 `--langs ru,ja --to zh,ko`），只下载你需要的。缺少某组合时，tlwu 会停止并指出该组合和确切的下载命令。

**可选 M2M-100 引擎（`--engine m2m`）**：不经英文中转，直接翻译，**MIT 许可**（可商用）。`tlwu-setup --m2m` 会下载 facebook/m2m100_1.2B 并用 `ct2-transformers-converter` 转换；转换需要 `torch` 和 `transformers`，它们不是运行时依赖：请在一次性 venv 中安装，并用 `TLWU_CONVERTER_PYTHON` 指向它。仅从 `models/m2m-1.2B/` 加载。 转换只读 `model.safetensors`（固定 Hugging Face 官方 SFconvertbot PR 的 commit，因为 `main` 只有 pickle 的 `pytorch_model.bin`），不会反序列化任何 pickle；没有固定的 safetensors 版本时 `tlwu-setup` 拒绝转换。


#### 模型来源固定

`tinglongwu_setup/manifest.json` 已纳入版本控制：每个可下载模型都固定到确切的 Hugging Face commit 与各文件 SHA256。`tlwu-setup` 以 `revision=<固定的 commit>` 下载，哈希不符即中止；`tlwu-setup --verify` 可离线复验。维护者用 `scripts/make_model_pins.py` 更新固定（请审阅差异）。

| 模型 | 来源 repo | 维护者 | 备注 |
|---|---|---|---|
| Whisper tiny/base/small/medium/large-v3（CT2） | `Systran/faster-whisper-*` | SYSTRAN | MIT |
| **Whisper large-v3-turbo（CT2）** | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | **mobiuslabsgmbh（第三方，非 OpenAI、非 SYSTRAN）** | MIT；社区的 CTranslate2 转换版，信任范围仅限于固定的 commit 与哈希 |
| Qwen3-8B / 4B Q4_K_M GGUF | `Qwen/Qwen3-*-GGUF` | Qwen（阿里巴巴） | Apache-2.0 |
| M2M-100 1.2B | `facebook/m2m100_1.2B` | Meta | MIT；**仅用 safetensors**（见上） |
| OPUS-MT（Argos 包） | `argos-net.com` | Argos Open Tech | CC-BY 4.0；`ru-en`、`en-zh` 以 URL＋压缩包 SHA256 固定；其他语对首次使用时查询可变的 Argos 索引，之后才固定（会打印警告） |

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
选项：`--model tiny|base|small|medium|large-v3|large-v3-turbo`、`--lang`、`--to`、`--preset meeting|chat`（默认 meeting）、`--chunk`（最长段落秒数，默认依 preset）、`--no-vad`、`--models-dir`、`--mic`、`--capture-device`（音频设备子串）、`--device auto|cpu|cuda`（计算设备）、`--engine opus|m2m`、`--no-merge`、`--max-wait`（秒，默认依 preset）、`--stream`、`--stream-src`、`--debug-latency`。

**流式草稿**（`--stream`，默认关闭、实验性）：句子还在说时，用 Whisper（greedy）重识别当前累积的音频，并立刻翻译整段暂时结果（沿用同一翻译引擎），原地只显示一行灰色译文。`--stream-src` 另显示原文行（已确认前缀正常亮度、其余灰色；LocalAgreement 思路）。`--debug-latency` 在 stderr 记录“段落音频起点 → 首次译文草稿/最终行”的秒数。段落结束后由正式输出取代草稿。需交互式终端（ANSI）；草稿让位于正式 ASR/MT，会多占 GPU；配合 `--file` 时按真实速度播放。

可用显存小于 9 GB 的 GPU（例如 8 GB 显卡同时加载 Qwen3-8B）上，Whisper 以 int8_float16 加载（该情况下每次推理比 fp16 快约 4 倍；可用环境变量 `TLWU_ASR_COMPUTE` 覆盖）。音乐/静音时 Whisper 常见的字幕署名幻觉（“Продолжение следует”、“Subtitles by”、“字幕由”等；列表在 `tinglongwu/hallucination.py`）会被丢弃，输出中的“妳”一律改为“你”。

**句子合并**：VAD 切出的段落常在句中结束。tlwu 会缓存 ASR 文字，在句尾（`.?!…。！？`）才翻译；或说话者停顿 ≥ 0.8 秒（VAD 静音）时送出；`--max-wait` 秒／约 45 词只是上限，先到者为准。因达到 chunk 上限而被硬切的段落，Whisper 加在句尾的“...”不算句尾，合并时也会去掉切点重复的词；`--no-merge` 则沿用逐段翻译。**预设组合**：`--preset meeting`（默认）＝`--chunk 8 --max-wait 6`，优先保证完整句子，延迟约 5–8 秒；`--preset chat`＝`--chunk 4 --max-wait 1.5`，延迟较低但更多破碎句子（翻译质量较差）。显式指定 `--chunk`／`--max-wait` 会覆盖 preset；GUI 有“模式”选项。反向（说话即翻译）模式有自己的设置。上下文：Qwen 引擎会看到前 2 句（原文+译文），让被切开的句子读起来连贯；Whisper 以前一段文字（≤100 字）作为 `initial_prompt`（出现重复/乱码时自动丢弃）。Qwen 输出会自动清理（多余下划线、markdown、引号、“Translation:”标签）。Ctrl+C 约 1 秒内退出。

## 图形界面（浮动字幕）

`tlwu-gui`（不弹出终端窗口）或 `tlwu --gui`，会打开一个小控制窗口（源语言、目标 繁中／简中／英文、引擎、语音模型、词库文件、“保存逐字稿”、开始／停止），以及一条无边框、始终置顶的字幕条（默认屏幕下方居中，约 70% 宽）。显示最近 2 行；中文草稿为灰色，final 译文出现后在原位换成白色。可拖动；Ctrl+滚轮或 +/- 调整字号；右键菜单：显示／隐藏原文行、字号、复制最后一行、暂停显示、退出。设置保存在 `%APPDATA%\tinglongwu\gui.json`。仅用标准库 tkinter，完全离线；不加 `--gui` 时 CLI 行为不变。

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
| tlwu | MIT | 本项目 |
| faster-whisper | MIT | github.com/SYSTRAN/faster-whisper |
| Whisper 权重（Systran CT2 转换版） | MIT | huggingface.co/Systran |
| Whisper large-v3-turbo 权重（第三方 mobiuslabsgmbh 的 CT2 转换版） | MIT | huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo |
| Silero VAD（faster-whisper 内置 ONNX，Silero Team） | MIT | github.com/snakers4/silero-vad |
| CTranslate2 | MIT | github.com/OpenNMT/CTranslate2 |
| OPUS-MT 模型（Helsinki-NLP，需注明出处） | CC-BY 4.0 | github.com/Helsinki-NLP/Opus-MT、argosopentech/argospm-index |
| Argos Translate（模型打包／索引） | MIT | github.com/argosopentech/argos-translate |
| SentencePiece | Apache-2.0 | github.com/google/sentencepiece |
| OpenCC（opencc-python-reimplemented） | Apache-2.0 | github.com/yichen0831/opencc-python |
| soundcard | BSD-3 | github.com/bastibe/SoundCard |
| soundfile / libsndfile | BSD-3 / LGPL-2.1 | github.com/bastibe/python-soundfile |
| PyAV（FFmpeg 库；仅源码安装使用，Windows exe 已用桩替换 `av`） | BSD-3 / LGPL | github.com/PyAV-Org/PyAV |
| numpy、scipy、onnxruntime | BSD-3、BSD-3、MIT | |
| psutil、huggingface_hub | BSD-3、Apache-2.0 | |
| NLLB-200 | CC-BY-NC 4.0（非商用） | 未随附，无代码亦无安装选项 |
| Qwen3 LLM（GGUF；Qwen 官方 repo） | Apache-2.0 | huggingface.co/Qwen/Qwen3-4B-GGUF（取自模型卡元数据） |
| llama-cpp-python（运行 Qwen3／Hy-MT2） | MIT | github.com/abetlen/llama-cpp-python |
| Hy-MT2-1.8B GGUF（腾讯；日语翻译） | Apache-2.0（repo 内 LICENSE.txt 为标准 Apache-2.0 全文，无额外限制） | huggingface.co/tencent/Hy-MT2-1.8B-GGUF |
| Vosk-TTS（代码；俄语语音） | Apache-2.0 | github.com/alphacep/vosk-tts |
| Vosk-TTS 俄语语音模型（vosk-model-tts-ru-0.7-multi，**可选、需主动启用**：默认绝不下载，只在执行 `tlwu-setup --vosk-tts-ru` 或自行下载时获取） | **不明：上游未标示模型许可**（只有 vosk-tts 代码 repo 为 Apache-2.0；alphacephei.com 与 repo 均找不到模型许可；上游 README 现列 0.9-multi）。视为未知，用户自负责任；没有此模型时，俄语发声会退回只显示字幕 | github.com/alphacep/vosk-tts、alphacephei.com/vosk/models |
| kokoro-onnx（英／日语语音） | MIT | github.com/thewh1teagle/kokoro-onnx |
| Kokoro 语音模型（Kokoro-82M，ONNX） | Apache-2.0（依 kokoro-onnx 说明） | github.com/thewh1teagle/kokoro-onnx |
| espeak-ng（包在 `espeakng-loader` 内，由 kokoro-onnx 带入；只在 Kokoro 独立 venv，不进主进程也不进 exe） | GPL-3.0-or-later | github.com/espeak-ng/espeak-ng |
| espeakng-loader（kokoro-onnx 带入） | MIT（它加载 espeak-ng 的 GPL 库） | github.com/thewh1teagle/espeakng-loader |
| phonemizer（kokoro-onnx 0.6.1 带入；仅 Kokoro venv） | GPL-3.0（PyPI 元数据） | github.com/bootphon/phonemizer |
| misaki（Kokoro 的 G2P，含日语） | Apache-2.0 | github.com/hexgrad/misaki |
| pyopenjtalk-plus、fugashi、jaconv、mojimoji、addict、regex、unidic-lite（misaki[ja] 附带，仅 Kokoro venv，由你自行安装） | 依次 MIT、MIT AND BSD-3-Clause、MIT、Apache-2.0、MIT、Apache-2.0 AND CNRI-Python、MIT（均取自 PyPI 元数据；unidic-lite 内 UniDic 词典数据未另行核查） | pypi.org/project/<包名> |
| GigaAM v3（俄语 ASR，仅手机版；k2-fsa/sherpa-onnx 导出的 ONNX，由你自行侧载） | MIT（GigaAM repo LICENSE 与 ai-sage/GigaAM-v3 模型卡；sherpa-onnx 导出说明指向同一份 GigaAM LICENSE） | github.com/salute-developers/GigaAM、huggingface.co/ai-sage/GigaAM-v3 |
| sherpa-onnx（仅手机版） | Apache-2.0 | github.com/k2-fsa/sherpa-onnx |
| llama.cpp（`mobile/` 内自行构建的 AAR） | MIT（依 mobile/README） | github.com/ggml-org/llama.cpp |
| BlackHole（macOS 虚拟音频设备） | GPL-3.0；非 GPL-3.0 项目须向 Existential Audio 另取许可 | github.com/ExistentialAudio/BlackHole。**由你自行安装（`mac_install.sh` 在你的机器上调用 Homebrew）；本项目不内置、不再分发。** |
| VB-CABLE（Windows 虚拟音频线） | 捐赠制（donationware）、专有软件（VB-Audio）；再分发须向 VB-Audio 取得许可 | vb-audio.com/Cable。**由你自行安装，本项目不内置、不再分发。** |

再分发模型前请自行确认上游许可证。

表格说明：（1）本 repo 与 Windows exe 均未内置任何 GPL 组件（exe 不含反向模式，所以没有 Kokoro、espeak-ng、phonemizer）。GPL 的部分（espeak-ng、phonemizer，由 kokoro-onnx 带入）只由你自己用 pip 装进独立的 Kokoro venv，由独立 worker 进程使用，主程序从不导入。（2）`tlwu-setup` 内置和下载的东西没有非商用许可；文中唯一的非商用模型 NLLB-200（CC-BY-NC）未随附。（3）Vosk-TTS 俄语语音模型上游未标示许可，请视为不明。本项目为非商用研究项目；你下载的一切内容的许可由你自行负责。

## 基准测试

用 `scripts/bench/`（开发工具，不属于产品）测量翻译质量与速度。

- 硬件：RTX 3050 8 GB。数据：FLORES-200 devtest（1012 句，俄文 -> 繁体中文，使用原文文本；50 句的行为前 50 句）。指标：chrF（sacrebleu）与 COMET（Unbabel/wmt22-comet-da）。延迟不含模型加载。Qwen 引擎为 Q4_K_M GGUF、2048 context。

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

另用 FLEURS 200 句语音（ASR large-v3-turbo -> 翻译）得到相同排序（opus 22.7、qwen3-8b 24.9、qwen3-4b 22.3、Google 28.5 chrF）。

复现方法（需要 bench venv、`%USERPROFILE%/tlwu-eval` 下的数据；Google 列需要 `google_key.txt` 中的 Cloud Translation 密钥，可加 `--no-google` 跳过）：

```
.venv-bench\Scripts\python scripts/bench/run.py --full --engines m2m,qwen3-8b,qwen3-8b+ctx,madlad3b,qwen3-4b,qwen25-7b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair en-zh --engines qwen3-8b,qwen3-4b
.venv-bench\Scripts\python scripts/bench/run.py --smoke --pair ru-en --engines qwen3-8b,qwen3-4b
```

**仅限 bench；会把测试句子发送到该服务。** `scripts/bench/run.py` 与 `google_ref.py` 只有在存在密钥文件且未加 `--no-google` 时，才会调用 Google Cloud Translation v2（FLEURS 语音测试另会调用 Speech-to-Text v1，测试音频会被发送）。这些是开发工具：`tinglongwu` 包从不导入 `scripts/`，常规的 `tlwu`／`tlwu-gui`／反向模式路径也不会调用它们。`fetch_data.py` 另会从网络下载测试数据集（FLORES-200、FLEURS）。

**Google 在所有测试方向的分数都高于所有离线引擎。** 离线引擎以部分质量换取无需联网、数据不离开本机。50 句样本仅供参考。

## 已知限制
- **延迟**约为段落长度加处理时间：停顿（VAD）后或达到 `--chunk` 秒才出字。改用 `--preset chat`（或减小 `--chunk`）可更快，但会有更多半句。
- **英文中转**会损失术语和语气（两次有损转换），人名、性别、敬语较差。
- **Whisper 在静音、音乐、噪声时会幻听**；用静音闸门、no-speech／logprob 阈值和 `condition_on_previous_text=False` 缓解，无法根除。
- **自动语言检测**在第一个识别段落后锁定；混合语言流请用 `--lang`。
- **CPU 速度与模型大小**：CPU（int8）上 `medium`／`large-v3` 可能跟不上实时；有 NVIDIA GPU 请用 `--device cuda`。
- **macOS** 需要虚拟回环设备（没有 ScreenCaptureKit 辅助程序就没有原生系统音频采集）。
- **繁体中文**是用 OpenCC 对简体模型输出做事后转换，用词仅近似。
- 实时采集后端已实现，但自动化测试只覆盖 `--file`（无音频硬件）。

## Linux 反向模式（实验性、未测试）

> 未测试：在没有 Linux 的 Windows 机器上编写，只有命令组装有单元测试，可能有粗糙之处。

在无 NVIDIA GPU 的 Linux 笔记本（如 AMD Ryzen 7040）上运行反向模式（你说中文、俄语语音送进 Meet/Zoom）：

```
sudo apt install pulseaudio-utils            # pactl（PipeWire 已由 pipewire-pulse 提供）
tlwu-setup --whisper small --pair zh-en --pair en-ru --pair ru-en --pair en-zh   # 一次性，需要网络
python -m tinglongwu.reverse.proto --list-mics
python -m tinglongwu.reverse.proto --mic "<麦克风名称子串>" --compute cpu --mt opus     --tts-engine vosk --tts-python /path/to/tts-vosk/bin/python --tts-model /path/to/vosk-model-tts-ru-0.7-multi
```

- Linux 上程序会自动创建虚拟麦克风（`pactl load-module module-null-sink sink_name=tlwu_sink` 加 `module-remap-source ... source_name=tlwu_mic`），把俄语语音播放到 `tlwu_sink`，退出时卸载两个模块。在 Meet/Zoom 选择麦克风 `tlwu_mic`。`--no-vmic` 可关闭（改用 `--device <sink 子串>`）；`--list-speakers` 列出输出设备。Windows 仍使用 VB-CABLE。
- 没有 CUDA 时，翻译改用 OPUS/Argos 枢纽 zh -> en -> ru（需上面四个 `--pair` 模型），Whisper 默认 `small`。`--mt qwen` 可强制使用 Qwen3（GPU）。
- 俄语语音模型为**可选、需主动启用**（上游未标示许可）：任何安装程序或默认设置都不会下载。请执行 `tlwu-setup --vosk-tts-ru`（或在菜单回答 y/N，默认 N，会先显示许可未标示的提示）。没有模型时，说俄语只会显示字幕并提示启用方式。
- 语音在 CPU 上使用内置固定语音：俄语用 Vosk-TTS（`--tts-engine vosk`，用 `--speaker` 选 0-4 号语音），英／日语用 Kokoro（`--tts-engine kokoro`），各自在独立 venv。没有参考音频的声音克隆功能。

## 合理使用／同意声明
- 录音、采集或转写通话或会议前，须取得所有参与者的同意；各地对录音和转写的法规不同。
- 不得用本工具规避 DRM 或其他对你无权复制之内容的技术保护措施。
- 不得用来冒充他人。反向模式只使用内置固定语音，没有声音克隆功能；也不得把合成语音当作他人的声音。

## 路线图
麦克风 -> 中文 -> 俄文翻译 -> 内置语音离线 TTS（Vosk-TTS／Kokoro） -> 虚拟麦克风（VB-Cable／BlackHole／PipeWire null-sink），并做回声排除，避免合成语音被重新采集。

## 测试
`pip install -r requirements-dev.txt && pytest`（不需要模型和网络）。

## 免责声明
- **现状提供。** 本软件依 MIT 许可“按现状”提供，不附任何形式的担保，作者不对任何损害负责（见 [LICENSE](LICENSE)）。
- **研究／教学演示。** 这是技术演示，不是经过认证或商业化的产品。
- **输出可能有误。** 转写和翻译可能出错，请勿据以做法律、医疗、安全或财务决定。
- **你的责任。** 你必须遵守适用于你的法律，并在录音或转写他人前取得同意；见[合理使用／同意声明](#合理使用同意声明)。
- **第三方组件。** 模型、库和驱动程序各自适用其许可证，由你从上游来源自行安装；本 repo 不再分发它们（唯一提交的第三方二进制文件是 `mobile/` 内自行构建的 llama.cpp Android AAR（MIT）；Windows exe 内含的运行时库列于其 `licenses/THIRD_PARTY_NOTICES.txt`）。见[许可证与来源](#许可证与来源)。
- **无从属关系。** 与 Google、Microsoft、Apple、Zoom、Meet、阿里巴巴（Qwen）、腾讯（Hunyuan／Hy-MT2）、Meta、OpenAI、NVIDIA、VB-Audio、Existential Audio 或文中提到的其他第三方均无关联，亦未获其背书。所有商标归各自所有人。
- **网络防护。** 网络防护是尽力而为，不保证没有任何数据离开你的机器。
- **安全。** 见 [SECURITY.md](SECURITY.md)。

MIT 许可，见 [LICENSE](LICENSE)。(c) 2026 Alonso。
