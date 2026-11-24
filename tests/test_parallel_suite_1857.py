"""#1857: the `test` job runs the suite in parallel pytest-xdist workers, so test order differs from run to run and
GUI tests need one display per worker. A test that drops the offline flags (as downloader.main() does) must not leak."""
import os


def test_a_drops_the_offline_flags_like_the_downloader():
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    assert "HF_HUB_OFFLINE" not in os.environ


def test_b_next_test_still_starts_offline():
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_worker_display_one_per_xdist_worker():
    from conftest import worker_display

    on = {"TLWU_XVFB_PER_WORKER": "1"}
    assert worker_display({**on, "PYTEST_XDIST_WORKER": "gw0"}) == ":200"
    assert worker_display({**on, "PYTEST_XDIST_WORKER": "gw3"}) == ":203"
    assert worker_display({**on}) is None                                   # not an xdist worker
    assert worker_display({"PYTEST_XDIST_WORKER": "gw1"}) is None            # switch off: keep DISPLAY
    assert worker_display({**on, "PYTEST_XDIST_WORKER": "master"}) is None
    # a plain pytest child of a test inherits the worker's env but is no xdist worker: keeps its own DISPLAY
    assert worker_display({**on, "PYTEST_XDIST_WORKER": "gw0"}, worker=False) is None
