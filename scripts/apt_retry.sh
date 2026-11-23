#!/usr/bin/env bash
# CI apt install that cannot stall a job (#1766): v0.5.4's `vulkan-pack-linux` sat 20 min silent in
# `apt-get update` (release run 45161027121) and was cancelled at CORE_JOB_BUDGET_MIN, so the release shipped
# without tlwu-vulkan-pack-linux-<v>.zip. The same step normally takes 10-30 s (13 earlier release runs).
#
# Usage: scripts/apt_retry.sh <package>...
#   apt-get update: up to APT_UPDATE_TRIES (2) attempts, each killed after APT_UPDATE_TIMEOUT_S (150) seconds;
#   apt-get install -y: one attempt, killed after APT_INSTALL_TIMEOUT_S (420) seconds.
#   Both use Acquire::http::Timeout=30 + Acquire::Retries=3, so a stalled mirror connection fails and is retried
#   instead of hanging. Exit 0 = installed, 1 = gave up (the caller's step fails fast, inside the job budget).
# CI only: it runs apt through sudo when not root and writes nothing outside apt's own state.
set -u
[ "$#" -gt 0 ] || { echo "usage: $0 <package>..." >&2; exit 2; }
tries=${APT_UPDATE_TRIES:-2}
up_s=${APT_UPDATE_TIMEOUT_S:-150}
in_s=${APT_INSTALL_TIMEOUT_S:-420}
pause_s=${APT_RETRY_PAUSE_S:-5}
sudo=(); [ "$(id -u)" = 0 ] || sudo=(sudo)
opts=(-o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 -o Acquire::Retries=3)

i=1
until timeout "$up_s" "${sudo[@]}" apt-get "${opts[@]}" update -qq; do
  rc=$?
  echo "::warning::apt-get update attempt $i/$tries failed (exit $rc; 124 = stalled over ${up_s}s)"
  [ "$i" -lt "$tries" ] || { echo "::error::apt-get update gave up after $tries attempts"; exit 1; }
  i=$((i + 1))
  sleep "$pause_s"
done
if ! timeout "$in_s" "${sudo[@]}" env DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq "$@"; then
  echo "::error::apt-get install $* failed or stalled over ${in_s}s"
  exit 1
fi
