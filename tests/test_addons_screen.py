"""#614: add-ons manager logic with a fake manifest and a fake network."""
from tinglongwu.gui import addons

SHA = "a" * 64
MAN = {"packs": {
    "gpu": {"feature": "gpu", "size": 2_400_000_000, "sha256": SHA, "url": "u"},
    "ja": {"feature": "ja", "size": 1_133_080_448, "sha256": SHA, "url": "u"},
    "speak": {"feature": "speak", "size": 120_575_669, "sha256": SHA, "url": "u"},
}}


def test_every_row_shows_size_and_one_time_internet_note():
    rs = {r["name"]: r for r in addons.rows(MAN, {})}
    assert rs["gpu"]["note"] == "Needs internet to download (2.4 GB). Works offline afterwards."
    assert rs["speak"]["note"] == "Needs internet to download (121 MB). Works offline afterwards."


def test_nvidia_preticks_only_gpu_pack():
    assert [r["name"] for r in addons.rows(MAN, {}, gpu_present=True) if r["ticked"]] == ["gpu"]
    assert not any(r["ticked"] for r in addons.rows(MAN, {}, gpu_present=False))
    assert not any(r["ticked"] for r in addons.rows(MAN, {"gpu": SHA}, gpu_present=True))


def test_installed_pack_is_marked_and_has_no_download_note():
    r = next(r for r in addons.rows(MAN, {"ja": SHA}) if r["name"] == "ja")
    assert r["installed"] and r["note"] == ""


def test_offline_gives_plain_message_not_traceback():
    def down():
        raise OSError("no route")

    assert addons.load_rows(down, {}) == ([], addons.OFFLINE)
    assert addons.load_rows(lambda: {"bad": 1}, {}) == ([], addons.OFFLINE)
    rs, msg = addons.load_rows(lambda: MAN, {})
    assert len(rs) == 3 and msg == ""


def _fake_models(tmp_path, keys):
    import json

    (tmp_path / "manifest.json").write_text(json.dumps({k: {} for k in keys}), "utf-8")
    for k in keys:
        (tmp_path / k).mkdir()
    return tmp_path


def test_local_rows_list_packs_and_installed_state(tmp_path):
    rs = {r["name"]: r for r in addons.local_rows(tmp_path)}
    assert set(rs) == {"ja", "ko", "ru"} and not any(r["installed"] for r in rs.values())
    assert rs["ja"]["note"].startswith("Needs internet to download (1.1 GB)")
    _fake_models(tmp_path, ["hymt2-1.8b"])
    rs = {r["name"]: r for r in addons.local_rows(tmp_path)}
    assert rs["ja"]["installed"] and rs["ko"]["installed"] and not rs["ru"]["installed"]


def test_remove_keeps_models_another_installed_pack_uses(tmp_path):
    _fake_models(tmp_path, ["hymt2-1.8b", "piper-ru_RU-dmitri-medium"])
    assert addons.remove_pack("ru", tmp_path) == ["piper-ru_RU-dmitri-medium"]  # hymt2 stays: ja/ko still use it
    assert (tmp_path / "hymt2-1.8b").is_dir() and not (tmp_path / "piper-ru_RU-dmitri-medium").exists()
    assert addons.remove_pack("ru", tmp_path) == []  # nothing left, no error


def test_screen_builds_rows_and_runs_fake_download(tmp_path):
    import pytest

    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    from tinglongwu.gui.addons_screen import AddonsScreen

    got = []
    s = AddonsScreen(root, mdir=tmp_path, run=lambda pack, put: (got.append(pack), put(("pack_done", pack, 0))))
    labels = [c.cget("text") for f in s.body.winfo_children() for c in f.winfo_children() if c.winfo_class() == "TButton"]
    assert labels == ["Add", "Add", "Add"]
    s.add("ja")
    s.win.update()
    s._poll()
    assert got == ["ja"] and "ja installed" in s.msg.get()
    root.destroy()
