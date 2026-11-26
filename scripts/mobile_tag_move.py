#!/usr/bin/env python3
"""mobile-apk.yml publish helper (#2018, #1021): point the rolling tag at the built commit.

    python3 -I scripts/mobile_tag_move.py REPO TAG SHA

GITHUB_TOKEN may not point a ref at a commit whose diff changes `.github/workflows/` (that needs the
`workflows` permission, which an Actions token never has). The API then answers HTTP 403
"Resource not accessible by integration". No PAT is used: on that refusal the tag stays where it is
and the caller still uploads the APK and rewrites the notes (`commit:` = the built SHA), so the
preview is current; the next dev push without workflow changes moves the tag again.

Prints one key=value line for $GITHUB_OUTPUT: `tag=moved`, `tag=created` or `tag=kept`.
Any other failure exits non-zero (the publish step fails as before).
"""
import subprocess
import sys

REFUSED = ("HTTP 403", "Resource not accessible by integration")


def _gh(args, run):
    p = run(["gh", "api", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _refused(out):
    return any(r in out for r in REFUSED)


def move_tag(repo, tag, sha, run=subprocess.run):
    """Return (state, message). state: moved / created / kept. Raises RuntimeError on other errors."""
    rc, out = _gh([f"repos/{repo}/git/ref/tags/{tag}", "--jq", ".object.sha"], run)
    exists = rc == 0
    old = out.strip() if exists else ""
    if exists:
        if old == sha:
            return "moved", f"{tag} already at {sha}"
        rc, out = _gh(["-X", "PATCH", f"repos/{repo}/git/refs/tags/{tag}",
                       "-f", f"sha={sha}", "-F", "force=true"], run)
        if rc == 0:
            return "moved", f"{tag}: {old} -> {sha}"
        if _refused(out):
            return "kept", (f"{tag} kept at {old}: GITHUB_TOKEN may not point a tag at {sha} "
                            "(its diff changes .github/workflows/); APK and notes are still updated")
        raise RuntimeError(f"moving {tag} failed: {out.strip()}")
    rc, out = _gh(["-X", "POST", f"repos/{repo}/git/refs",
                   "-f", f"ref=refs/tags/{tag}", "-f", f"sha={sha}"], run)
    if rc == 0:
        return "created", f"{tag} created at {sha}"
    # no tag to fall back on: the release cannot be created without one
    raise RuntimeError(f"creating {tag} failed: {out.strip()}")


def main(argv, run=subprocess.run):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    _, repo, tag, sha = argv
    try:
        state, msg = move_tag(repo, tag, sha, run)
    except RuntimeError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    print(("::warning::" if state == "kept" else "") + msg, file=sys.stderr)
    print(f"tag={state}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
