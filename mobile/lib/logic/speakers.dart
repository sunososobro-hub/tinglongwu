import 'dart:math' as math;

/// Online speaker clustering (#1155), a port of desktop `tinglongwu/speakers.py`. Pure logic, no
/// model / IO: feed one sentence embedding + its duration, get back a speaker number (1, 2, ...)
/// or [unknown] when it cannot be decided yet.
const unknownSpeaker = 0;

/// "說話者 N" text for a speaker number; empty for [unknownSpeaker].
String speakerLabel(int n) => n > 0 ? '說話者 $n' : '';

/// Cosine similarity; 0 if either vector has zero norm. Throws on a length mismatch.
double cosine(List<double> a, List<double> b) {
  if (a.length != b.length) throw ArgumentError('embedding dimension mismatch');
  var na = 0.0, nb = 0.0, dot = 0.0;
  for (var i = 0; i < a.length; i++) {
    na += a[i] * a[i];
    nb += b[i] * b[i];
    dot += a[i] * b[i];
  }
  if (na == 0 || nb == 0) return 0;
  return dot / (math.sqrt(na) * math.sqrt(nb));
}

/// Cosine similarity to running cluster centroids (sums of unit vectors).
///
/// [threshold]: cosine >= threshold means "same speaker" (same value as desktop).
/// [minSeconds]: shorter sentences never create or update a cluster; they reuse the previous
/// speaker, or [unknownSpeaker] when there is none.
class SpeakerClusterer {
  SpeakerClusterer({this.threshold = 0.6, this.minSeconds = 1.0});

  final double threshold;
  final double minSeconds;
  final List<List<double>> _sums = [];
  int _last = unknownSpeaker;

  int get count => _sums.length;

  int assign(List<double> embedding, double seconds) {
    if (seconds < minSeconds) return _last;
    var norm = 0.0;
    for (final x in embedding) {
      norm += x * x;
    }
    norm = math.sqrt(norm);
    if (norm == 0) return _last;
    final unit = [for (final x in embedding) x / norm];
    var best = -1;
    var bestSim = -2.0;
    for (var i = 0; i < _sums.length; i++) {
      final sim = cosine(unit, _sums[i]);
      if (sim > bestSim) {
        best = i;
        bestSim = sim;
      }
    }
    if (best >= 0 && bestSim >= threshold) {
      final s = _sums[best];
      for (var i = 0; i < s.length; i++) {
        s[i] += unit[i];
      }
    } else {
      _sums.add(unit);
      best = _sums.length - 1;
    }
    return _last = best + 1;
  }
}
