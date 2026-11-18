#!/usr/bin/env python3
"""Mobile release-lane helpers (#1534): versionName, internal build label, release notes.

    python3 -I tool/mobile_version.py name   [pubspec.yaml]            -> X.Y.Z (versionName)
    python3 -I tool/mobile_version.py dev    <run_number> [pubspec]    -> X.Y.Z-dev.<run>
    python3 -I tool/mobile_version.py next   [pubspec]                 -> X.Y.(Z+1)
    python3 -I tool/mobile_version.py notes  <X.Y.Z> [whats_new.md]    -> body of "## mobile X.Y.Z"
                                                                         (exit 1 if missing/empty/TODO)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PUBSPEC = os.path.join(os.path.dirname(HERE), "pubspec.yaml")
WHATS_NEW = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "whats_new.md")
_VER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def version_name(pubspec_text):
    """versionName = the `version:` value of pubspec.yaml without the `+build` part."""
    m = re.search(r"^version:\s*([^\s+#]+)(?:\+\S+)?\s*(?:#.*)?$", pubspec_text, re.M)
    if not m or not _VER.match(m.group(1)):
        raise ValueError("pubspec.yaml has no X.Y.Z version")
    return m.group(1)


def dev_name(base, run_number):
    """Label for internal-track builds, so a tester can tell builds apart."""
    if not _VER.match(base):
        raise ValueError("bad base version: %r" % base)
    run = str(run_number)
    if not run.isdigit() or int(run) < 1:
        raise ValueError("run number must be a positive integer: %r" % run_number)
    return "%s-dev.%d" % (base, int(run))


def next_patch(base):
    m = _VER.match(base)
    if not m:
        raise ValueError("bad base version: %r" % base)
    return "%s.%s.%d" % (m.group(1), m.group(2), int(m.group(3)) + 1)


def notes_section(text, version):
    """Body of the `## mobile <version>` section of docs/whats_new.md.

    Raises ValueError when the section is missing, has no bullet, or still says TODO."""
    lines = text.splitlines()
    head = "## mobile %s" % version
    try:
        start = next(i for i, ln in enumerate(lines) if ln.strip() == head)
    except StopIteration:
        raise ValueError("no '%s' section" % head) from None
    body = []
    for ln in lines[start + 1:]:
        if ln.startswith("## "):
            break
        body.append(ln)
    body = "\n".join(body).strip()
    if not any(b.lstrip().startswith("- ") for b in body.splitlines()):
        raise ValueError("'%s' has no bullet" % head)
    if "TODO" in body:
        raise ValueError("'%s' still has TODO" % head)
    return body


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv):
    try:
        cmd = argv[1]
        if cmd == "name":
            print(version_name(_read(argv[2] if len(argv) > 2 else PUBSPEC)))
        elif cmd == "dev":
            print(dev_name(version_name(_read(argv[3] if len(argv) > 3 else PUBSPEC)), argv[2]))
        elif cmd == "next":
            print(next_patch(version_name(_read(argv[2] if len(argv) > 2 else PUBSPEC))))
        elif cmd == "notes":
            print(notes_section(_read(argv[3] if len(argv) > 3 else WHATS_NEW), argv[2]))
        else:
            raise IndexError
    except IndexError:
        print(__doc__, file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
