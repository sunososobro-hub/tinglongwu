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
