"""#1265: a PR into `main` must come from `release/vX.Y.Z` or `docs/fastlane-N` (or the `hotfix/` lane is not allowed here).

main only requires the `test` check, so a feature branch merged straight into main broke every release PR.
Usage: python scripts/check_main_head.py <base_ref> <head_ref>
Exit 0 = allowed (base is not main, or the head name is allowed), 1 = rejected (names the rule).
"""
from __future__ import annotations

import re
import sys

ALLOWED_HEADS = (
    re.compile(r"^release/v\d+\.\d+\.\d+$"),
    re.compile(r"^docs/fastlane-\d+$"),
)
RULE = "PRs into main must come from release/vX.Y.Z or docs/fastlane-N (tlwu_rules.md sections 11 and 14)"


def check(base: str, head: str) -> tuple[bool, str]:
    if base != "main":
        return True, f"base {base!r} is not main"
    if any(p.match(head) for p in ALLOWED_HEADS):
        return True, f"head {head!r} allowed"
    return False, f"head {head!r} not allowed: {RULE}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    ok, msg = check(argv[0], argv[1])
    print(("ok: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
