"""Static guard: the runtime package `tinglongwu` must contain no download/network code."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BANNED = ("requests", "urllib.request", "urllib3", "http.client", "httpx", "aiohttp", "ftplib",
          "smtplib", "socket", "huggingface_hub", "argostranslate", "tinglongwu_setup", "pip")
ALLOWED = {("tinglongwu/offline.py", "socket"),  # the guard itself
           ("tinglongwu/ollama.py", "http.client")}  # loopback-only Ollama client (see _endpoint)


def imports(path):
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{a.name}" for a in node.names)  # from urllib import request


def banned(name):
    return any(name == b or name.startswith(b + ".") for b in BANNED)


def test_tinglongwu_has_no_network_imports():
    bad = []
    for f in sorted((ROOT / "tinglongwu").rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        bad += [f"{rel}: {m}" for m in imports(f) if banned(m) and (rel, m) not in ALLOWED]
    assert not bad, bad


def test_scanner_catches_violations(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("import socket\nfrom urllib import request\nfrom huggingface_hub import snapshot_download\n")
    assert [m for m in imports(f) if banned(m)] == [
        "socket", "urllib.request", "huggingface_hub", "huggingface_hub.snapshot_download"]


def test_tinglongwu_has_no_dynamic_imports():
    for f in (ROOT / "tinglongwu").rglob("*.py"):
        text = f.read_text("utf-8")
        assert "__import__" not in text and "importlib" not in text, f


def test_setup_does_not_import_runtime():
    for f in (ROOT / "tinglongwu_setup").rglob("*.py"):
        assert not any(m == "tinglongwu" or m.startswith("tinglongwu.") for m in imports(f)), f


def test_bundle_safe_extract_rejects_traversal(tmp_path):
    import zipfile

    import pytest
    from tinglongwu_setup.cli import safe_extract

    z = tmp_path / "b.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../evil.txt", "x")
    with pytest.raises(SystemExit):
        safe_extract(z, tmp_path / "out")
