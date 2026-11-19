"""#1161: build the Chrome Web Store zip of the tlwu capture extension.

Usage: python scripts/build_extension_zip.py [--out-dir dist]   (writes dist/tlwu-extension-<version>.zip)

The zip root is the extension itself (manifest.json at the top). Only runtime files are packed (no README, tests or
spike); the manifest inside the zip carries the repo version (`tinglongwu.__version__`) and no "(dev)" name suffix.
The checked-in web/extension/capture/manifest.json is not modified.
"""
import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = ROOT / "web" / "extension" / "capture"
# Chrome Web Store package limit: 2 GB (https://developer.chrome.com/docs/webstore/publish).
MAX_ZIP_BYTES = 2 * 1024**3
# Files in web/extension/capture/ that are docs, not runtime.
NON_RUNTIME = {"README.md"}


def repo_version(root: Path = ROOT) -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', (root / "tinglongwu" / "__init__.py").read_text("utf-8"), re.M)
    if not m:
        raise SystemExit("cannot read __version__")
    return m.group(1)


def runtime_files(ext_dir: Path = EXT_DIR) -> list[Path]:
    return sorted(p for p in ext_dir.rglob("*") if p.is_file() and p.name not in NON_RUNTIME)


def store_manifest(ext_dir: Path, version: str) -> dict:
    m = json.loads((ext_dir / "manifest.json").read_text("utf-8"))
    m["version"] = version
    m["name"] = re.sub(r"\s*\(dev\)\s*$", "", m["name"])
    return m


def build(out_dir: Path, ext_dir: Path = EXT_DIR, version: str | None = None) -> Path:
    version = version or repo_version()
    if out_dir.resolve().is_relative_to(ext_dir.resolve()):
        raise SystemExit(f"--out-dir must be outside {ext_dir} (the zip would package itself)")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tlwu-extension-{version}.zip"
    manifest = store_manifest(ext_dir, version)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in runtime_files(ext_dir):
            rel = p.relative_to(ext_dir).as_posix()
            if rel == "manifest.json":
                z.writestr(rel, json.dumps(manifest, indent=2) + "\n")
            else:
                z.write(p, rel)
    if out.stat().st_size > MAX_ZIP_BYTES:
        raise SystemExit(f"{out} exceeds the Web Store limit")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(ROOT / "dist"))
    args = ap.parse_args(argv)
    out = build(Path(args.out_dir))
    print(f"{out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
