// #1158 spike page: runs transformers.js ASR + MT in the browser on the WASM (CPU) backend and times it.
// The Python driver (scripts/bench/extension_spike.py) serves this folder and calls window.spike.* per clip.
// Everything the page runs is local (library + ORT wasm from node_modules), like an MV3 extension must be
// (no remote code); only the model files come from the Hugging Face hub, once, then from the browser cache.
import { env, pipeline } from "./node_modules/@huggingface/transformers/dist/transformers.min.js";

env.allowLocalModels = false;
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = new URL("./node_modules/onnxruntime-web/dist/", import.meta.url).href;
env.backends.onnx.wasm.numThreads = navigator.hardwareConcurrency || 4;

const WHISPER_LANG = { en: "english", ja: "japanese", ru: "russian", zh: "chinese" };
const loaded = new Map(); // key -> pipeline

function b64ToFloat32(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Float32Array(bytes.buffer);
}

async function load(key, task, model, opts) {
  const files = {};
  const t0 = performance.now();
  const p = await pipeline(task, model, {
    device: "wasm",
    ...opts,
    progress_callback: (e) => {
      if (e.file && e.total) files[e.file] = Math.max(files[e.file] || 0, e.total);
    },
  });
  const seconds = (performance.now() - t0) / 1000;
  loaded.set(key, p);
  const bytes = Object.values(files).reduce((a, b) => a + b, 0);
  return { seconds, bytes, files: Object.keys(files).length };
}

async function loadTwice(key, task, model, opts) {
  // cold = download + compile (first install); warm = from the browser cache (every later meeting)
  const cold = await load(key, task, model, opts);
  await loaded.get(key).dispose?.();
  const warm = await load(key, task, model, opts);
  return { cold_s: cold.seconds, warm_s: warm.seconds, bytes: cold.bytes, files: cold.files };
}

window.spike = {
  threads: () => env.backends.onnx.wasm.numThreads,
  crossOriginIsolated: () => self.crossOriginIsolated,
  loadAsr: (key, model, dtype) => loadTwice(key, "automatic-speech-recognition", model, { dtype }),
  loadMt: (key, model, dtype) => loadTwice(key, "translation", model, { dtype }),
  async asr(key, b64, lang) {
    const audio = b64ToFloat32(b64);
    const t0 = performance.now();
    const out = await loaded.get(key)(audio, { language: WHISPER_LANG[lang] || lang, task: "transcribe" });
    return { text: (out.text || "").trim(), seconds: (performance.now() - t0) / 1000 };
  },
  async mt(key, text, src_lang, tgt_lang) {
    const t0 = performance.now();
    const opts = { max_new_tokens: 256 };
    if (src_lang) Object.assign(opts, { src_lang, tgt_lang });
    const out = text.trim() ? await loaded.get(key)(text, opts) : [{ translation_text: "" }];
    return { text: (out[0].translation_text || "").trim(), seconds: (performance.now() - t0) / 1000 };
  },
  async unload(key) {
    await loaded.get(key)?.dispose?.();
    loaded.delete(key);
  },
};
document.getElementById("status").textContent = "ready";
