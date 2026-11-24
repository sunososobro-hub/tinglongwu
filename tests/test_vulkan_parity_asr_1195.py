"""#1195 ASR half: the CPU-vs-Vulkan Whisper parity check passes only for a real whisper.cpp Vulkan run whose CER is
within +0.02 of the CPU run (behaviour tests with fake recognizers and a tiny manifest)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import vulkan_parity as vp  # noqa: E402

REFS = {"en_001": "the meeting will start at nine tomorrow morning",
        "en_002": "please send me the report before the end of the week"}


def _result(backend, hyps, device_log=""):
    return {"backend": backend, "device_log": device_log, "seconds": 2.0, "audio_seconds": 10.0,
            "clips": [{"id": k, "lang": "en", "ref": REFS[k], "hyp": h} for k, h in zip(REFS, hyps)]}


CPU = _result("cpu", list(REFS.values()))


def test_matching_vulkan_transcript_passes():
    ok, md = vp.compare_asr(CPU, _result("vulkan", list(REFS.values()), "vulkan"))
    assert ok, md
    assert "| vulkan | vulkan | vulkan | 0.0000 |" in md


def test_app_fallback_to_cpu_fails():
    ok, md = vp.compare_asr(CPU, _result("cpu", list(REFS.values())))
    assert not ok and "silent fallback" in md


def test_whisper_cpp_internal_cpu_fallback_fails():
    ok, md = vp.compare_asr(CPU, _result("vulkan", list(REFS.values()), "cpu"))
    assert not ok and "CPU fallback" in md


def test_missing_or_unknown_device_log_fails():
    for log in ("", None):
        ok, md = vp.compare_asr(CPU, _result("vulkan", list(REFS.values()), log))
        assert not ok and "no Vulkan device" in md, log
    no_key = _result("vulkan", list(REFS.values()), "vulkan")
    del no_key["device_log"]
    assert not vp.compare_asr(CPU, no_key)[0]


def test_same_ids_with_different_references_fail():
    other = _result("vulkan", ["cat", "dog"], "vulkan")
    for clip, ref in zip(other["clips"], ("cat", "dog")):
        clip["ref"] = ref
    ok, md = vp.compare_asr(CPU, other)
    assert not ok and "(id, lang, ref)" in md


def test_cer_bound_is_plus_0_02():
    # 1 wrong char out of 85 reference chars = 0.0118 -> inside the bound; a garbled clip is far outside
    near = ["the meeting will start at nine tomorrow mornin", REFS["en_002"]]
    assert vp.compare_asr(CPU, _result("vulkan", near, "vulkan"))[0]
    far = ["the meting wil stat at nne tomorow", REFS["en_002"]]
    ok, md = vp.compare_asr(CPU, _result("vulkan", far, "vulkan"))
    assert not ok and "CER(vulkan)" in md


def test_bound_is_relative_to_the_cpu_run():
    worse_cpu = _result("cpu", ["the meting wil stat at nne tomorow", REFS["en_002"]])
    same = _result("vulkan", ["the meting wil stat at nne tomorow", REFS["en_002"]], "vulkan")
    assert vp.compare_asr(worse_cpu, same)[0]


def test_mismatched_or_empty_clip_lists_fail():
    assert not vp.compare_asr(CPU, {**_result("vulkan", [], "vulkan"), "clips": []})[0]
    one = _result("vulkan", list(REFS.values()), "vulkan")
    one["clips"] = one["clips"][:1]
    assert not vp.compare_asr(CPU, one)[0]


def test_whisper_log_device_reads_whisper_cpp_init_lines():
    assert vp.whisper_log_device("whisper_backend_init_gpu: using Vulkan0 backend\n") == "vulkan"
    assert vp.whisper_log_device("whisper_backend_init_gpu: no GPU found\n") == "cpu"
    assert vp.whisper_log_device("whisper_backend_init: using CPU backend\n") == "cpu"
    assert vp.whisper_log_device("") == ""


class _Rec:
    def __init__(self, texts):
        self.texts, self.warm = list(texts), 0

    def warm_up(self):
        self.warm += 1
        os.write(2, b"whisper_backend_init_gpu: using Vulkan0 backend\n")  # native code logs to fd 2

    def transcribe(self, audio):
        return type("T", (), {"text": " " + self.texts.pop(0) + " "})()


def _manifest(tmp_path):
    rows = [{"id": k, "lang": "en", "wav": str(tmp_path / f"{k}.wav"), "ref_text": v} for k, v in REFS.items()]
    rows.append({"id": "ru_001", "lang": "ru", "wav": str(tmp_path / "ru.wav"), "ref_text": "привет"})
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")
    return tmp_path


def test_transcribe_records_backend_device_and_only_the_asked_languages(tmp_path):
    rec, calls = _Rec(REFS.values()), []

    def select(models_dir, size, lang):
        calls.append((models_dir, size, lang))
        return rec, "vulkan"

    res = vp.transcribe("M", str(_manifest(tmp_path)), ("en",), "small", select=select,
                        read=lambda p: (np.zeros(16000, np.float32), 5.0))
    assert calls == [(Path("M"), "small", "en")] and rec.warm == 1
    assert isinstance(calls[0][0], Path)  # the CPU recognizer does models_dir / "whisper-small"
    assert res["backend"] == "vulkan" and res["device_log"] == "vulkan"
    assert [c["hyp"] for c in res["clips"]] == list(REFS.values()) and res["audio_seconds"] == 10.0
    ok, _ = vp.compare_asr(CPU, res)
    assert ok


def test_transcribe_cpu_path_skips_warm_up_and_reports_cpu(tmp_path):
    rec = _Rec(REFS.values())
    res = vp.transcribe("M", str(_manifest(tmp_path)), ("en",), select=lambda *a: (rec, "cpu"),
                        read=lambda p: (np.zeros(160, np.float32), 1.0))
    assert res["backend"] == "cpu" and rec.warm == 0 and len(res["clips"]) == 2


def test_cli_compare_asr_exit_codes(tmp_path):
    a, b, c = tmp_path / "c.json", tmp_path / "v.json", tmp_path / "f.json"
    a.write_text(json.dumps(CPU), "utf-8")
    b.write_text(json.dumps(_result("vulkan", list(REFS.values()), "vulkan")), "utf-8")
    c.write_text(json.dumps(_result("cpu", list(REFS.values()))), "utf-8")
    script = str(Path(vp.__file__))
    run = lambda x, y: subprocess.run([sys.executable, script, "compare-asr", str(x), str(y)],  # noqa: E731
                                      capture_output=True).returncode
    assert run(a, b) == 0
    assert run(a, c) == 1


def test_one_language_falling_back_taints_the_run(tmp_path):
    rows = [{"id": "en_1", "lang": "en", "wav": "a.wav", "ref_text": "hello"},
            {"id": "ru_1", "lang": "ru", "wav": "b.wav", "ref_text": "привет"}]
    (tmp_path / "manifest.jsonl").write_text("\n".join(json.dumps(r) for r in rows), "utf-8")

    class Rec(_Rec):
        def __init__(self, line):
            super().__init__(["x"])
            self.line = line

        def warm_up(self):
            os.write(2, self.line)

    recs = {"en": Rec(b"whisper_backend_init_gpu: using Vulkan0 backend\n"),
            "ru": Rec(b"whisper_backend_init_gpu: no GPU found\n")}
    res = vp.transcribe("M", str(tmp_path), ("en", "ru"), select=lambda m, s, lang: (recs[lang], "vulkan"),
                        read=lambda p: (np.zeros(160, np.float32), 1.0))
    assert res["devices"] == {"en": "vulkan", "ru": "cpu"} and res["device_log"] == "cpu"
