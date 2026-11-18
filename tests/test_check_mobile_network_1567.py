"""#1567: scripts/check_mobile_network.py flags network use outside the pack downloader."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_mobile_network.py"

MANIFEST_OK = '''<manifest xmlns:android="a"><uses-permission android:name="android.permission.RECORD_AUDIO"/>
<uses-permission android:name="android.permission.INTERNET"/></manifest>'''


def make(tmp_path, manifest=MANIFEST_OK, files=None):
    m = tmp_path / "mobile"
    (m / "android/app/src/main/kotlin").mkdir(parents=True, exist_ok=True)
    (m / "lib").mkdir(exist_ok=True)
    (m / "android/app/src/main/AndroidManifest.xml").write_text(manifest)
    for rel, text in (files or {}).items():
        p = m / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return m


def run(m):
    return subprocess.run([sys.executable, str(SCRIPT), "--mobile-dir", str(m)],
                          capture_output=True, text=True)


def test_downloader_only_passes(tmp_path):
    m = make(tmp_path, files={
        "android/app/src/main/kotlin/PackDownloader.kt": "val c = URL(u).openConnection() as HttpURLConnection",
        "lib/ui/screen.dart": "import 'dart:io';\nvoid f() {}",
    })
    r = run(m)
    assert r.returncode == 0, r.stdout


def test_network_api_elsewhere_fails(tmp_path):
    m = make(tmp_path, files={
        "android/app/src/main/kotlin/Other.kt": "val c = HttpURLConnection()",
        "lib/services/x.dart": "import 'package:http/http.dart';",
    })
    r = run(m)
    assert r.returncode == 1
    assert "Other.kt:1" in r.stdout and "x.dart:1" in r.stdout


def test_missing_or_removed_internet_fails(tmp_path):
    r = run(make(tmp_path, manifest='<manifest><uses-permission android:name="android.permission.RECORD_AUDIO"/></manifest>'))
    assert r.returncode == 1 and "does not declare INTERNET" in r.stdout
    removed = '<manifest><uses-permission android:name="android.permission.INTERNET" tools:node="remove"/></manifest>'
    r = run(make(tmp_path, manifest=removed))
    assert r.returncode == 1 and "removes INTERNET" in r.stdout


def test_other_network_permission_fails(tmp_path):
    extra = MANIFEST_OK.replace("</manifest>", '<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE"/></manifest>')
    r = run(make(tmp_path, manifest=extra))
    assert r.returncode == 1 and "ACCESS_NETWORK_STATE" in r.stdout
