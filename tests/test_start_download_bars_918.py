"""#918: the first-Start bar follows the largest file's tqdm bar, not the max over every file's bar."""
from tinglongwu.gui import core


def feed(sd, *lines):
    for line in lines:
        sd.feed(("dl_line", line))
    return sd.percent


def test_small_file_at_100_does_not_pin_the_bar():
    sd = core.StartDownload("small")
    assert feed(sd, "config.json: 100%|##########| 2.0k/2.0k [00:00<00:00, 1.2MB/s]") == 100
    assert feed(sd, "model.bin:  10%|#         | 48M/484M [00:01<00:09, 48.0MB/s]") == 10
    assert feed(sd, "model.bin:  60%|######    | 290M/484M [00:06<00:04, 48.0MB/s]") == 60
    assert feed(sd, "vocabulary.txt: 100%|##########| 460k/460k [00:00<00:00]") == 60
    assert feed(sd, "Fetching 4 files:  75%|#######5  | 3/4 [00:07<00:02]") == 60
    sd.feed(("dl_done", "small", 0))
    assert sd.percent == 100 and sd.ok


def test_per_label_monotonic_and_mb_totals():
    sd = core.StartDownload("pack")
    assert feed(sd, "voice.zip 40% (100/250 MB)") == 40
    assert feed(sd, "voice.zip 30% (75/250 MB)") == 40
    assert feed(sd, "readme.txt 100% (1/1 kB)") == 40


def test_without_totals_the_latest_label_is_shown():
    sd = core.StartDownload("medium")
    assert feed(sd, "a 90%", "b 20%") == 20


def test_issue_acceptance_sequence():
    sd = core.StartDownload("small")
    sd.feed(("dl_line", "config.json: 100%|##| 2.0k/2.0k"))
    sd.feed(("dl_line", "model.bin: 10%|#| 48M/484M"))
    assert sd.percent == 10
    sd.feed(("dl_line", "model.bin: 60%|######| 290M/484M"))
    assert sd.percent == 60
    sd.feed(("dl_done", "small", 0))
    assert sd.percent == 100
