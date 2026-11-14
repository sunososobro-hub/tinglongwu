"""#663: reverse/proto.py was split into player / synth / translator / args / session / listen_side. Old imports
from proto must give the very same objects, and patching proto.<name> must still reach the live loop."""
from tinglongwu.reverse import args, listen_side, player, proto, session, synth, translator


def test_proto_reexports_are_the_moved_objects():
    moved = {player: ["Player", "VramSampler", "vram_used_mib"],
             synth: ["FollowTTS", "_synth_sentences", "warm_tts", "_target", "describe_error", "no_audio_reason"],
             translator: ["build_translator", "choose_speak_mt", "_mt_kind"],
             args: ["build_parser", "check_args", "default_whisper", "resolve_tts_engine"],
             session: ["_run_confirm", "_run_auto_live", "make_segmenter", "_stop_live", "_drain", "_close_items"],
             listen_side: ["ListenSide"]}
    for mod, names in moved.items():
        for n in names:
            assert getattr(proto, n) is getattr(mod, n), (mod.__name__, n)


def test_voice_module_imports_from_proto_still_work():
    from tinglongwu.reverse.proto import Player, _synth_sentences  # what tinglongwu/voice.py imports

    assert Player is player.Player and _synth_sentences is synth._synth_sentences


def test_parser_lives_in_args_and_accepts_the_old_options():
    a = args.build_parser().parse_args(["--mic", "--to", "en", "--tts-engine", "kokoro", "--end-silence", "0.5"])
    assert (a.mic, a.to, a.tts_engine, a.end_silence) == ("", "en", "kokoro", 0.5)


def test_live_main_uses_the_names_patched_on_proto(monkeypatch):
    """_live_main stays in proto and looks its helpers up there, so tests/scripts that patch proto.<name> still work."""
    seen = []

    class NoSpeech:
        pass

    class Src:
        def close(self):
            pass

    monkeypatch.setattr(proto, "make_segmenter", lambda *a, **k: (seen.append("seg") or NoSpeech(), 0.5))
    monkeypatch.setattr(proto, "paced_file_source", lambda path: seen.append("paced") or Src())
    monkeypatch.setattr(proto, "_run_auto_live", lambda items, *a, **k: seen.append("auto"))
    monkeypatch.setattr(proto, "_drain", lambda *p: seen.append("drain"))
    monkeypatch.setattr(proto, "_close_items", lambda gen, timeout=5.0: seen.append("close"))

    class A:
        input, paced, chunk_s, dev, ext_stop = "clip.wav", True, 6.0, "cpu", None

    class Pl:
        enabled = False

        def abort(self):
            pass

    proto._live_main(A(), None, None, object(), Pl(), Pl(), False, {"items": []})
    assert seen == ["seg", "paced", "auto", "drain", "close"]
