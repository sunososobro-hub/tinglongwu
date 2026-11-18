#!/usr/bin/env python3
"""#1567: the phone app may use the network only for user-tapped pack downloads.

Checks `mobile/`:
  * the main AndroidManifest declares INTERNET (no `tools:node="remove"`) and no other network permission;
  * network APIs (HTTP/socket clients) appear only in the downloader files (ALLOWED_FILES).

Usage: python scripts/check_mobile_network.py [--mobile-dir mobile]
Exit 0 when clean, 1 naming each violation.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ALLOWED_FILES = {
    "android/app/src/main/kotlin/com/tinglongwu/mobile/PackDownloader.kt",
    "lib/services/pack_downloader.dart",
}
OTHER_NETWORK_PERMISSIONS = (
    "ACCESS_NETWORK_STATE", "CHANGE_NETWORK_STATE", "ACCESS_WIFI_STATE",
    "CHANGE_WIFI_STATE", "NEARBY_WIFI_DEVICES", "CHANGE_WIFI_MULTICAST_STATE",
)
NETWORK_API = re.compile(
    r"HttpURLConnection|HttpsURLConnection|java\.net\.(?:Socket|URL\b|URLConnection)|"
    r"okhttp3?|android\.net\.http|HttpClient|java\.nio\.channels\.SocketChannel|\bSocket(?:\.connect|\.startConnect)?\(|"
    r"WebView|package:http/|package:dio/|dart:html|WebSocket|RawSocket|\bURLSession\b"
)
SOURCE_EXT = {".dart", ".kt", ".java", ".swift"}


def manifest_problems(text: str) -> list[str]:
    out = []
    perms = re.findall(r"<uses-permission[^>]*>", text)
    internet = [p for p in perms if re.search(r'"android\.permission\.INTERNET"', p)]
    if not internet:
        out.append("AndroidManifest.xml does not declare INTERNET")
    for p in internet:
        if "tools:node" in p:
            out.append("AndroidManifest.xml removes INTERNET (tools:node)")
    for name in OTHER_NETWORK_PERMISSIONS:
        if any(f"android.permission.{name}" in p and 'tools:node="remove"' not in p for p in perms):
            out.append(f"AndroidManifest.xml requests {name}; INTERNET must be the only network permission")
    return out


def source_problems(files: dict[str, str]) -> list[str]:
    """files: relative path -> text. Flags network API use outside the downloader files."""
    out = []
    for path, text in sorted(files.items()):
        if path.replace("\\", "/") in ALLOWED_FILES:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if NETWORK_API.search(line):
                out.append(f"{path}:{n}: network API outside the downloader: {line.strip()[:80]}")
    return out


def collect_sources(mobile: Path) -> dict[str, str]:
    files = {}
    for sub in ("lib", "android/app/src/main", "ios/Runner"):
        for p in (mobile / sub).rglob("*"):
            if p.is_file() and p.suffix in SOURCE_EXT:
                files[str(p.relative_to(mobile))] = p.read_text(encoding="utf-8", errors="replace")
    return files


def check(mobile: Path) -> list[str]:
    manifest = mobile / "android/app/src/main/AndroidManifest.xml"
    return manifest_problems(manifest.read_text(encoding="utf-8")) + source_problems(collect_sources(mobile))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mobile-dir", default="mobile")
    problems = check(Path(ap.parse_args(argv).mobile_dir))
    for p in problems:
        print(p)
    if problems:
        return 1
    print("mobile network use: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
