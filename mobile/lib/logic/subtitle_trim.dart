/// Bounded subtitle list that never drops lines still waiting for a
/// translation (pure logic, #533).
library;

/// Removes the oldest items for which [done] is true until [items] has at most
/// [keep] entries. Items not yet done (queued or in-flight translations) are
/// kept even if that leaves the list longer than [keep]; they are trimmed by a
/// later call once translated.
void trimFinished<T>(List<T> items, int keep, bool Function(T) done) {
  var i = 0;
  while (items.length > keep && i < items.length) {
    if (done(items[i])) {
      items.removeAt(i);
    } else {
      i++;
    }
  }
}
