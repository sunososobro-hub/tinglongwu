"""#1348: the self-updater only takes a SHA256SUMS.txt signed by the embedded Ed25519 key, and re-hashes a pending
installer / app-layer zip right before apply. All offline: every request is a fake."""
import base64
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu_setup import ed25519, selfupdate as su

ROOT = Path(__file__).resolve().parent.parent
SK = bytes(range(32))
PK = ed25519.public_key(SK).hex()
EXE = b"installer bytes"
ZIP = b"PK app layer bytes"


def test_rfc8032_vector_1():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = ed25519.public_key(sk)
    assert pk.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    sig = ed25519.sign(sk, b"")
    assert sig.hex() == ("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                         "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.verify(pk, b"", sig)
    assert not ed25519.verify(pk, b"x", sig)
    assert not ed25519.verify(pk, b"", sig[:63] + bytes([sig[63] ^ 1]))
    assert not ed25519.verify(b"\x00" * 31, b"", sig) and not ed25519.verify(pk, b"", b"short")


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")
def test_openssl_signature_verifies(tmp_path):
    key, msg, sig = tmp_path / "k.pem", tmp_path / "m", tmp_path / "s"
    if subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(key)],
                      capture_output=True).returncode:
        pytest.skip("openssl without ed25519")
    msg.write_bytes(b"abc  tinglongwu-setup.exe\n")
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key), "-rawin", "-in", str(msg), "-out", str(sig)],
                   check=True, capture_output=True)
    der = subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    assert ed25519.verify(der[-32:], msg.read_bytes(), sig.read_bytes())


def sums_for(exe=EXE, zipb=ZIP) -> bytes:
    return (f"{hashlib.sha256(zipb).hexdigest()}  {su.APP_ZIP_NAME}\n"
            f"{hashlib.sha256(exe).hexdigest()}  {su.EXE_NAME}\n").encode()


def sig_for(data: bytes, sk=SK) -> bytes:
    return base64.b64encode(ed25519.sign(sk, data)) + b"\n"


def fake_release(sums: bytes, sig: bytes | None):
    names = [su.EXE_NAME, su.SUMS_NAME, su.APP_ZIP_NAME, su.APP_MANIFEST_NAME] + ([su.SIG_NAME] if sig else [])
    rel = json.dumps({"tag_name": "v9.9.9", "assets": [{"name": n, "browser_download_url": f"https://x/{n}",
                                                        "size": 1} for n in names]}).encode()
    man = json.dumps({"runtime_key": "k", "lite": False, "sha256": hashlib.sha256(ZIP).hexdigest(),
                      "size": len(ZIP)}).encode()
    files = {su.SUMS_NAME: sums, su.SIG_NAME: sig, su.APP_MANIFEST_NAME: man}

    def get(url, timeout=None):
        name = url.rsplit("/", 1)[-1]
        return files[name] if name in files else rel
    return get


def fake_fetch(body: bytes):
    def fetch(url, dest, sha, on_bytes=None):
        dest.write_bytes(body)
        if hashlib.sha256(body).hexdigest() != sha:
            raise ValueError("sha256 mismatch")
        return dest
    return fetch


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(su, "SUMS_PUBKEY", PK)


def test_signed_release_downloads_and_applies(tmp_path, key):
    sums = sums_for()
    path = su.download(tmp_path, get=fake_release(sums, sig_for(sums)), fetch=fake_fetch(EXE), say=lambda *_: None)
    assert path.read_bytes() == EXE
    assert su.verify_pending(path) == hashlib.sha256(EXE).hexdigest()
    z = su.download_app(tmp_path, get=fake_release(sums, sig_for(sums)), fetch=fake_fetch(ZIP), say=lambda *_: None)
    assert su.verify_pending(z) == hashlib.sha256(ZIP).hexdigest()


@pytest.mark.parametrize("sig", [None, b"not base64!!", lambda s: sig_for(s, sk=bytes(32)),
                                 lambda s: sig_for(s + b"x")], ids=["unsigned", "malformed", "other-key", "other-file"])
def test_unsigned_or_wrongly_signed_release_fails(tmp_path, key, sig):
    sums = sums_for()
    sig = sig(sums) if callable(sig) else sig
    fetched = []

    def fetch(*a, **k):
        fetched.append(a)
    with pytest.raises(ValueError, match="sign"):
        su.download(tmp_path, get=fake_release(sums, sig), fetch=fetch, say=lambda *_: None)
    with pytest.raises(ValueError, match="sign"):
        su.download_app(tmp_path, get=fake_release(sums, sig), fetch=fetch, say=lambda *_: None)
    assert fetched == [] and list(tmp_path.iterdir()) == []  # nothing downloaded, nothing kept


