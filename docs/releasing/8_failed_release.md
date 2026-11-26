# Releasing: When a release run fails

Part of [RELEASING](../RELEASING.md).

If the workflow fails before the release is published, re-run it: the draft only has its files replaced, and the wizard lines in `SHA256SUMS.txt` are rewritten, not appended. The workflow refuses to touch a release that is already published. The zip comes from the workflow; do not replace it by hand. A version with a suffix (`__version__ = "1.2.3-beta.1"`) gives a pre-release. If the tag itself is wrong (for example a pre-release tag on neither `main` nor `dev`), delete the draft release (`gh release delete vX.Y.Z`) and the tag (`git push origin :refs/tags/vX.Y.Z`), fix `__version__` on dev, and merge the release PR again (or, for a pre-release, push the tag again).
