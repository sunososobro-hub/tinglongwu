import Foundation

/// 看影片翻譯 on iOS (#1076): the broadcast extension hands 16 kHz mono PCM16 to the app through
/// a file in the shared App Group container. Compiled into both targets (Runner and Broadcast).
///
/// File layout: 8-byte header = total bytes ever written (UInt64, little-endian), then a ring of
/// [capacity] bytes. One writer (the extension), one reader (the app). The writer writes the data
/// first and the header last, so the reader never sees a header ahead of its data.
enum BroadcastShared {
  static let appGroup = "group.com.tinglongwu.mobile"
  static let extensionBundleId = "com.tinglongwu.mobile.broadcast"
  /// Darwin notifications (no payload) between the two processes.
  static let started = "com.tinglongwu.mobile.broadcast.started"
  static let finished = "com.tinglongwu.mobile.broadcast.finished"
  static let stopRequest = "com.tinglongwu.mobile.broadcast.stop"
  static let ringFileName = "broadcast_pcm.ring"
  static let headerSize = 8
  /// 30 s of 16 kHz mono PCM16.
  static let capacity = 16000 * 2 * 30

  static func ringURL() -> URL? {
    FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroup)?
      .appendingPathComponent(ringFileName)
  }

  static func post(_ name: String) {
    CFNotificationCenterPostNotification(
      CFNotificationCenterGetDarwinNotifyCenter(), CFNotificationName(name as CFString), nil, nil, true)
  }
}

final class BroadcastRingWriter {
  private let handle: FileHandle
  private var total: UInt64 = 0

  /// Creates (or truncates) the ring file. Nil when the App Group container is unavailable.
  init?() {
    guard let url = BroadcastShared.ringURL() else { return nil }
    let size = BroadcastShared.headerSize + BroadcastShared.capacity
    guard FileManager.default.createFile(atPath: url.path, contents: Data(count: size)),
      let h = try? FileHandle(forUpdating: url)
    else { return nil }
    handle = h
  }

  func write(_ data: Data) {
    guard !data.isEmpty else { return }
    let cap = BroadcastShared.capacity
    var off = 0
    do {
      while off < data.count {
        let pos = Int(total % UInt64(cap))
        let n = min(data.count - off, cap - pos)
        try handle.seek(toOffset: UInt64(BroadcastShared.headerSize + pos))
        try handle.write(contentsOf: data.subdata(in: off..<(off + n)))
        off += n
        total += UInt64(n)
      }
      var le = total.littleEndian
      try handle.seek(toOffset: 0)
      try handle.write(contentsOf: Data(bytes: &le, count: 8))
    } catch {
      // Disk full or the file vanished: drop this chunk, the reader just sees a gap.
    }
  }

  func close() {
    try? handle.close()
  }
}

final class BroadcastRingReader {
  private var handle: FileHandle?
  private var readPos: UInt64 = 0

  /// Opens the ring and skips what is already in it (only new audio is wanted).
  @discardableResult
  func open() -> Bool {
    close()
    guard let url = BroadcastShared.ringURL(), let h = try? FileHandle(forReadingFrom: url) else {
      return false
    }
    handle = h
    readPos = header() ?? 0
    return true
  }

  func close() {
    try? handle?.close()
    handle = nil
    readPos = 0
  }

  private func header() -> UInt64? {
    guard let h = handle else { return nil }
    do {
      try h.seek(toOffset: 0)
      guard let d = try h.read(upToCount: 8), d.count == 8 else { return nil }
      var v: UInt64 = 0
      for (i, b) in d.enumerated() { v |= UInt64(b) << (8 * UInt64(i)) }
      return v
    } catch {
      return nil
    }
  }

  /// Bytes written since the last call (nil = nothing new). Keeps at most the newest
  /// [BroadcastShared.capacity] bytes when the reader fell behind.
  func readNew() -> Data? {
    guard let h = handle, let total = header() else { return nil }
    let cap = UInt64(BroadcastShared.capacity)
    if total < readPos { readPos = 0 }  // the extension restarted and recreated the file
    if total - readPos > cap { readPos = total - cap }
    if total == readPos { return nil }
    var out = Data()
    do {
      while readPos < total {
        let pos = readPos % cap
        let n = min(total - readPos, cap - pos)
        try h.seek(toOffset: UInt64(BroadcastShared.headerSize) + pos)
        guard let d = try h.read(upToCount: Int(n)), !d.isEmpty else { break }
        out.append(d)
        readPos += UInt64(d.count)
      }
    } catch {
      return out.isEmpty ? nil : out
    }
    // PCM16: hand over whole samples only.
    if out.count % 2 == 1 {
      out.removeLast()
      readPos -= 1
    }
    return out.isEmpty ? nil : out
  }
}
