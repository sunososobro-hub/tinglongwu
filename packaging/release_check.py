r"""Release gate for the CI-built Windows zip (#414): test what users download, not the build folder.

    python packaging\release_check.py <tinglongwu-win64.zip> --work DIR [--report report.md]

1. size < 2 GiB (GitHub Release per-asset limit) and SHA256 (written next to the zip as <zip>.sha256)
2. unzip, then scripts/check_dist_dlls.py on the unpacked app (every DLL dependent resolves, incl. CUDA; #398)
3. `tlwu.exe --smoke`: every native module loads, llama_cpp too (the CPU fallback is allowed: no GPU on the runner)
4. `tlwu.exe setup --whisper tiny --pair en-zh` (network), then `tlwu.exe --selftest --model tiny --engine opus
   --device cpu`: Whisper transcribes the bundled clip and OPUS translates it, inside the packaged exe
Exit 0 = every check passed. The markdown report goes into the Release notes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAX_ASSET = 2 * 1024**3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def size_problem(n: int) -> str:
    return f"zip is {n} bytes, over the 2 GiB per-asset limit of GitHub Releases" if n >= MAX_ASSET else ""


def smoke_verdict(rc: int, out: str) -> tuple[bool, str]:
    lines = out.splitlines()
    if rc != 0:
        return False, f"exit code {rc}"
    if not any(ln.startswith("smoke: OK") for ln in lines):
        return False, "no 'smoke: OK' line"
    if not any(ln.startswith("OK   llama_cpp") for ln in lines):
        return False, "llama_cpp did not load (#398)"
    if any(ln.startswith("CPU  llama_cpp") for ln in lines):
        return True, "OK (llama_cpp: CUDA build not loaded on the GPU-less runner, CPU fallback loaded)"
    return True, "OK"


def run(cmd: list[str], cwd: Path, timeout: int, env: dict | None = None) -> tuple[int, str]:
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    print(out, flush=True)
    return r.returncode, out


def report(rows: list[tuple[str, bool, str]], size: int, digest: str) -> str:
    out = ["| check | result |", "|---|---|"]
    out += [f"| {name} | {'PASS' if ok else 'FAIL'}: {detail} |" for name, ok, detail in rows]
    out += ["", f"- `tinglongwu-win64.zip`: {size / 1024**2:.0f} MB",
            f"- SHA256: `{digest}` (check with `Get-FileHash tinglongwu-win64.zip`; also in `SHA256SUMS.txt`)",
            "- Built by the `release` workflow on GitHub Actions (windows-latest) from the tagged commit; unsigned, "
            "not reproducible."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path)
    ap.add_argument("--work", type=Path, required=True, help="scratch folder (unpacked app, models)")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    rows: list[tuple[str, bool, str]] = []
    size = a.zip.stat().st_size
    digest = sha256(a.zip)
    a.zip.with_name(a.zip.name + ".sha256").write_text(f"{digest}  {a.zip.name}\n", "utf-8")
    prob = size_problem(size)
    rows.append(("size < 2 GiB", not prob, prob or f"{size >> 20} MB"))

    out_dir = a.work / "unzipped"
    with zipfile.ZipFile(a.zip) as z:
        z.extractall(out_dir)
    app = out_dir / "tlwu"
    sys.path.insert(0, str(REPO / "scripts"))
    from check_dist_dlls import check  # noqa: E402

    missing = check(app)
    rows.append(("check_dist_dlls.py (unpacked zip)", not missing, "every DLL dependent resolves" if not missing
                 else "MISSING lines in the log"))

    rc, out = run([str(app / "tlwu.exe"), "--smoke"], app, 600)
    ok, why = smoke_verdict(rc, out)
    rows.append(("tlwu.exe --smoke", ok, why))

    models = a.work / "models"
    env = {**os.environ, "TLWU_MODELS": str(models)}
    rc, _ = run([str(app / "tlwu.exe"), "setup", "--whisper", "tiny", "--pair", "en-zh", "--models-dir", str(models)],
                app, 1800, env)
    if rc != 0:
        rows.append(("tlwu.exe --selftest (CPU, whisper tiny + OPUS en-zh)", False, f"model download failed (exit {rc})"))
    else:
        rc, out = run([str(app / "tlwu.exe"), "--selftest", "--model", "tiny", "--engine", "opus", "--device", "cpu",
                       "--models-dir", str(models)], app, 1200, env)
        rows.append(("tlwu.exe --selftest (CPU, whisper tiny + OPUS en-zh)", rc == 0,
                     "OK" if rc == 0 else f"exit code {rc}"))

    text = report(rows, size, digest)
    print(text)
    if a.report:
        a.report.write_text(text, "utf-8")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    return 0 if all(ok for _, ok, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
