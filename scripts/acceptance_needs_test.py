#!/usr/bin/env python3
"""Scripted acceptance for issues that were waiting on a hands-on check (label status:needs-test / 需實測).

Each entry maps an issue to the tests that replace the manual check on a headless Windows machine
(no mic, no GPU, no models). The CI job `acceptance-windows` runs this on windows-latest; run it
locally the same way:

    python scripts/acceptance_needs_test.py            # all issues, table + exit code
    python scripts/acceptance_needs_test.py 317 130    # only these issues

Exit code 0 = every listed issue passed. Real-device checks (audio quality, a real meeting) are out
of scope here; they stay on the issue as a one-line owner task.

Not listed yet because their tests assume Linux (fix the tests first, then add the issue here):
#67 / #90 (install tests: bash paths, cp1252 subprocess decoding, PATH-only env), #85 (Tk init.tcl
lookup fails intermittently on windows-latest; settings default target depends on the OS locale).
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# issue -> (what the manual check was, pytest targets that replace it)
CHECKS: dict[int, tuple[str, list[str]]] = {
    9: ("packaged exe on a clean machine (static DLL scan; build+--smoke is #363)", ["tests/test_clean_machine.py"]),
    10: ("Stop releases the microphone", ["tests/test_stop_releases_device.py"]),
    53: ("source line shown before the translation", ["tests/test_source_first.py"]),
    96: ("missing default model falls back to a downloaded one", ["tests/test_model_pick.py"]),
    124: ("one-click install sets up talk mode", ["tests/test_talk_setup.py"]),
    130: ("non-EXTENSIBLE WASAPI microphone opens", ["tests/test_mic_mixformat.py"]),
    134: ("speak mode en -> zh (reverse direction)", ["tests/test_reverse_en.py", "tests/test_speak_from_en.py"]),
    256: ("built-in tech glossary + Hy-MT2 term prompt",
          ["tests/test_builtin_glossary.py", "tests/test_hymt_glossary.py"]),
    270: ("app icon resource loads", ["tests/test_gui_icon.py"]),
    312: ("speak: Traditional output, filler dropped, clean exit", ["tests/test_speak_filler_312.py"]),
    316: ("speak: every utterance spoken or a reason shown", ["tests/test_tts_no_audio_reason.py"]),
    317: ("WASAPI device invalidated -> reopen, no crash",
          ["tests/test_soundcard_reopen_317.py", "tests/test_com_init.py"]),
    318: ("ru credit hallucination filtered, repeats collapsed",
          ["tests/test_hallucination_quiet.py", "tests/test_stream.py"]),
}


def run(issue: int) -> tuple[bool, float, str]:
    _what, targets = CHECKS[issue]
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    summary = lines[-1] if lines else p.stderr.strip()[-200:]
    if p.returncode != 0:
        print(p.stdout[-4000:], p.stderr[-2000:], sep="\n", file=sys.stderr)
    ok = p.returncode == 0 and " passed" in summary  # all-skipped is not an acceptance
    return ok, time.monotonic() - t0, summary


def main(argv: list[str]) -> int:
    issues = [int(a.lstrip("#")) for a in argv] or sorted(CHECKS)
    unknown = [i for i in issues if i not in CHECKS]
    if unknown:
        print(f"no scripted acceptance for: {unknown}", file=sys.stderr)
        return 2
    rows, failed = [], []
    for i in issues:
        ok, secs, summary = run(i)
        rows.append(f"| #{i} | {CHECKS[i][0]} | {'PASS' if ok else 'FAIL'} | {secs:.1f} s | {summary} |")
        if not ok:
            failed.append(i)
    print("| issue | check | result | time | pytest |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    print(f"\n{len(issues) - len(failed)}/{len(issues)} passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
