import CryptoKit
import Flutter
import UIKit
import UniformTypeIdentifiers

/// Copies model files the user picks in the Files app (UIDocumentPicker) into a staging dir,
/// streaming and hashing on the way. Swift twin of ModelImporter.kt.
/// Which names are accepted and whether size/SHA-256 match is decided in Dart (model_manifest.dart);
/// this side only copies the names Dart asks for, to the target paths Dart gives.
///
/// Zip support is streaming and handles STORED entries only (what `tool/make_models_zip.py`
/// writes). Archives made by the Files app "Compress" action use deflate and are rejected with a
/// clear message; pick the unpacked model files instead.
final class ModelImporter: NSObject, UIDocumentPickerDelegate {
  private static let bufferSize = 1 << 20
  private static let progressStep: Int64 = 4 << 20

  private var pending: FlutterResult?
  /// Picked URLs by their string form: the security scope belongs to the URL object, so the
  /// import must use the object the picker handed over, not a URL rebuilt from the string.
  private var picked: [String: URL] = [:]

  private let lock = NSLock()
  private var cancelledFlag = false
  private var isCancelled: Bool {
    lock.lock(); defer { lock.unlock() }
    return cancelledFlag
  }
  private func setCancelled(_ v: Bool) {
    lock.lock(); cancelledFlag = v; lock.unlock()
  }

  func cancel() { setCancelled(true) }

  // MARK: picker

  func pick(_ result: @escaping FlutterResult) {
    if pending != nil {
      result(FlutterError(code: "BUSY", message: "picker already open", details: nil))
      return
    }
    guard let top = ModelImporter.topViewController() else {
      result(FlutterError(code: "NO_PICKER", message: "no view controller to present from", details: nil))
      return
    }
    let picker = UIDocumentPickerViewController(forOpeningContentTypes: [UTType.item], asCopy: false)
    picker.allowsMultipleSelection = true
    picker.delegate = self
    pending = result
    top.present(picker, animated: true)
  }

