#!/usr/bin/env python3
"""Google Cloud reference column for the benchmarks (dev tool; NOT used by the product, which never imports scripts/).

  python scripts/bench/google_ref.py mt  IN.txt OUT.txt [--src ru --to zh-TW]       Cloud Translation v2, one line per line
  python scripts/bench/google_ref.py stt DATA_DIR FLEURS_OUT_DIR OUT.txt             Speech-to-Text v1 on the FLEURS subset

The API key is read from the file given by --key-file (default %USERPROFILE%\\tlwu-eval\\google_key.txt; base dir overridable via env TLWU_EVAL_DIR) or env GOOGLE_API_KEY_FILE,
sent only in the X-Goog-Api-Key header, and never printed or written. Errors print status + API message only.
Needs network (it is a benchmark tool, outside the offline runtime). stdlib only (soundfile for stt).
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_KEY_FILE = str(Path(os.environ.get("TLWU_EVAL_DIR") or (Path.home() / "tlwu-eval")) / "google_key.txt")


def _key(path: str) -> str:
    return Path(path).read_text("utf-8").strip()


def _post(url: str, key: str, payload: dict, retries: int = 3) -> dict:
    data = json.dumps(payload).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json", "X-Goog-Api-Key": key, "User-Agent": "tlwu-bench/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:  # never echo the request (headers/URL) - only status + message
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"Google API error {e.code}: {body}")
        except urllib.error.URLError as e:
            raise SystemExit(f"network error: {e.reason}")
    raise SystemExit("unreachable")


def translate(lines: list[str], key: str, src: str, tgt: str) -> list[str]:
    out = []
    for i in range(0, len(lines), 100):
        chunk = lines[i:i + 100]
        res = _post("https://translation.googleapis.com/language/translate/v2", key,
                    {"q": chunk, "source": src, "target": tgt, "format": "text"})
        out += [html.unescape(t["translatedText"]).replace("\n", " ") for t in res["data"]["translations"]]
    return out


def recognize(wav: Path, key: str, lang: str = "ru-RU") -> str:
    import soundfile as sf  # only needed for this mode (bench venv)

    import numpy as np

    x, sr = sf.read(str(wav), dtype="float32")  # (sf int16 read of a float wav truncates to silence)
    pcm = (np.clip(x, -1, 1) * 32767).astype("int16")  # raw 16-bit PCM, header-independent
    res = _post("https://speech.googleapis.com/v1/speech:recognize", key,
                {"config": {"encoding": "LINEAR16", "sampleRateHertz": sr, "languageCode": lang,
                            "enableAutomaticPunctuation": True},
                 "audio": {"content": base64.b64encode(pcm.tobytes()).decode()}})
    return " ".join(r["alternatives"][0]["transcript"] for r in res.get("results", []) if r.get("alternatives"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["mt", "stt"])
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("c", nargs="?")
    ap.add_argument("--src", default="ru")
    ap.add_argument("--to", default="zh-TW")
    ap.add_argument("--key-file", default=os.environ.get("GOOGLE_API_KEY_FILE", DEFAULT_KEY_FILE))
    a = ap.parse_args(argv)
    key = _key(a.key_file)
    if a.mode == "mt":
        lines = Path(a.a).read_text("utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        res = translate(lines, key, a.src, a.to)
        assert len(res) == len(lines)
        Path(a.b).write_text("\n".join(res) + "\n", "utf-8")
        print(f"translated {len(lines)} lines, {sum(map(len, lines))} chars")
    else:
        data, fdir, outp = Path(a.a), Path(a.b), Path(a.c)
        gold = (fdir / "fleurs.gold_ru.txt").read_text("utf-8").split("\n")[:-1]
        names = {}
        for line in (data / "fleurs_ru" / "test.tsv").read_text("utf-8").splitlines():
            c = line.split("\t")
            names.setdefault(c[2].replace("\n", " "), c[1])
        index = {p.name: p for p in (data / "fleurs_ru" / "audio").rglob("*.wav")}
        hyps = [recognize(index[names[g]], key) for g in gold]
        outp.write_text("\n".join(h.replace("\n", " ") for h in hyps) + "\n", "utf-8")
        print(f"recognised {len(hyps)} utterances")
    return 0


if __name__ == "__main__":
    sys.exit(main())
