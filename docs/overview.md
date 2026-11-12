# Overview

Moved from the README (#342).

Fully local, offline, real-time speech translator: **system audio output -> capture -> ASR -> translate -> text**.
It captures whatever the PC plays (the system output mix, not per-app). There is no network use at runtime; only setup / first-run model download uses the network.
Nothing leaves your machine: no cloud APIs, no telemetry, no audio or transcripts written to disk.

## Verification status note

Windows verified so far: one-line install and Listen; the rest is pending (linked issues in the README table).