  func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
    guard let r = pending else { return }
    pending = nil
    picked.removeAll()
    var out: [[String: Any]] = []
    for url in urls {
      let scoped = url.startAccessingSecurityScopedResource()
      defer { if scoped { url.stopAccessingSecurityScopedResource() } }
      let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? -1
      picked[url.absoluteString] = url
      out.append(["uri": url.absoluteString, "name": url.lastPathComponent, "size": size])
    }
    r(out)
  }

  func documentPickerWasCancelled(_ controller: UIDocumentPickerViewController) {
    guard let r = pending else { return }
    pending = nil
    r([[String: Any]]())
  }

  private static func topViewController() -> UIViewController? {
    let scene = UIApplication.shared.connectedScenes
      .compactMap { $0 as? UIWindowScene }
      .first { $0.activationState == .foregroundActive }
      ?? UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first
    var vc = scene?.windows.first(where: { $0.isKeyWindow })?.rootViewController
      ?? scene?.windows.first?.rootViewController
    while let next = vc?.presentedViewController { vc = next }
    return vc
  }

  // MARK: import

  /// Args: uri, stagingDir, size, and either `target` (a single file) or `zipTargets`
  /// (basename -> target path). Returns [{target, size, sha256}] for every file copied.
  func importDocument(
    _ args: [String: Any], _ result: @escaping FlutterResult,
    progress: @escaping (Int64, Int64) -> Void
  ) {
    guard let uri = args["uri"] as? String, let stagingPath = args["stagingDir"] as? String else {
      result(FlutterError(code: "BAD_ARGS", message: "importDocument needs uri and stagingDir", details: nil))
      return
    }
    let target = args["target"] as? String
    let zipTargets = args["zipTargets"] as? [String: String]
    let total = (args["size"] as? NSNumber)?.int64Value ?? -1
    guard let url = picked[uri] ?? URL(string: uri) else {
      result(FlutterError(code: "IMPORT_FAILED", message: "bad uri \(uri)", details: nil))
      return
    }
    setCancelled(false)

    DispatchQueue.global(qos: .userInitiated).async { [weak self] in
      guard let self = self else { return }
      let scoped = url.startAccessingSecurityScopedResource()
      defer { if scoped { url.stopAccessingSecurityScopedResource() } }

      var lastSent: Int64 = 0
      var done: Int64 = 0
      let tick: (Int64) -> Void = { n in
        done += n
        if done - lastSent >= ModelImporter.progressStep {
          lastSent = done
          let d = done
          DispatchQueue.main.async { progress(d, total) }
        }
      }

      do {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let staging = URL(fileURLWithPath: stagingPath, isDirectory: true)
        var out: [[String: Any]] = []
        if let zipTargets = zipTargets {
          out = try self.importZip(handle, zipTargets, staging, tick)
        } else if let target = target {
          out.append(try self.copy(handle, from: 0, length: nil, staging, target, tick))
        } else {
          throw ImportError.message("importDocument needs target or zipTargets")
        }
        if self.isCancelled { throw ImportError.cancelled }
        let finalDone = done
        let finalOut = out
        DispatchQueue.main.async {
          progress(finalDone, total)
          result(finalOut)
        }
      } catch {
        let cancelled: Bool
        if case ImportError.cancelled = error { cancelled = true } else { cancelled = false }
        let msg = (error as? ImportError)?.description ?? "\(error)"
        DispatchQueue.main.async {
          result(FlutterError(code: cancelled ? "CANCELLED" : "IMPORT_FAILED", message: msg, details: nil))
        }
      }
    }
  }

  private enum ImportError: Error, CustomStringConvertible {
    case cancelled
    case message(String)
    var description: String {
      switch self {
      case .cancelled: return "cancelled"
      case .message(let m): return m
      }
    }
  }

  /// Streams the zip's local file headers front to back (no central directory needed).
  private func importZip(
    _ handle: FileHandle, _ zipTargets: [String: String], _ staging: URL, _ tick: (Int64) -> Void
  ) throws -> [[String: Any]] {
    var out: [[String: Any]] = []
    var seen = Set<String>()
    var offset: UInt64 = 0
    while true {
      if isCancelled { throw ImportError.cancelled }
      try handle.seek(toOffset: offset)
      guard let sig = try handle.read(upToCount: 4), sig.count == 4 else { break }
      // 0x04034b50 = local file header; anything else is the central directory / end of archive.
      if ModelImporter.le32(sig, 0) != 0x0403_4b50 { break }
      guard let hdr = try handle.read(upToCount: 26), hdr.count == 26 else {
        throw ImportError.message("zip is truncated")
      }
      let flags = ModelImporter.le16(hdr, 2)
      let method = ModelImporter.le16(hdr, 4)
      let compSize32 = ModelImporter.le32(hdr, 14)
      let nameLen = Int(ModelImporter.le16(hdr, 22))
      let extraLen = Int(ModelImporter.le16(hdr, 24))
      guard let nameData = try handle.read(upToCount: nameLen), nameData.count == nameLen else {
        throw ImportError.message("zip is truncated")
      }
      let name = String(data: nameData, encoding: .utf8) ?? ""
      let dataStart = offset + 30 + UInt64(nameLen) + UInt64(extraLen)

      if compSize32 == 0xFFFF_FFFF {
        throw ImportError.message("zip64 archives are not supported; pick the model files directly")
      }
      let compSize = UInt64(compSize32)
      let isDir = name.hasSuffix("/")
      let base = name.replacingOccurrences(of: "\\", with: "/").split(separator: "/").last.map(String.init) ?? ""

      if !isDir, let target = zipTargets[base] {
        // Two entries for one target would overwrite a checked file after its hash was taken.
        if !seen.insert(target).inserted { throw ImportError.message("zip has more than one \(base)") }
        if method != 0 || (flags & 0x08) != 0 {
          throw ImportError.message(
            "\(base) is compressed inside the zip; this app reads uncompressed (stored) zips only. "
              + "Use the official tlwu-mobile-models.zip or pick the model files directly.")
        }
        out.append(try copy(handle, from: dataStart, length: compSize, staging, target, tick))
      } else {
        if !isDir && (flags & 0x08) != 0 && compSize == 0 {
          throw ImportError.message("zip entry \(name) has no size in its header (streamed zip)")
        }
        tick(Int64(compSize))
      }
      offset = dataStart + compSize
    }
    return out
  }

  /// Copies [length] bytes (or to EOF when nil) starting at [from] into staging/target, hashing on the way.
  private func copy(
    _ handle: FileHandle, from start: UInt64, length: UInt64?, _ staging: URL, _ target: String,
    _ tick: (Int64) -> Void
  ) throws -> [String: Any] {
    let parts = target.split(separator: "/", omittingEmptySubsequences: false)
    if target.hasPrefix("/") || parts.contains("..") || target.isEmpty {
      throw ImportError.message("unsafe target \(target)")
    }
    let dest = staging.appendingPathComponent(target)
    try FileManager.default.createDirectory(
      at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
    FileManager.default.createFile(atPath: dest.path, contents: nil)
    let outHandle = try FileHandle(forWritingTo: dest)
    defer { try? outHandle.close() }

    try handle.seek(toOffset: start)
    var hasher = SHA256()
    var remaining = length
    var written: Int64 = 0
    while true {
      if isCancelled { throw ImportError.cancelled }
      let want = remaining.map { Int(min(UInt64(ModelImporter.bufferSize), $0)) } ?? ModelImporter.bufferSize
      if want == 0 { break }
      guard let chunk = try handle.read(upToCount: want), !chunk.isEmpty else { break }
      hasher.update(data: chunk)
      try outHandle.write(contentsOf: chunk)
      written += Int64(chunk.count)
      tick(Int64(chunk.count))
      if let r = remaining { remaining = r - UInt64(chunk.count) }
    }
    if let r = remaining, r != 0 { throw ImportError.message("\(target): zip is truncated") }
    let hex = hasher.finalize().map { String(format: "%02x", $0) }.joined()
    return ["target": target, "size": written, "sha256": hex]
  }

  // MARK: little-endian helpers

  private static func le16(_ d: Data, _ i: Int) -> UInt16 {
    let b = [UInt8](d)
    return UInt16(b[i]) | (UInt16(b[i + 1]) << 8)
  }

  private static func le32(_ d: Data, _ i: Int) -> UInt32 {
    let b = [UInt8](d)
    return UInt32(b[i]) | (UInt32(b[i + 1]) << 8) | (UInt32(b[i + 2]) << 16) | (UInt32(b[i + 3]) << 24)
  }
}
