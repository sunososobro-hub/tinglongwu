import getpass
import os
from urllib.parse import parse_qs, urlparse

from tinglongwu import cli, report


def test_url_encoding_and_length_cap():
    logs = [f"line {i} " + "é中 " * 40 for i in range(60)]
    url = report.build_url(info={"Engine": "opus"}, log_lines=logs)
    assert len(url) <= report.MAX_URL
    q = parse_qs(urlparse(url).query)
    assert q["title"] == ["[bug] "]
    assert "Engine: opus" in q["body"][0]
    assert "line 59" in q["body"][0]  # newest lines are kept, oldest dropped


def test_redaction(monkeypatch):
    monkeypatch.setattr(getpass, "getuser", lambda: "alice")
    home = os.path.expanduser("~")
    out = report.redact(f"open {home}/x and C:\\Users\\Bob\\f and /home/carol/y by alice")
    assert "alice" not in out and "Bob" not in out and "carol" not in out
    assert "<user>" in out


def test_cli_report_opens_browser(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    (tmp_path / "a.log").write_text("hello log\n", "utf-8")
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: seen.append(u) or True)
    assert cli.main(["--report", "--lang", "ru"]) == 0
    assert len(seen) == 1 and seen[0].startswith(report.ISSUE_URL)
    assert "ru" in parse_qs(urlparse(seen[0]).query)["body"][0]
    assert "hello log" in parse_qs(urlparse(seen[0]).query)["body"][0]


def test_report_includes_environment_info(monkeypatch, tmp_path):
    from tinglongwu import hwprobe

    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(hwprobe, "gpu_info", lambda *a, **k: ("GeForce GTX 1050", 2048))
    seen = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: seen.append(u) or True)
    report.open_report(info={"Engine": "opus"})
    body = parse_qs(urlparse(seen[0]).query)["body"][0]
    for key in ("CPU:", "RAM MiB:", "GPU: GeForce GTX 1050 (2048 MiB)", "NVIDIA driver:",
                "ctranslate2 CUDA devices:", "Packages:", "Engine: opus"):
        assert key in body


def test_pkg_versions_reads_dist_info_without_importing(monkeypatch, tmp_path):
    import sys

    (tmp_path / "ctranslate2-4.5.0.dist-info").mkdir()
    (tmp_path / "faster_whisper-1.1.0.dist-info").mkdir()
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    out = report._pkg_versions()
    assert "ctranslate2 4.5.0" in out and "faster-whisper 1.1.0" in out
    assert "llama-cpp-python not installed" in out


def test_windows_cpu_features_come_from_the_os():
    """#91: on Windows (the target OS) AVX2/AVX-512 come from IsProcessorFeaturePresent(40/41), not 'unknown'."""
    asked = []

    def present(feature):
        asked.append(feature)
        return 1 if feature == 40 else 0

    model, feats = report._win_cpu(present=present, name=lambda: " Intel(R) Core(TM) i5-9300H CPU @ 2.40GHz ")
    assert model == "Intel(R) Core(TM) i5-9300H CPU @ 2.40GHz" and feats == ["AVX2"] and asked == [40, 41]
    out = report._cpu_features("win32", win=lambda: (model, feats))
    assert out == "Intel(R) Core(TM) i5-9300H CPU @ 2.40GHz [AVX2]"
    assert report._win_cpu(present=lambda f: 1, name=lambda: "X")[1] == ["AVX2", "AVX-512"]
    assert report._cpu_features("win32", win=lambda: ("Old CPU", [])) == "Old CPU [no AVX2, no AVX-512]"

    def broken(feature):
        raise OSError("no kernel32")

    assert report._win_cpu(present=broken, name=broken) == ("", None)  # never a crash, never a wrong "absent"
    assert report._cpu_features("win32", win=lambda: ("", None)).endswith("[AVX2/AVX-512 unknown]")


def test_linux_cpu_features_from_cpuinfo(tmp_path):
    f = tmp_path / "cpuinfo"
    f.write_text("model name\t: AMD Ryzen 5 5600\nflags\t\t: fpu sse2 avx avx2 fma\n", "utf-8")
    assert report._cpu_features("linux", cpuinfo=f) == "AMD Ryzen 5 5600 [AVX2]"
    assert report._cpu_features("linux", cpuinfo=tmp_path / "missing").endswith("[AVX2/AVX-512 unknown]")


def test_report_has_install_selftest_block_and_keeps_run_log_separate(monkeypatch, tmp_path):
    """#91: install.log self-test result is in the report; install.log is never mistaken for the run log."""
    monkeypatch.setenv("TLWU_LOG_DIR", str(tmp_path))
    run = tmp_path / "tlwu.log"
    run.write_text("run line A\n", "utf-8")
    os.utime(run, (1, 1))  # older than install.log
    (tmp_path / "install.log").write_text(
        "--- tlwu self-test 2026-11-01 10:00:00 ---\nOLD RUN FAIL\n"
        "--- tlwu self-test 2026-11-10 10:00:00 ---\nasr: OK 3.1s\nmt: FAIL 0.2s out of memory\n", "utf-8")
    assert report.last_log_lines(200) == ["run line A"]
    assert report.selftest_lines() == ["--- tlwu self-test 2026-11-10 10:00:00 ---", "asr: OK 3.1s",
                                       "mt: FAIL 0.2s out of memory"]
    seen = []
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", lambda u: seen.append(u) or True)
    report.open_report(info={"Engine": "opus"})
    body = parse_qs(urlparse(seen[0]).query)["body"][0]
    assert "Install self-test (install.log):" in body and "mt: FAIL 0.2s out of memory" in body
    assert "OLD RUN FAIL" not in body and "run line A" in body
    # a long run log is cut from the oldest line; the self-test block survives
    url = report.build_url(log_lines=[f"line {i} " + "x" * 200 for i in range(200)], selftest=report.selftest_lines())
    assert len(url) <= report.MAX_URL and "mt: FAIL" in parse_qs(urlparse(url).query)["body"][0]
    (tmp_path / "install.log").unlink()
    assert report.selftest_lines() == [] and "Install self-test" not in report.build_body({}, ["x"])


def test_gui_report_info_lists_choices_but_no_paths():
    from tinglongwu.gui import core

    s = dict(core.DEFAULTS, lang="ja", model="base", source="mic", glossary="/home/bob/secret.tsv",
             save_path="/home/bob/out.txt", live_draft=False)
    info = core.report_info(s)
    assert info["Language pair"] == "ja -> zh-tw" and info["Model"] == "base" and info["Source"] == "mic"
    assert info["Engine"] == "auto" and info["Mode"] == "gui/meeting" and info["Live draft"] == "off"
    assert "secret" not in str(info) and "out.txt" not in str(info)
