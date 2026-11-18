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
        "android/app/src/main/kotlin/com/tinglongwu/mobile/PackDownloader.kt": "val c = URL(u).openConnection() as HttpURLConnection",
        "lib/services/pack_downloader.dart": "final c = HttpClient();",
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


def test_same_basename_elsewhere_is_not_exempt(tmp_path):
    r = run(make(tmp_path, files={"lib/analytics/pack_downloader.dart": "final c = HttpClient();"}))
    assert r.returncode == 1 and "analytics/pack_downloader.dart:1" in r.stdout


def test_socket_connect_and_exact_internet_name(tmp_path):
    r = run(make(tmp_path, files={"lib/x.dart": "await Socket.connect('h', 443);"}))
    assert r.returncode == 1 and "x.dart:1" in r.stdout
    extra = MANIFEST_OK.replace('"android.permission.INTERNET"', '"android.permission.INTERNET_EXTRA"')
    r = run(make(tmp_path, manifest=extra))
    assert r.returncode == 1 and "does not declare INTERNET" in r.stdout
    multi = MANIFEST_OK.replace("</manifest>", '<uses-permission android:name="android.permission.CHANGE_WIFI_MULTICAST_STATE"/></manifest>')
    r = run(make(tmp_path / "m", manifest=multi))
    assert r.returncode == 1 and "CHANGE_WIFI_MULTICAST_STATE" in r.stdout


def test_removed_network_permission_is_ok(tmp_path):
    ok = MANIFEST_OK.replace("</manifest>", '<uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" tools:node="remove"/></manifest>')
    assert run(make(tmp_path, manifest=ok)).returncode == 0
