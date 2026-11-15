"""#1089: with VB-CABLE and VB-Audio Hi-Fi Cable both installed, speak must go to VB-CABLE's "CABLE Input"."""
import pytest

from tinglongwu.reverse.logic import match_device
from tinglongwu.reverse.vmic import pick_win_cable

# soundcard.all_speakers() order from the report (other devices omitted, one extra real speaker added).
SPEAKERS = [
    "Speakers (Realtek(R) Audio)",
    "CABLE In 16ch (VB-Audio Virtual Cable)",
    "Hi-Fi Cable Input (VB-Audio Hi-Fi Cable)",
    "CABLE Input (VB-Audio Virtual Cable)",
]


def test_pick_win_cable_prefers_vb_cable_over_hifi_cable():
    assert pick_win_cable(SPEAKERS) == "CABLE Input (VB-Audio Virtual Cable)"


def test_pick_win_cable_falls_back_to_contains_when_no_prefix_match():
    assert pick_win_cable(["Speakers", "Hi-Fi Cable Input (VB-Audio Hi-Fi Cable)"]) == \
        "Hi-Fi Cable Input (VB-Audio Hi-Fi Cable)"
    assert pick_win_cable(["Speakers (Realtek(R) Audio)"]) is None


def test_match_device_cli_needle_prefers_prefix():
    assert SPEAKERS[match_device(SPEAKERS, "CABLE Input")] == "CABLE Input (VB-Audio Virtual Cable)"


def test_match_device_exact_name_wins_and_contains_still_works():
    names = ["Hi-Fi Cable Input (VB-Audio Hi-Fi Cable)", "Hi-Fi Cable Input"]
    assert match_device(names, "hi-fi cable input") == 1
    assert match_device(SPEAKERS, "realtek") == 0
    with pytest.raises(LookupError):
        match_device(SPEAKERS, "BlackHole")
