#!/usr/bin/env python3
"""#1158 (Chrome extension part 1, parent #711): browser-size ASR + MT candidates, measured in headless Chromium.

Dev tool, NOT part of the offline runtime. It serves extension/spike/ (transformers.js + ONNX Runtime Web from
node_modules, WASM backend = CPU only), loads each candidate model in the page, runs the CI smoke clips
(eval/ci_smoke.json -> eval_data/smoke, the same 20 FLEURS clips as the eval gate) through ASR then MT and
times both inside the page. The extension will run the same way in its offscreen document.

  cd extension/spike && npm ci && cd -
  python scripts/bench/extension_spike.py --data eval_data/smoke --asr whisper-base --out spike-base.json
  python scripts/bench/extension_spike.py --merge spike-*.json --md spike.md --json spike.json

Per utterance latency = ASR seconds + MT seconds for the whole clip (end of speech -> subtitle, no streaming).
MT output into zh is converted to Taiwan Traditional with the app's zh_convert "tw", like the eval gate.
The table puts the desktop pipeline's chrF (eval/baseline.json: Whisper small + Hy-MT2 1.8B, same 20 clips)
next to each pair, and names the best combination whose median latency is <= the #711 budget (8 s).
Workflow: .github/workflows/extension-spike.yml (GitHub-hosted runner; the agent sandbox cannot reach the hub).
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import statistics
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPIKE_DIR = ROOT / "extension" / "spike"
sys.path.insert(0, str(ROOT))
from tlwu_eval import scoring  # noqa: E402  (pure Python, no deps)

BUDGET_S = 8.0  # #711 Acceptance: median latency <= 8 s on a laptop without GPU
SR = 16000

# name -> (hub id, dtype). q8 everywhere: the size a user downloads once into the browser cache.
ASR = {
    "whisper-tiny": ("onnx-community/whisper-tiny", "q8"),
    "whisper-base": ("onnx-community/whisper-base", "q8"),
    "whisper-small": ("onnx-community/whisper-small", "q8"),
}
# MT candidate -> pair -> chain of (hub id, src_lang, tgt_lang, prefix). A chain of 2 = pivot through English.
_OPUS_EN_ZH = ("Xenova/opus-mt-en-zh", None, None, ">>cmn_Hans<< ")  # multi-target model: needs the target token
_NLLB = "Xenova/nllb-200-distilled-600M"
_NLLB_CODE = {"en": "eng_Latn", "ja": "jpn_Jpan", "ru": "rus_Cyrl", "zh": "zho_Hans"}
MT = {
    "opus-mt": {
        "en->zh": [_OPUS_EN_ZH],
        "ja->zh": [("Xenova/opus-mt-ja-en", None, None, ""), _OPUS_EN_ZH],
        "ru->zh": [("Xenova/opus-mt-ru-en", None, None, ""), _OPUS_EN_ZH],
        "zh->en": [("Xenova/opus-mt-zh-en", None, None, "")],
    },
    "nllb-600m": {
        p: [(_NLLB, _NLLB_CODE[p[:2]], _NLLB_CODE[p[-2:]], "")] for p in ("en->zh", "ja->zh", "ru->zh", "zh->en")
    },
}
MT_DTYPE = "q8"


def pair_of(row: dict) -> str:
    return "zh->en" if row["lang"] == "zh" else f"{row['lang']}->zh"


def ref_of(row: dict) -> str:
    return row["ref_en"] if row["lang"] == "zh" else row["ref_zh"]


# ---------------------------------------------------------------- summary (pure, unit-tested)
def summarize(report: dict, baseline: dict | None = None) -> list[dict]:
    """One row per (ASR, MT) combination: size, load times, latency median/p90, chrF per pair, ASR error per lang."""
    base_pairs = (baseline or {}).get("pairs", {})
    out = []
    for asr_name, asr_info in report["asr"].items():
        clips = [c for c in report["clips"] if c["asr"] == asr_name]
        if not clips or asr_info.get("error"):
            continue
        asr_err = {}
        for lang in sorted({c["lang"] for c in clips}):
            pairs = [(c["ref_text"], c["asr_text"]) for c in clips if c["lang"] == lang]
            asr_err[lang] = round(scoring.error_rate(pairs, lang), 3)
        # merged matrix reports keep each ASR job's own MT loads (status, size, load times)
        for mt_name, mt_info in report.get("mt_by_asr", {}).get(asr_name, report["mt"]).items():
            if mt_info.get("error") or any(mt_name not in c["mt"] for c in clips):
                continue
            lat = [c["asr_s"] + c["mt"][mt_name]["s"] for c in clips]
            chrf = {}
            for pair in sorted({pair_of(c) for c in clips}):
                pp = [(ref_of(c), c["mt"][mt_name]["text"]) for c in clips if pair_of(c) == pair]
                chrf[pair] = {"chrf": round(scoring.chrf(pp), 2), "desktop": base_pairs.get(pair, {}).get("chrf")}
            out.append({
                "asr": asr_name, "mt": mt_name,
                "download_mb": round((asr_info["bytes"] + mt_info["bytes"]) / 1e6, 1),
                "cold_load_s": round(asr_info["cold_s"] + mt_info["cold_s"], 1),
                "warm_load_s": round(asr_info["warm_s"] + mt_info["warm_s"], 1),
                "latency_median_s": round(statistics.median(lat), 2),
                "latency_p90_s": round(scoring.percentile(lat, 90), 2),
                "asr_median_s": round(statistics.median(c["asr_s"] for c in clips), 2),
                "mt_median_s": round(statistics.median(c["mt"][mt_name]["s"] for c in clips), 2),
                "chrf": chrf,
                "mean_chrf": round(statistics.mean(v["chrf"] for v in chrf.values()), 2),
                "asr_error": asr_err,
                "clips": len(clips),
            })
    return out


def pick(rows: list[dict], budget_s: float = BUDGET_S) -> dict | None:
    """Best mean chrF among combinations with median latency <= budget; ties -> smaller download. None = none fits."""
    ok = [r for r in rows if r["latency_median_s"] <= budget_s]
    if not ok:
        return None
    return sorted(ok, key=lambda r: (-r["mean_chrf"], r["download_mb"]))[0]


def to_markdown(rows: list[dict], env: dict, budget_s: float = BUDGET_S) -> str:
    pairs = sorted({p for r in rows for p in r["chrf"]})
    langs = sorted({lang for r in rows for lang in r["asr_error"]})
    head = (["ASR", "MT", "download MB", "load cold s", "load warm s", "latency median s", "p90 s", "ASR median s",
             "MT median s"] + [f"chrF {p} (desktop)" for p in pairs] + [f"ASR {'CER' if lang in scoring.CHAR_LANGS else 'WER'} {lang}" for lang in langs])
    lines = [f"Runner: {env.get('runner', '?')}, Chromium {env.get('browser', '?')}, WASM threads "
             f"{env.get('threads', '?')}, crossOriginIsolated {env.get('isolated', '?')}, WebGPU in page "
             f"{env.get('webgpu', '?')}, clips {env.get('clips', '?')}.", "",
             "| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for r in sorted(rows, key=lambda r: (r["latency_median_s"], -r["mean_chrf"])):
        cells = [r["asr"], r["mt"], r["download_mb"], r["cold_load_s"], r["warm_load_s"], r["latency_median_s"],
                 r["latency_p90_s"], r["asr_median_s"], r["mt_median_s"]]
        for p in pairs:
            c = r["chrf"].get(p)
            cells.append("-" if c is None else f"{c['chrf']} ({c['desktop'] if c['desktop'] is not None else '-'})")
        cells += [r["asr_error"].get(lang, "-") for lang in langs]
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    best = pick(rows, budget_s)
    lines.append("")
    if not rows:
        lines.append("Choice: none - no ASR + MT combination loaded and ran (see the run log).")
    elif best:
        lines.append(f"Choice: ASR **{best['asr']}** ({ASR[best['asr']][0]}) + MT **{best['mt']}** - median "
                     f"{best['latency_median_s']} s (p90 {best['latency_p90_s']} s) <= {budget_s:g} s, mean chrF "
                     f"{best['mean_chrf']}, {best['download_mb']} MB download.")
    else:
        lines.append(f"Choice: no candidate meets the median latency budget of {budget_s:g} s on this runner.")
    return "\n".join(lines) + "\n"


def merge(reports: list[dict]) -> dict:
    """Matrix jobs each run one ASR model with every MT candidate; join them into one report."""
    out = {"env": reports[0]["env"], "asr": {}, "mt": {}, "mt_by_asr": {}, "clips": []}
    for r in reports:
        out["asr"].update(r["asr"])
        for asr_name in r["asr"]:
            out["mt_by_asr"][asr_name] = r.get("mt_by_asr", {}).get(asr_name, r["mt"])
        for k, v in r["mt"].items():
            if k not in out["mt"] or out["mt"][k].get("error"):
                out["mt"][k] = v
        out["clips"] += r["clips"]
    out["env"]["clips"] = len({c["id"] for c in out["clips"]})
    return out


# ---------------------------------------------------------------- browser run (GitHub runner)
class _Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm"}

    def end_headers(self):
        # cross-origin isolation -> SharedArrayBuffer -> multi-threaded WASM (an extension page gets the same via
        # manifest cross_origin_embedder_policy / cross_origin_opener_policy)
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def log_message(self, *a):
        pass


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(directory)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/spike.html"


def read_clip(path: Path) -> str:
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SR:
        n = int(len(audio) * SR / sr)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")
    return base64.b64encode(audio.astype("<f4").tobytes()).decode()


def run(data: Path, asr_names: list[str], mt_names: list[str], limit: int) -> dict:
    from playwright.sync_api import sync_playwright

    from tinglongwu.translate import zh_convert

    rows = [json.loads(x) for x in (data / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if limit:
        by_lang: dict[str, list] = {}
        for r in rows:
            by_lang.setdefault(r["lang"], []).append(r)
        rows = [r for v in by_lang.values() for r in v[:limit]]
    srv, url = serve(SPIKE_DIR)
    report: dict = {"asr": {}, "mt": {}, "clips": []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on("console", lambda m: print("  [page]", m.text, flush=True) if m.type == "error" else None)
            page.goto(url)
            page.wait_for_function("window.spike !== undefined", timeout=60_000)
            report["env"] = {"browser": browser.version, "threads": page.evaluate("spike.threads()"),
                             "isolated": page.evaluate("spike.crossOriginIsolated()"),
                             "webgpu": page.evaluate("!!navigator.gpu"), "clips": len(rows)}
            mt_keys = {}  # hub id -> loaded once for every candidate that uses it
            for mt in mt_names:
                info = {"bytes": 0, "cold_s": 0.0, "warm_s": 0.0, "models": []}
                try:
                    for chain in MT[mt].values():
                        for hub, *_ in chain:
                            if hub in info["models"]:
                                continue
                            info["models"].append(hub)
                            if hub not in mt_keys:
                                mt_keys[hub] = page.evaluate("([k, m, d]) => spike.loadMt(k, m, d)", [hub, hub, MT_DTYPE])
                                print(f"MT {hub}: {mt_keys[hub]}", flush=True)
                            for k in ("bytes", "cold_s", "warm_s"):
                                info[k] += mt_keys[hub][k]
                except Exception as e:  # a candidate that cannot load is reported, not fatal
                    info["error"] = str(e)[:300]
                    print(f"MT {mt} failed: {info['error']}", flush=True)
                report["mt"][mt] = info
            for name in asr_names:
                hub, dtype = ASR[name]
                try:
                    info = page.evaluate("([k, m, d]) => spike.loadAsr(k, m, d)", [name, hub, dtype])
                except Exception as e:
                    report["asr"][name] = {"model": hub, "error": str(e)[:300]}
                    print(f"ASR {name} failed: {e}", flush=True)
                    continue
                report["asr"][name] = {"model": hub, "dtype": dtype, **info}
                print(f"ASR {name}: {info}", flush=True)
                for r in rows:
                    a = page.evaluate("([k, b, l]) => spike.asr(k, b, l)", [name, read_clip(data / r["wav"]), r["lang"]])
                    clip = {"id": r["id"], "lang": r["lang"], "ref_text": r["ref_text"], "asr": name,
                            "asr_text": a["text"], "asr_s": a["seconds"], "mt": {}}
                    clip["ref_zh" if r["lang"] != "zh" else "ref_en"] = ref_of(r)
                    for mt in mt_names:
                        if report["mt"][mt].get("error"):
                            continue
                        text, secs = a["text"], 0.0
                        for hub, src, tgt, prefix in MT[mt][pair_of(r)]:
                            m = page.evaluate("([k, t, s, g]) => spike.mt(k, t, s, g)", [hub, prefix + text, src, tgt])
                            text, secs = m["text"], secs + m["seconds"]
                        if pair_of(r).endswith("zh"):
                            text = zh_convert(text, "tw")
                        clip["mt"][mt] = {"text": text, "s": secs}
                    report["clips"].append(clip)
                    print(f"{name} {r['id']}: asr {a['seconds']:.2f}s "
                          + " ".join(f"{k} {v['s']:.2f}s" for k, v in clip["mt"].items()), flush=True)
                page.evaluate("(k) => spike.unload(k)", name)
            browser.close()
    finally:
        srv.shutdown()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", type=Path, default=ROOT / "eval_data" / "smoke", help="folder with manifest.jsonl")
    ap.add_argument("--asr", default=",".join(ASR), help=f"comma list of {sorted(ASR)}")
    ap.add_argument("--mt", default=",".join(MT), help=f"comma list of {sorted(MT)}")
    ap.add_argument("--limit", type=int, default=0, help="first N clips per language (0 = all)")
    ap.add_argument("--runner", default="", help="runner description for the table header")
    ap.add_argument("--out", type=Path, help="raw report JSON (one run)")
    ap.add_argument("--merge", nargs="+", type=Path, help="raw report JSONs to join and summarize")
    ap.add_argument("--baseline", type=Path, default=ROOT / "eval" / "baseline.json")
    ap.add_argument("--md", type=Path, help="write the markdown table here")
    ap.add_argument("--json", type=Path, help="write the summary JSON here")
    a = ap.parse_args(argv)
    if a.merge:
        report = merge([json.loads(p.read_text(encoding="utf-8")) for p in a.merge])
    else:
        bad = [n for n in a.asr.split(",") if n not in ASR] + [n for n in a.mt.split(",") if n not in MT]
        if bad:
            ap.error(f"unknown candidate(s): {bad}")
        report = run(a.data, a.asr.split(","), a.mt.split(","), a.limit)
        report["env"]["runner"] = a.runner or "?"
        if a.out:
            a.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.runner:
        report["env"]["runner"] = a.runner
    baseline = json.loads(a.baseline.read_text(encoding="utf-8")) if a.baseline.exists() else None
    rows = summarize(report, baseline)
    md = to_markdown(rows, report["env"])
    print(md)
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    if a.json:
        best = pick(rows)
        a.json.write_text(json.dumps({"env": report["env"], "budget_s": BUDGET_S, "rows": rows,
                                      "choice": best and {"asr": best["asr"], "mt": best["mt"]}},
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
