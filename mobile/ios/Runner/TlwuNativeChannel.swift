import Flutter
import UIKit

/// iOS side of the `tlwu/native` method channel (Android twin: MainActivity.kt).
/// Same method names and argument shapes, so the Dart code is shared by both platforms.
///
/// Not implemented on iOS: Google Play asset packs (`playStatus` reports unavailable, so the
/// "Get models" button stays hidden; models are imported from Files with the picker instead).
final class TlwuNativeChannel {
  static let channelName = "tlwu/native"

  private let channel: FlutterMethodChannel
  private let importer = ModelImporter()

  init(messenger: FlutterBinaryMessenger) {
    channel = FlutterMethodChannel(name: TlwuNativeChannel.channelName, binaryMessenger: messenger)
    UIDevice.current.isBatteryMonitoringEnabled = true
    // Subtitle display: never let the screen sleep while the app is in front.
    UIApplication.shared.isIdleTimerDisabled = true
    channel.setMethodCallHandler { [weak self] call, result in
      guard let self = self else { result(FlutterMethodNotImplemented); return }
      self.handle(call, result)
    }
  }

  private func handle(_ call: FlutterMethodCall, _ result: @escaping FlutterResult) {
    switch call.method {
    case "externalFilesDir":
      // Name kept from Android. On iOS: Application Support (private, not shown in Files),
      // excluded from iCloud backup because the models are re-importable.
      do {
        var dir = try FileManager.default.url(
          for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true)
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? dir.setResourceValues(values)
        result(dir.path)
      } catch {
        result(FlutterError(code: "NO_DIR", message: "files dir unavailable: \(error)", details: nil))
      }

    case "stats":
      // temp: iOS exposes no battery temperature, -1 = n/a. thermal: ProcessInfo thermalState
      // (0 nominal, 1 fair, 2 serious, 3 critical).
      let level = UIDevice.current.batteryLevel  // -1 when unknown
      let stats: [String: Any] = [
        "tempC": -1.0,
        "level": level < 0 ? -1 : Int((level * 100).rounded()),
        "thermal": ProcessInfo.processInfo.thermalState.rawValue,
      ]
      result(stats)

    case "pickDocuments":
      importer.pick(result)

    case "importDocument":
      guard let args = call.arguments as? [String: Any] else {
        result(FlutterError(code: "BAD_ARGS", message: "importDocument needs arguments", details: nil))
        return
      }
      importer.importDocument(args, result) { [weak self] done, total in
        self?.channel.invokeMethod("importProgress", arguments: ["done": done, "total": total])
      }

    case "cancelImport":
      importer.cancel()
      result(nil)

    // Google Play asset packs do not exist on iOS.
    case "playStatus":
      result(["available": false])
    case "playFetch":
      result(FlutterError(code: "UNSUPPORTED", message: "Google Play packs are Android only", details: nil))
    case "playCancel", "playRemove":
      result(nil)

    case "freeBytes":
      guard let args = call.arguments as? [String: Any], let dir = args["dir"] as? String else {
        result(FlutterError(code: "BAD_ARGS", message: "freeBytes needs dir", details: nil))
        return
      }
      result(TlwuNativeChannel.freeBytes(dir))

    // Opens a link in Safari (the browser downloads; this app stays offline).
    case "openUrl":
      guard let args = call.arguments as? [String: Any],
        let s = args["url"] as? String, let url = URL(string: s)
      else {
        result(false)
        return
      }
      UIApplication.shared.open(url, options: [:]) { ok in result(ok) }

    // Other apps' audio + floating subtitles (#998): iOS has no system audio capture API.
    case "playbackCaptureSupported":
      result(false)

    default:
      result(FlutterMethodNotImplemented)
    }
  }

  /// Bytes available for the app's own data on the volume holding [dir] (-1 on error).
  private static func freeBytes(_ dir: String) -> Int64 {
    let url = URL(fileURLWithPath: dir)
    try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    if let v = try? url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]),
      let n = v.volumeAvailableCapacityForImportantUsage
    {
      return n
    }
    if let attrs = try? FileManager.default.attributesOfFileSystem(forPath: dir),
      let n = attrs[.systemFreeSize] as? NSNumber
    {
      return n.int64Value
    }
    return -1
  }
}
