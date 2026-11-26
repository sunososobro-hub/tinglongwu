"""#1857: the `test` job runs the suite in parallel pytest-xdist workers, so test order differs from run to run and
GUI tests need one display per worker. A test that drops the offline flags (as downloader.main() does) must not leak."""
import os


def test_a_drops_the_offline_flags_like_the_downloader():
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    assert "HF_HUB_OFFLINE" not in os.environ


def test_b_next_test_still_starts_offline():
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_real_tk_files_share_one_group_others_get_their_own():
    from conftest import REAL_TK_GROUP, xdist_group_for

    assert xdist_group_for("tests/test_x.py", 'tk = pytest.importorskip("tkinter")') == REAL_TK_GROUP
    assert xdist_group_for("tests/test_x.py", "from tinglongwu import gui") == REAL_TK_GROUP
    assert xdist_group_for("tests/test_x.py", "import tinglongwu.overlay as ov") == REAL_TK_GROUP
    assert xdist_group_for("tests/test_x.py", 'env["DISPLAY"] = ":99"') == REAL_TK_GROUP
    assert xdist_group_for("tests/test_x.py", "from tinglongwu import translate") == "test_x.py"


def test_send_gate_833_runs_in_the_real_tk_group():
    # the file whose Tk focus_force segfaulted a parallel worker (PR #1867) must never run beside another Tk file
    from pathlib import Path

    from conftest import REAL_TK_GROUP, xdist_group_for

    assert xdist_group_for(Path(__file__).with_name("test_send_gate_833.py")) == REAL_TK_GROUP
    assert xdist_group_for(Path(__file__).with_name("test_netguard.py")) == "test_netguard.py"


def test_collected_items_carry_the_group(request):
    if not request.config.pluginmanager.hasplugin("xdist"):
        import pytest
        pytest.skip("pytest-xdist not installed")
    from conftest import xdist_group_for

    mark = request.node.get_closest_marker("xdist_group")  # this file names tkinter, so it is in the real-Tk group
    assert mark is not None and mark.args[0] == xdist_group_for(request.node.path) == "real-tk"
