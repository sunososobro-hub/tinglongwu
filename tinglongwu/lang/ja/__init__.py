"""Japanese patches (see tinglongwu/lang/__init__.py)."""

# #327: Whisper's Japanese output usually ends a sentence without 。！？ (the 2026-11-12 FLEURS ja eval had none at
# all), so a ja merge buffer never completes on punctuation and only the pause path can release it. The segmenter cuts
# at >= 0.6 s of silence and reports ~0.6-0.95 s, often below the core 0.8 s, so a finished ja sentence sat for the
# whole max_wait (6 s on the meeting preset). For ja, any real VAD pause cut (not a forced chunk cut) means
# "the speaker stopped".
MERGE_PAUSE_SEND = 0.5
