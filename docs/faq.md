# FAQ

## How is this different from Chrome's built-in captions or browser extensions?

Browser-based captions and extensions work inside a browser tab and usually rely on an online service. tinglongWu runs as a separate program on your computer, so it can caption audio from any app, with the models running locally.

| | Browser captions / extensions (typical) | tinglongWu |
|---|---|---|
| Works in any app (Meet, Zoom, Discord, TV capture, games) | Browser tab only | Yes, captures system audio |
| Fully offline at runtime | Usually needs a connection | Yes, after install and model download |
| No account | Often required | Yes |
| Audio stays on your machine | Often sent to a cloud service | Yes, processed locally |
| Speak-back voice | Not typical | Yes, speak mode |
| Languages | Varies by product | See the [language table](details.md#languages) |

Categories only: check any specific product's current behaviour yourself.

## What is the latency? Does the subtitle appear after the speech, or is it preloaded?

It is live recognition, not preloaded: tinglongWu never sees the audio before it is spoken. A subtitle appears after the speaker makes a short pause, or when a segment reaches the chunk limit while the speaker keeps going, and then speech recognition and translation run on that segment. So the line always comes a little after the speech.

The preset sets the trade-off:

| Preset | Chunk limit / max wait | What you get |
|---|---|---|
| `meeting` (default) | 8 s / 6 s | Whole sentences and better translations, but a line can wait longer for its sentence to finish |
| `chat` | 4 s / 1.5 s | Lines come sooner, but more of them are sentence fragments |

With `--stream`, a dim draft line is shown while the speaker is still talking and is replaced by the final line. Measured numbers per hardware tier, with p50 / p90 and the method, are on the [latency page](latency.md).

## Why Whisper + Hy-MT2 instead of an end-to-end model such as Qwen3-ASR?

Qwen3-ASR is speech recognition only, with no translation mode, so it would still need Hy-MT2 after it. On the owner's RTX 3050 8 GB (en and ja to zh-TW, n=40 each) the 1.7B model raised chrF by +2.1 on en but only +0.1 on ja, was about 4x slower (p50 about 3 s instead of under 1 s), and needed about 6.5-7 GB VRAM with Hy-MT2 instead of about 3 GB. It also needs a separate PyTorch runtime. So the default stays Whisper + Hy-MT2. Numbers and method: [Qwen3-ASR research](qwen3-asr.md).
