"""tlwu-setup: network-using setup. Run before going offline; `tlwu` itself never touches the network.

  tlwu-setup --deps                                   pip install pinned requirements
  tlwu-setup --whisper small --langs ru --to zh       download + pin models (see --help)
  tlwu-setup --vosk-tts-ru                            OPT-IN Russian voice model (no upstream license; never default)
  tlwu-setup --piper-ru [dmitri|denis]                Piper Russian voice for speak mode (CC0 data; engine: own venv)
  tlwu-setup --gpu                                    pip install CUDA 12 libs (cuBLAS, cuDNN) for --device cuda
  tlwu-setup --talk [--talk-whisper small --talk-compute cpu --talk-mt opus]
                                                      talk mode: Kokoro venv (~/tts-kokoro) + pinned model + smoke wav
                                                      + tlwu-talk.ps1 launcher (used by install.ps1)
  tlwu-setup --check-llama                            offline: does llama-cpp-python (Qwen / Hy-MT2) fit this CPU?
  tlwu-setup --selftest [--model M --engine E]        offline: load the models, translate one test sentence (tlwu --selftest)
  tlwu-setup --verify                                 offline integrity check
  tlwu-setup --bundle out.zip                         pack wheels + models/ for an air-gapped machine
                                                     (run on the same OS/CPU/Python as the target)
  tlwu-setup --from-bundle out.zip                    offline install from such a bundle
  tlwu-setup --packs zh,en | --list | --pack-prompt ja language packs (see tinglongwu_setup/packs.py)
  tlwu-setup --export-pack ja out.zip | --import-pack out.zip   move one language pack to an offline machine
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from . import downloader, packs, talk

ROOT = downloader.ROOT


# `tlwu --selftest` runs in a child process: setup never imports the offline runtime package (same as --check-llama).
SELFTEST_CMD = [sys.executable, "-m", "tinglongwu"]
SELFTEST_UNAVAILABLE = 3  # exit code when the installed tlwu has no --selftest yet (installers print "skipped")


def run_selftest(args: list[str]) -> int:
    """Forward to `tlwu --selftest <args>`; 0 = the models work, 1 = they do not, 3 = this tlwu has no self-test."""
    p = subprocess.run([*SELFTEST_CMD, "--selftest", *args], stderr=subprocess.PIPE, text=True,
                       encoding="utf-8", errors="replace")
    err = p.stderr or ""
    if p.returncode == 2 and "unrecognized arguments" in err and "--selftest" in err:
        print("self-test skipped: this tlwu version has no --selftest yet")
        return SELFTEST_UNAVAILABLE
    sys.stderr.write(err)
    return p.returncode


def _pip(*args: str) -> None:
    subprocess.check_call([sys.executable, "-m", "pip", *args])


def make_bundle(out: Path, req: Path, mdir: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        wheels = Path(tmp) / "wheels"
        _pip("download", "-r", str(req), "-d", str(wheels))
        meta = {"python": list(sys.version_info[:2]), "platform": sys.platform,
                "machine": platform.machine()}
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(wheels.iterdir()):
                z.write(f, f"wheels/{f.name}")
            z.write(req, "requirements.txt")
            z.writestr("BUNDLE.json", json.dumps(meta))
            for f in sorted(mdir.rglob("*")):
                if f.is_file() and ".cache" not in f.parts:
                    z.write(f, "models/" + f.relative_to(mdir).as_posix())
    print(f"bundle written: {out}")


def safe_extract(zpath: Path, dest: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if Path(n).is_absolute() or ".." in Path(n).parts:
                raise SystemExit(f"unsafe path in bundle: {n}")
        z.extractall(dest)


def from_bundle(zpath: Path, mdir: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        safe_extract(zpath, Path(tmp))
        meta = json.loads((Path(tmp) / "BUNDLE.json").read_text("utf-8"))
        if meta["python"] != list(sys.version_info[:2]) or meta["platform"] != sys.platform:
            print(f"WARNING: bundle built for {meta}; this is {sys.platform} {sys.version_info[:2]}")
        _pip("install", "--no-index", "--find-links", str(Path(tmp) / "wheels"),
             "-r", str(Path(tmp) / "requirements.txt"))
        src = Path(tmp) / "models"
        if src.exists():
            shutil.copytree(src, mdir, dirs_exist_ok=True)
    return downloader.main(["--verify", "--models-dir", str(mdir)])  # SHA256 vs manifest


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="tlwu-setup", add_help=False, description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--deps", action="store_true", help="pip install -r requirements.txt")
    p.add_argument("--gpu", action="store_true",
                   help="pip install requirements-gpu.txt (nvidia-cublas-cu12, nvidia-cudnn-cu12)")
    p.add_argument("--check-llama", action="store_true",
                   help="check that the installed llama-cpp-python build loads and fits this CPU (no network)")
    p.add_argument("--selftest", action="store_true",
                   help="run `tlwu --selftest` (no network); --model/--engine/--device are forwarded")
    p.add_argument("--talk", action="store_true", help="set up talk mode (Kokoro TTS venv, model, launcher)")
    p.add_argument("--talk-venv", default=str(talk.default_venv()))
    p.add_argument("--talk-launcher", default=str(ROOT / "tlwu-talk.ps1"))
    p.add_argument("--talk-whisper", default="small")
    p.add_argument("--talk-compute", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--talk-mt", default="opus", choices=["auto", "qwen", "opus"])
    p.add_argument("--requirements", default=str(ROOT / "requirements.txt"))
    p.add_argument("--bundle", metavar="OUT.zip")
    p.add_argument("--from-bundle", metavar="BUNDLE.zip")
    p.add_argument("--models-dir", default=os.environ.get("TLWU_MODELS") or str(ROOT / "models"))
    p.add_argument("--list", action="store_true", help="offline: installed / missing language packs")
    p.add_argument("--pack-prompt", metavar="LANG", help="offline check; ask before downloading a missing pack")
    p.add_argument("--export-pack", nargs=2, metavar=("LANG", "OUT.zip"))
    p.add_argument("--import-pack", metavar="PACK.zip")
    args = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in args:  # forward the rest verbatim (argparse here would take --model as --models-dir)
        return run_selftest([x for x in args if x != "--selftest"])
    a, rest = p.parse_known_args(args)
    if a.help:
        p.print_help()
        print("\nModel options (forwarded):\n")
        return downloader.main(["-h"])
    if a.check_llama:  # separate process: setup never imports the offline runtime package
        return subprocess.call([sys.executable, "-m", "tinglongwu.llamacheck"])
    mdir = Path(a.models_dir)
    if a.talk:
        return talk.main(a, mdir)
    if a.from_bundle:
        return from_bundle(Path(a.from_bundle), mdir)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # pack prompts use non-ASCII; never crash on a legacy console
    if a.list:
        return packs.list_packs(mdir)
    if a.pack_prompt:
        return packs.prompt(a.pack_prompt, mdir)
    if a.export_pack:
        return packs.export(a.export_pack[0], Path(a.export_pack[1]), mdir)
    if a.import_pack:
        return packs.import_pack(Path(a.import_pack), mdir)
    if a.deps:
        _pip("install", "-r", a.requirements)
    if a.gpu:
        _pip("install", "-r", str(ROOT / "requirements-gpu.txt"))
    if rest or not (a.deps or a.bundle or a.gpu):
        rc = downloader.main([*rest, "--models-dir", str(mdir)])
        if rc:
            return rc
    if a.bundle:
        make_bundle(Path(a.bundle), Path(a.requirements), mdir)
    return 0
