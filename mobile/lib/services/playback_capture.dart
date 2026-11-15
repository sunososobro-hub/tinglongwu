import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/services.dart';

import '../logic/phone_audio.dart';

/// Native side of phone-audio subtitles (#998): Android AudioPlaybackCapture (MediaProjection)
/// in a foreground service, plus the floating subtitle window. Platform-neutral API: on iOS (no
/// system audio capture) [supported] is false and nothing else is called.
class PlaybackCapture {
  static const _ch = MethodChannel('tlwu/native');
  static const _pcm = EventChannel('tlwu/playback_pcm');

  static Future<bool> supported() async {
    if (!Platform.isAndroid) return false;
    try {
      return await _ch.invokeMethod<bool>('playbackCaptureSupported') ?? false;
    } catch (_) {
      return false;
    }
  }

  /// 16 kHz mono PCM16 chunks; the stream ends when capture stops (notification Stop, the
  /// system revoking the projection, or [stop]).
  static Stream<Uint8List> pcm() =>
      _pcm.receiveBroadcastStream().map((e) => e as Uint8List);

  /// Shows the system consent dialog and starts the capture service. False = declined.
  static Future<bool> start() async =>
      await _ch.invokeMethod<bool>('startPlaybackCapture') ?? false;

  static Future<void> stop() => _ch.invokeMethod('stopPlaybackCapture');

  static Future<bool> overlayAllowed() async =>
      await _ch.invokeMethod<bool>('overlayAllowed') ?? false;

  /// Opens the system "display over other apps" page; returns whether it is granted afterwards.
  static Future<bool> requestOverlayPermission() async =>
      await _ch.invokeMethod<bool>('requestOverlayPermission') ?? false;

  static Future<void> overlayShow() => _ch.invokeMethod('overlayShow');
  static Future<void> overlayHide() => _ch.invokeMethod('overlayHide');

  /// [lines] oldest first; each shows its source (smaller, gray) above its translation.
  /// [draft] = interim source text (#1064), shown gray below the lines until the final replaces it.
  static Future<void> overlayLines(List<SubtitleLine> lines, String? hint, {String draft = '', String draftTr = ''}) =>
      _ch.invokeMethod('overlayText', {
        'lines': [
          for (final l in lines) {'src': l.source, 'tr': l.translation}
        ],
        'hint': hint,
        'draft': draft,
        'draftTr': draftTr,
      });

  static Future<void> overlayStyle(OverlayStyle s) =>
      _ch.invokeMethod('overlayStyle', {'fontSize': s.fontSize, 'opacity': s.opacity});
}