def test_cli_download_prints_fail_for_unsigned(tmp_path, key, monkeypatch, capsys):
    sums = sums_for()
    orig = su.download
    monkeypatch.setattr(su, "download", lambda d: orig(d, get=fake_release(sums, None), fetch=fake_fetch(EXE)))
    monkeypatch.delenv("TLWU_OFFLINE", raising=False)
    assert su.main(["--download", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith("FAIL ValueError") and "not signed" in out and "READY" not in out


def _pending(tmp_path, body=EXE, signed=True, name="tinglongwu-setup-9.9.9.exe"):
    p = tmp_path / name
    p.write_bytes(body)
    sums = sums_for()
    su.keep_sums(p, sums, sig_for(sums) if signed else None)
    return p


def test_pending_file_modified_after_download_is_refused(tmp_path, key, monkeypatch):
    p = _pending(tmp_path)
    p.write_bytes(b"evil installer")
    with pytest.raises(ValueError, match="changed after download"):
        su.verify_pending(p)
    started = []
    monkeypatch.setattr(su, "launch_apply", lambda *a, **k: started.append(a))
    assert su.main(["--apply", str(p), "--app", str(tmp_path), "--pid", "1"]) == 1
    assert started == []  # nothing runs


def test_pending_with_tampered_kept_sums_is_refused(tmp_path, key):
    p = _pending(tmp_path, body=b"evil installer")
    evil = sums_for(exe=b"evil installer")
    Path(str(p) + ".sums").write_bytes(evil)  # the attacker rewrites the kept sums too, but cannot sign them
    with pytest.raises(ValueError, match="signature"):
        su.verify_pending(p)


def test_pending_without_kept_sums_is_refused(tmp_path):
    p = tmp_path / "tinglongwu-setup-9.9.9.exe"
    p.write_bytes(EXE)
    with pytest.raises(ValueError, match="no kept"):
        su.verify_pending(p)


def test_no_embedded_key_still_rehashes(tmp_path, monkeypatch):
    monkeypatch.setattr(su, "SUMS_PUBKEY", "")
    p = _pending(tmp_path, signed=False)
    assert su.verify_pending(p) == hashlib.sha256(EXE).hexdigest()
    p.write_bytes(b"changed")
    with pytest.raises(ValueError):
        su.verify_pending(p)


def test_apply_cli_passes_hash_to_helper(tmp_path, key, monkeypatch, capsys):
    p = _pending(tmp_path)
    seen = {}
    monkeypatch.setattr(su, "launch_apply", lambda exe, app, pid, app_layer=False, sha=None: seen.update(sha=sha))
    assert su.main(["--apply", str(p), "--app", str(tmp_path), "--pid", "1"]) == 0
    assert seen["sha"] == hashlib.sha256(EXE).hexdigest() and "APPLYING" in capsys.readouterr().out
    script = su.apply_script(p, tmp_path, 1, seen["sha"])
    assert script.index("Get-FileHash") < script.index("Start-Process -FilePath")
    zscript = su.apply_app_script(tmp_path / "a.zip", tmp_path, 1, "ab" * 32)
    assert zscript.index("Get-FileHash") < zscript.index("Expand-Archive")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="needs PowerShell")
def test_helper_refuses_file_changed_after_check(tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"changed")
    script = tmp_path / "s.ps1"
    script.write_text(su.apply_script(exe, tmp_path, 0, hashlib.sha256(EXE).hexdigest()), "utf-8-sig")
    r = subprocess.run(["pwsh", "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0  # the helper swallowed the refusal; the installer never ran


def test_sign_script_round_trip(tmp_path):
    f = tmp_path / su.SUMS_NAME
    f.write_bytes(sums_for())
    env = {"TLWU_SUMS_SIGNING_KEY": SK.hex(), "PATH": "", "SYSTEMROOT": ""}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sign_sums.py"), "sign", str(f)], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    su.verify_sums(f.read_bytes(), Path(str(f) + ".sig").read_bytes(), PK)
    ok = subprocess.run([sys.executable, str(ROOT / "scripts" / "sign_sums.py"), "verify", str(f), PK],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == "OK"
    f.write_bytes(f.read_bytes() + b"x")
    bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "sign_sums.py"), "verify", str(f), PK],
                         capture_output=True, text=True)
    assert bad.returncode == 1


def test_sign_script_without_key_writes_nothing(tmp_path):
    f = tmp_path / su.SUMS_NAME
    f.write_bytes(b"x")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "sign_sums.py"), "sign", str(f)],
                       env={"PATH": "", "SYSTEMROOT": ""}, capture_output=True, text=True)
    assert r.returncode == 0 and not Path(str(f) + ".sig").exists()
