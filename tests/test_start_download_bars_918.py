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


class FakeProc:
    def __init__(self, lines):
        self.stdout, self._rc = [x.encode() for x in lines], 0

    def wait(self):
        return self._rc


def test_long_bar_from_the_child_keeps_the_model_total():
    """Producer -> StartDownload: an 80-char bar field must not cut the size off before the 90-char limit."""
    bar = "#" * 8 + " " * 72
    lines = ["config.json: 100%|##########| 2.0k/2.0k [00:00<00:00]\n",
             f"model.bin:  10%|{bar}| 48.4M/484M [00:01<00:09, 48.0MB/s]\n"]
    events = []
    core.run_download("small", events.append, popen=lambda *a, **k: FakeProc(lines))
    sd = core.StartDownload("small")
    for ev in events[:-1]:
        sd.feed(ev)
        assert len(ev[1]) <= 90
    assert sd.percent == 10
    sd.feed(events[-1])
    assert sd.percent == 100 and sd.ok


def test_long_file_name_from_the_child_keeps_the_model_total():
    """Producer -> StartDownload: a 79-char file name must not cut the size off either."""
    name = "a" * 75 + ".bin"
    lines = ["config.json: 100%|##########| 2.0k/2.0k [00:00<00:00]\n",
             f"{name}:  10%|#         | 48M/484M [00:01<00:09, 48.0MB/s]\n",
             f"{name}:  60%|######    | 290M/484M [00:06<00:04, 48.0MB/s]\n"]
    events = []
    core.run_download("small", events.append, popen=lambda *a, **k: FakeProc(lines))
    sd = core.StartDownload("small")
    seen = []
    for ev in events[:-1]:
        sd.feed(ev)
        seen.append(sd.percent)
        assert len(ev[1]) <= 90
    assert seen == [100, 10, 60]
    assert "484M" in events[1][1] and "..." in events[1][1]


def test_long_names_with_the_same_end_stay_two_files():
    tail = "x" * 90 + ".safetensors"
    lines = [f"encoder/{tail}:  90%|######### | 1.8k/2.0k [00:00<00:00]\n",
             f"decoder/{tail}:  10%|#         | 48M/484M [00:01<00:09, 48.0MB/s]\n"]
    events = []
    core.run_download("small", events.append, popen=lambda *a, **k: FakeProc(lines))
    sd = core.StartDownload("small")
    for ev in events[:-1]:
        sd.feed(ev)
    assert sd.percent == 10
    one = [core.progress_bar(core.short_line(f"decoder/{tail}: {p}|#| 9M/484M"))[0] for p in (" 10%", "100%")]
    assert one[0] == one[1]  # one file keeps one label when tqdm's padding changes


def test_long_names_that_differ_in_the_middle_stay_two_files():
    a, b = ("encoder/" + "x" * 40 + c + "x" * 40 + ".safetensors" for c in "AB")
    sd = core.StartDownload("small")
    sd.feed(core.line_event("dl_line", f"{a}: 100%|##########| 2.0k/2.0k [00:00<00:00]"))
    sd.feed(core.line_event("dl_line", f"{b}:  10%|#         | 48M/484M [00:01<00:09]"))
    assert sd.percent == 10


def test_names_whose_short_text_is_the_same_stay_two_files():
    """Any shortening can map two names to one text; tracking uses the full line, so they never share a bar."""
    a, b = ("encoder/" + "x" * 20 + c + "x" * 20 + ".safetensors" for c in "AB")
    assert core.short_line(f"{a}: 1%") == core.short_line(f"{b}: 1%")  # same text on screen
    sd = core.StartDownload("small")
    sd.feed(core.line_event("dl_line", f"{a}: 100%|####| 2.0k/2.0k"))
    sd.feed(core.line_event("dl_line", f"{b}:  10%|#   | 48M/484M"))
    assert sd.percent == 10


def test_short_lines_keep_the_two_item_event():
    assert core.line_event("pack_line", "pack: 50%") == ("pack_line", "pack: 50%")


def test_label_is_the_same_whether_or_not_the_line_is_long():
    name = "a" * 56 + ".bin"
    sd = core.StartDownload("small")
    for line in (name + ":  60%|#| 290M/484M", name + ":  10%|#| 48M/484M [00:01<00:09, 48.0MB/s]"):
        ev = core.line_event("dl_line", line)
        assert len(ev[1]) <= 90 and "484M" in ev[1]
        sd.feed(ev)
    assert sd.percent == 60


def test_padding_and_long_tail_stay_within_the_limit():
    out = core.short_line("b" * 80 + ":   5%|#| 1.0M/484M [00:01<10:09, 48.0MB/s, some=extra, more=fields, here=x]")
    assert len(out) <= 90 and "1.0M/484M" in out
