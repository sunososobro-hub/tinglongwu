r"""Release gate for the CI-built Windows zip (#414): test what users download, not the build folder.

    python packaging\release_check.py <tinglongwu-win64.zip> --work DIR [--report report.md]

1. size < 2 GiB (GitHub Release per-asset limit); SHA256 written next to the zip as <zip>.sha256
2. unzip, then scripts/check_dist_dlls.py on the unpacked app (every DLL dependent resolves, CUDA too; #398)
3. llama CPU instructions: every bundled ggml-cpu*.dll (CUDA build and CPU fallback) is asked which instruction
   sets it was compiled for; anything beyond the supported AVX2 baseline (AVX-512, AVX-VNNI, AMX) fails, since it
   would crash with an illegal instruction on a supported AVX2 PC (#74; owner gate on #414). Each DLL is loaded in its
   own process, so a build that already crashes on this runner fails the check instead of the checker.
4. `tlwu.exe --smoke`: every native module loads, llama_cpp too (CPU fallback allowed: no GPU on the runner)
5. `tlwu.exe setup --whisper tiny --hymt` (network), then `tlwu.exe --selftest --model tiny --engine hymt
   --device cpu`: Whisper transcribes the bundled clip and Hy-MT2 translates it on the CPU, inside the packaged exe
   (OPUS was removed in #471; `--engine opus` is rejected)
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
# argv after `tlwu.exe` (models dir appended); tests parse them with the real setup / cli parsers
SETUP_ARGS = ["setup", "--whisper", "tiny", "--hymt"]
SELFTEST_ARGS = ["--selftest", "--model", "tiny", "--engine", "hymt", "--device", "cpu"]
# ggml-cpu exports: name -> compiled-in feature. All beyond the AVX2 baseline of docs/details.md.
BEYOND_AVX2 = {"ggml_cpu_has_avx512": "AVX512", "ggml_cpu_has_avx512_vbmi": "AVX512_VBMI",
               "ggml_cpu_has_avx512_vnni": "AVX512_VNNI", "ggml_cpu_has_avx512_bf16": "AVX512_BF16",
               "ggml_cpu_has_avx_vnni": "AVX_VNNI", "ggml_cpu_has_amx_int8": "AMX_INT8"}
PROBE = r"""
import ctypes, os, sys
dll = sys.argv[1]
os.add_dll_directory(os.path.dirname(dll))
for d in sys.argv[2:]:
    os.add_dll_directory(d)
lib = ctypes.CDLL(dll)
for name in %r:
    f = getattr(lib, name, None)
    print(name, -1 if f is None else f())
"""


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


def probe_verdict(rc: int, out: str) -> tuple[bool, str]:
    """Output of PROBE for one ggml-cpu DLL -> (ok, detail)."""
    if rc != 0:
        hint = " (illegal instruction: built for a newer CPU)" if rc & 0xFFFFFFFF == 0xC000001D else ""
        return False, f"probe exit code {rc:#x}{hint}: {out.strip()[-200:]}"
    got = dict(ln.split() for ln in out.splitlines() if len(ln.split()) == 2)
    if not any(got.get(k, "-1") != "-1" for k in BEYOND_AVX2):
        return False, "no ggml_cpu_has_* export answered; cannot tell the instruction sets"
    bad = [BEYOND_AVX2[k] for k, v in got.items() if k in BEYOND_AVX2 and v not in ("0", "-1")]
    return (False, "compiled for " + ", ".join(bad)) if bad else (True, "AVX2 baseline")


def llama_cpu_check(app: Path, runner=subprocess.run) -> tuple[bool, str]:
    dlls = sorted(p for d in ("lib", "lib_cpu") for p in (app / "llama_cpp" / d).glob("ggml-cpu*.dll"))
    if not dlls:
        return False, "no llama_cpp/lib*/ggml-cpu*.dll in the package"
    extra = [str(p) for p in (app, app / "cuda_libs") if p.is_dir()]
    code = PROBE % (list(BEYOND_AVX2),)
    details, ok_all = [], True
    for dll in dlls:
        r = runner([sys.executable, "-c", code, str(dll), *extra], capture_output=True, text=True, timeout=120)
        ok, why = probe_verdict(r.returncode, (r.stdout or "") + (r.stderr or ""))
        ok_all &= ok
        details.append(f"{dll.parent.name}/{dll.name}: {why}")
    return ok_all, "; ".join(details)


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
            "- Built by the `release` workflow on GitHub Actions (windows-latest); unsigned, not reproducible."]
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", type=Path)
    ap.add_argument("--work", type=Path, required=True, help="scratch folder (unpacked app, models)")
    ap.add_argument("--report", type=Path)
    a = ap.parse_args(argv)
    rows: list[tuple[str, bool, str]] = []
    size, digest = a.zip.stat().st_size, sha256(a.zip)
    a.zip.with_name(a.zip.name + ".sha256").write_text(f"{digest}  {a.zip.name}\n", "utf-8")
    prob = size_problem(size)
    rows.append(("size < 2 GiB", not prob, prob or f"{size >> 20} MB"))

    with zipfile.ZipFile(a.zip) as z:
        z.extractall(a.work / "unzipped")
    app = a.work / "unzipped" / "tlwu"
    sys.path.insert(0, str(REPO / "scripts"))
    from check_dist_dlls import check  # noqa: E402

    missing = check(app)
    rows.append(("check_dist_dlls.py (unpacked zip)", not missing,
                 "every DLL dependent resolves" if not missing else "MISSING lines in the log"))
    rows.append(("llama CPU instructions (AVX2 baseline)", *llama_cpu_check(app)))
    rows.append(("tlwu.exe --smoke", *smoke_verdict(*run([str(app / "tlwu.exe"), "--smoke"], app, 600))))

    models = a.work / "models"
    env = {**os.environ, "TLWU_MODELS": str(models)}
    name = "tlwu.exe --selftest (CPU, whisper tiny + Hy-MT2)"
    rc, _ = run([str(app / "tlwu.exe"), *SETUP_ARGS, "--models-dir", str(models)], app, 1800, env)
    if rc != 0:
        rows.append((name, False, f"model download failed (exit {rc})"))
    else:
        rc, _ = run([str(app / "tlwu.exe"), *SELFTEST_ARGS, "--models-dir", str(models)], app, 1800, env)
        rows.append((name, rc == 0, "OK" if rc == 0 else f"exit code {rc}"))

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
