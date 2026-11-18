"""#1525: in-app one-click GPU speed-up pack offer (banner decision, Add-ons row, wizard asks, CPU-mode wording)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tinglongwu.gui import core, gpu_offer
from tinglongwu.i18n import t

G = gpu_offer


# ---- decision function --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("gpu,pack,usable,state", [
    (False, None, True, G.UNAVAILABLE),
    (False, Path("x"), True, G.UNAVAILABLE),
    (True, None, True, G.MISSING),
    (True, Path("x"), False, G.OLD),
    (True, Path("x"), True, G.INSTALLED),
])
def test_gpu_pack_offer_state(gpu, pack, usable, state):
    assert G.pack_state(gpu, pack, usable) == state


@pytest.mark.parametrize("state,cpu,dismissed,offline,want", [
    (G.MISSING, False, False, False, True),
    (G.OLD, False, False, False, True),
    (G.INSTALLED, False, False, False, False),  # pack current: no banner
    (G.UNAVAILABLE, False, False, False, False),  # no NVIDIA: no banner
    (G.MISSING, True, False, False, False),  # user picked CPU mode
    (G.MISSING, False, True, False, False),  # "Don't ask again"
    (G.MISSING, False, False, True, False),  # TLWU_OFFLINE=1
])
def test_gpu_pack_offer_should_offer(state, cpu, dismissed, offline, want):
    assert G.should_offer(state, cpu, dismissed, offline) is want


def test_gpu_pack_offer_env_flags():
    assert G.offline({"TLWU_OFFLINE": "1"}) and not G.offline({})
    assert G.cpu_mode({"CUDA_VISIBLE_DEVICES": "-1"}) and not G.cpu_mode({"CUDA_VISIBLE_DEVICES": "0"})


def test_gpu_pack_offer_detect_uses_nvidia_smi_not_cuda(tmp_path):
    """The field-test case: nvidia-smi sees the RTX 3050 but CUDA cannot load (no pack) -> missing, so offered."""
    assert G.detect(gpu_info=lambda: ("NVIDIA GeForce RTX 3050", 6144), pack_dir=lambda: None) == G.MISSING
    assert G.detect(gpu_info=lambda: (None, None), pack_dir=lambda: None) == G.UNAVAILABLE
    assert G.detect(gpu_info=lambda: ("RTX", 1), pack_dir=lambda: tmp_path, usable=lambda p: False) == G.OLD


def test_gpu_pack_offer_installed_version(tmp_path):
    assert G.installed_version(None) is None and G.installed_version(tmp_path) is None
    (tmp_path / G.VERSION_FILE).write_text("0.4.10\n", encoding="utf-8")
    assert G.installed_version(tmp_path) == "0.4.10"
    assert G.row_text(G.INSTALLED, "0.4.10") == t("gpu_pack_row_installed", ver=" v0.4.10")
    assert G.row_text(G.UNAVAILABLE, None) == t("gpu_pack_row_unavailable")


def test_gpu_pack_offer_worker_cmd_and_result():
    cmd = G.gpu_pack_cmd("/m", bundled=False)
    assert "--gpu-pack" in cmd and "--whisper" not in cmd and "small" not in cmd and cmd[-2] == "--models-dir"
    assert G.install_ok(0, ["gpu pack 0.4.10: installed in x"])
    assert not G.install_ok(0, ["gpu pack: this release has no GPU pack (tinglongwu-gpu-pack.zip); skipped"])
    assert not G.install_ok(1, [])
    assert G.fail_reason(["x", "gpu pack: FAIL offline (TLWU_OFFLINE=1)"]) == "offline (TLWU_OFFLINE=1)"


def test_gpu_pack_offer_missing_notice_detected():
    from tinglongwu.device import gpu_pack_message

    msg = gpu_pack_message(["cublas64_12.dll"], bundled=True)
    assert G.is_missing_pack_notice(msg)
    assert G.is_missing_pack_notice(" ".join(t("gpu_fallback", why=msg.removeprefix("tlwu: ")).split()))
    assert not G.is_missing_pack_notice(t("gui_cpu_mode"))


# ---- settings key -------------------------------------------------------------------------------------------------
def test_gpu_pack_offer_settings_key(tmp_path):
    p = tmp_path / "s.json"
    assert core.load_settings(p, gpu=lambda: False)[G.DISMISS_KEY] is False
    s = core.load_settings(p, gpu=lambda: False)
    s[G.DISMISS_KEY] = True
    assert core.save_settings(s, p)
    assert json.loads(p.read_text(encoding="utf-8"))[G.DISMISS_KEY] is True
    assert core.load_settings(p, gpu=lambda: False)[G.DISMISS_KEY] is True
    p.write_text(json.dumps({G.DISMISS_KEY: "yes"}), encoding="utf-8")  # wrong type -> default
    assert core.load_settings(p, gpu=lambda: False)[G.DISMISS_KEY] is False


# ---- CPU-mode wording (field test: "CPU mode (no NVIDIA GPU)" on an RTX 3050) -------------------------------------
def test_gpu_pack_offer_cpu_mode_text_does_not_deny_a_found_gpu():
    assert core.cpu_mode_text(gpu_seen=True) == t("gui_cpu_mode_gpu_unused")
    assert core.cpu_mode_text(gpu_seen=False) == t("gui_cpu_mode")
    assert "no NVIDIA" not in core.cpu_mode_text(gpu_seen=True)
    status = core.cpu_mode_text(gpu_seen=True) + " - MT 900 ms"
    assert core.drop_cpu_mode_notice(core.cpu_mode_text(gpu_seen=True), status) == ""


def test_gpu_pack_offer_i18n_all_languages(monkeypatch):
    from tinglongwu import i18n

    monkeypatch.setattr(i18n, "_override", i18n._override)  # restored on teardown, even when an assert fails
    keys = ["gpu_offer_banner", "gpu_offer_install", "gpu_offer_later", "gpu_offer_never", "gpu_offer_running",
            "gpu_offer_ok", "gpu_offer_fail", "gpu_pack_row_name", "gpu_pack_row_installed", "gpu_pack_row_unavailable",
            "gpu_pack_row_missing", "gpu_pack_row_old", "gui_cpu_mode_gpu_unused"]
    en = {}
    for lang in ("en", "ru", "zh-TW", "ja"):
        i18n.set_ui_lang(lang, persist=False)
        for k in keys:
            if lang == "en":
                en[k] = t(k, size="1.5 GB", ver="", reason="r")
            else:
                assert t(k, size="1.5 GB", ver="", reason="r") != en[k] or k == "gpu_pack_row_name", (lang, k)


# ---- setup wizard asks, never auto-installs ------------------------------------------------------------------------
def test_gpu_pack_offer_wizard_asks_first():
    sg = pytest.importorskip("tlwu_app.setup_gui")
    from tlwu_app import tiers

    gpu = tiers.Gpu("NVIDIA GeForce RTX 3050", 6144, 5000)
    tier = tiers.choose_tier(gpu, False)
    asked = []
    assert sg.gpu_pack_choice(tier, gpu, lambda: asked.append(1) or None) is False  # no answer -> no worker
    assert asked == [1]
    assert sg.gpu_pack_choice(tier, gpu, lambda: False) is False  # Later
    assert sg.gpu_pack_choice(tier, gpu, lambda: True) is True  # explicit yes only
    assert sg.gpu_pack_choice(tier, gpu, lambda: 1 / 0) is False  # broken dialog never downloads
    assert sg.gpu_pack_choice(tiers.choose_tier(None, False), None, lambda: pytest.fail("asked without GPU")) is False
    assert sg.gpu_pack_choice(tiers.choose_tier(gpu, True), gpu, lambda: pytest.fail("asked in CPU mode")) is False


def test_gpu_pack_offer_setup_writes_version_marker(tmp_path):
    """The marker is written inside the staged pack before the swap (a failed write keeps the old pack)."""
    import hashlib
    import zipfile

    from tinglongwu_setup import gpu_pack as sp

    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("cublas64_12.dll", b"x")
    man = {"size": z.stat().st_size, "sha256": hashlib.sha256(z.read_bytes()).hexdigest()}
    dest = tmp_path / "gpu-pack"
    get = lambda url, timeout=None: (json.dumps(man).encode() if url == "m" else  # noqa: E731
                                     json.dumps({"tag_name": "v0.4.10", "assets": [
                                         {"name": sp.ASSET, "browser_download_url": "z"},
                                         {"name": sp.MANIFEST_ASSET, "browser_download_url": "m"}]}).encode())
    assert sp.run(dest, "0.4.10", get=get, fetch=lambda u, p, h: z, out=lambda *_: None, env={}) == 0
    assert G.installed_version(dest) == "0.4.10" and (dest / "cublas64_12.dll").is_file()


def test_gpu_pack_offer_garbled_marker_is_unknown(tmp_path):
    (tmp_path / G.VERSION_FILE).write_bytes(b"\xff\xfe\xfa")
    assert G.installed_version(tmp_path) is None


def test_gpu_pack_offer_incomplete_windows_pack_is_old(tmp_path):
    (tmp_path / "cublas64_12.dll").write_bytes(b"x")
    assert not G._pack_usable(tmp_path, platform="win32")  # no cuDNN
    (tmp_path / "cudnn64_9.dll").write_bytes(b"x")
    assert G._pack_usable(tmp_path, platform="win32")


def test_gpu_pack_offer_no_pack_note_matched_by_prefix_only():
    from tinglongwu_setup.gpu_pack import NO_PACK_NOTE

    assert NO_PACK_NOTE.startswith(G.NO_PACK_PREFIX)
    assert not G.install_ok(0, [NO_PACK_NOTE[:90]])
    assert G.install_ok(0, ["gpu pack 0.4.10: installed in C:/no GPU pack here/gpu-pack"])


def test_gpu_pack_offer_notice_matched_in_any_language(monkeypatch):
    from tinglongwu import i18n
    from tinglongwu.device import gpu_pack_message

    monkeypatch.setattr(i18n, "_override", i18n._override)
    en = gpu_pack_message(["cublas64_12.dll"], bundled=True)
    i18n.set_ui_lang("ja", persist=False)  # queued in English, read after a switch to Japanese
    assert G.is_missing_pack_notice(en)


def test_gpu_pack_offer_cpu_text_never_blocks_when_asked(monkeypatch):
    monkeypatch.setattr(core, "_NVIDIA_SEEN", [])
    monkeypatch.setattr("tinglongwu.hwprobe.gpu_info", lambda *a, **k: pytest.fail("nvidia-smi on the Tk thread"))
    assert core.cpu_mode_text(block=False) == t("gui_cpu_mode")
