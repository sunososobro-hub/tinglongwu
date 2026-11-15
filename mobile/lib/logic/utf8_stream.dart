/// Streaming UTF-8 decoder for token pieces (pure logic).
///
/// llama.cpp token pieces are raw bytes and often split a multi-byte character
/// (one CJK char = 3 bytes can span two or three tokens). Decoding each piece on
/// its own turns the halves into U+FFFD or garbage. [Utf8Stream] buffers the
/// incomplete tail and only returns complete characters.
library;

import 'dart:convert';

class Utf8Stream {
  Utf8Stream() {
    _sink = const Utf8Decoder(allowMalformed: true)
        .startChunkedConversion(StringConversionSink.fromStringSink(_out));
  }

  final StringBuffer _out = StringBuffer();
  late final ByteConversionSink _sink;
  bool _closed = false;

  /// Feeds the next piece; returns the complete characters it finished (maybe '').
  String add(List<int> bytes) {
    if (_closed) return '';
    _sink.add(bytes);
    return _take();
  }

  /// End of stream: returns what is left; an incomplete tail becomes U+FFFD (then dropped by
  /// [sanitizeOutput]).
  String close() {
    if (_closed) return '';
    _closed = true;
    _sink.close();
    return _take();
  }

  String _take() {
    final s = _out.toString();
    _out.clear();
    return s;
  }
}

/// Removes characters that can only be decoding damage from translator output: U+FFFD, control
/// characters, and IPA / spacing-modifier letters (U+0250-02FF) that never belong in a subtitle.
String sanitizeOutput(String s) => s.replaceAll(
    RegExp('[\u{FFFD}\u{0000}-\u{0008}\u{000B}\u{000C}\u{000E}-\u{001F}\u{0250}-\u{02FF}]'), '');
