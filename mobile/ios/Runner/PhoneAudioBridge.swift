import AVFoundation
import Flutter
import ReplayKit
import UIKit

/// 看影片翻譯 on iOS (#1076), app side. Android twin: PlaybackCaptureService.kt + SubtitleOverlay.kt.
///
/// - Start: shows the system broadcast sheet (RPSystemBroadcastPickerView with our extension
///   preselected) and waits until the extension reports "started" (Darwin notification).
/// - Audio: polls the App Group ring the extension writes (BroadcastRing.swift) and streams the
///   16 kHz mono PCM16 on `tlwu/playback_pcm`, the same EventChannel Android uses, so the Dart
///   ASR -> MT pipeline is shared.
/// - Background: while the mode is on, a silent audio engine (playback, mixWithOthers) keeps the
///   app running behind the video app; PiP (SubtitlePiP) shows the subtitles.
final class PhoneAudioBridge: NSObject, FlutterStreamHandler {
  static let pcmChannelName = "tlwu/playback_pcm"

  private var sink: FlutterEventSink?
  private let reader = BroadcastRingReader()
  private var pollTimer: Timer?
  private var broadcasting = false

  private var pendingStart: FlutterResult?
  private var startWatch: Timer?
  private var startDeadline = Date.distantPast
  private var sheetSeen = false
  private var sheetGoneAt: Date?
  private var picker: RPSystemBroadcastPickerView?

  private var keepAlive: AVAudioEngine?
  private lazy var pip = SubtitlePiP()

  init(messenger: FlutterBinaryMessenger) {
    super.init()
    FlutterEventChannel(name: Self.pcmChannelName, binaryMessenger: messenger).setStreamHandler(self)
    let center = CFNotificationCenterGetDarwinNotifyCenter()
    let me = Unmanaged.passUnretained(self).toOpaque()
    CFNotificationCenterAddObserver(
      center, me,
      { _, observer, _, _, _ in
        guard let observer = observer else { return }
        let bridge = Unmanaged<PhoneAudioBridge>.fromOpaque(observer).takeUnretainedValue()
        DispatchQueue.main.async { bridge.onStarted() }
      }, BroadcastShared.started as CFString, nil, .deliverImmediately)
    CFNotificationCenterAddObserver(
      center, me,
      { _, observer, _, _, _ in
        guard let observer = observer else { return }
        let bridge = Unmanaged<PhoneAudioBridge>.fromOpaque(observer).takeUnretainedValue()
        DispatchQueue.main.async { bridge.onFinished() }
      }, BroadcastShared.finished as CFString, nil, .deliverImmediately)
  }

  deinit {
    CFNotificationCenterRemoveEveryObserver(
      CFNotificationCenterGetDarwinNotifyCenter(), Unmanaged.passUnretained(self).toOpaque())
  }

  /// Needs a working App Group (signed build). Min iOS is 15, so PiP with a sample buffer layer exists.
  static var supported: Bool { BroadcastShared.ringURL() != nil }

  // MARK: FlutterStreamHandler

  func onListen(withArguments arguments: Any?, eventSink events: @escaping FlutterEventSink) -> FlutterError? {
    sink = events
    return nil
  }

  func onCancel(withArguments arguments: Any?) -> FlutterError? {
    sink = nil
    return nil
  }

  // MARK: start / stop

  /// Result true once the broadcast runs; false when the user closed the sheet without starting.
  func start(_ result: @escaping FlutterResult) {
    if broadcasting {  // already started (e.g. from Control Center)
      result(true)
      return
    }
    finishStart(false)  // a previous request still waiting
    guard let window = Self.keyWindow() else {
      result(false)
      return
    }
    let p = RPSystemBroadcastPickerView(frame: CGRect(x: 0, y: 0, width: 1, height: 1))
    p.preferredExtension = BroadcastShared.extensionBundleId
    p.showsMicrophoneButton = false
    p.alpha = 0.01
    window.addSubview(p)
    picker = p
    guard let button = p.subviews.compactMap({ $0 as? UIButton }).first else {
      p.removeFromSuperview()
      picker = nil
      result(false)
      return
    }
    pendingStart = result
    sheetSeen = false
    sheetGoneAt = nil
    startDeadline = Date().addingTimeInterval(120)
    button.sendActions(for: .touchUpInside)
    let t = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in self?.watchStart() }
    RunLoop.main.add(t, forMode: .common)
    startWatch = t
  }

  /// The sheet is presented on top of the app; once it is gone without a "started" within a few
  /// seconds (the system counts down 3 s first), the user cancelled.
  private func watchStart() {
    guard pendingStart != nil else { return }
    let presented = Self.keyWindow()?.rootViewController?.presentedViewController != nil
    if presented {
      sheetSeen = true
      sheetGoneAt = nil
    } else if sheetSeen && sheetGoneAt == nil {
      sheetGoneAt = Date()
    }
    if let gone = sheetGoneAt, Date().timeIntervalSince(gone) > 6 {
      finishStart(false)
    } else if Date() > startDeadline {
      finishStart(false)
    }
  }

  private func finishStart(_ ok: Bool) {
    startWatch?.invalidate()
    startWatch = nil
    picker?.removeFromSuperview()
    picker = nil
    if let r = pendingStart {
      pendingStart = nil
      r(ok)
    }
  }

  func stop() {
    BroadcastShared.post(BroadcastShared.stopRequest)
    finishStart(false)
    endCapture()
    overlayHide()
  }

  private func onStarted() {
    broadcasting = true
    reader.open()
    if pollTimer == nil {
      let t = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in self?.poll() }
      RunLoop.main.add(t, forMode: .common)
      pollTimer = t
    }
    finishStart(true)
  }

  private func onFinished() {
    let was = broadcasting
    endCapture()
    if was { sink?(FlutterEndOfEventStream) }
  }

  private func endCapture() {
    broadcasting = false
    pollTimer?.invalidate()
    pollTimer = nil
    reader.close()
  }

  private func poll() {
    guard let data = reader.readNew(), let s = sink else { return }
    s(FlutterStandardTypedData(bytes: data))
  }

  // MARK: overlay (PiP) + background keep-alive

  func overlayShow() {
    startKeepAlive()
    pip.show()
  }

  func overlayHide() {
    pip.hide()
    stopKeepAlive()
  }

  func overlayText(_ args: [String: Any]) {
    let raw = args["lines"] as? [[String: Any]] ?? []
    let lines = raw.map { SubtitlePiP.Line(src: $0["src"] as? String, tr: $0["tr"] as? String) }
    pip.setLines(
      lines, hint: args["hint"] as? String, draft: args["draft"] as? String ?? "",
      draftTr: args["draftTr"] as? String ?? "")
  }

  func overlayStyle(_ args: [String: Any]) {
    if let f = args["fontSize"] as? Double { pip.setFontSize(CGFloat(f)) }
  }

  /// Silent playback so iOS keeps the app (ASR + translation) running while another app plays
  /// the video. mixWithOthers: never pauses or ducks the video's own sound.
  private func startKeepAlive() {
    guard keepAlive == nil else { return }
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
      try session.setActive(true)
      let engine = AVAudioEngine()
      let player = AVAudioPlayerNode()
      let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
      guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16000) else { return }
      buffer.frameLength = 16000  // 1 s of silence
      if let ch = buffer.floatChannelData { ch[0].update(repeating: 0, count: 16000) }
      engine.attach(player)
      engine.connect(player, to: engine.mainMixerNode, format: format)
      try engine.start()
      player.scheduleBuffer(buffer, at: nil, options: .loops)
      player.play()
      keepAlive = engine
    } catch {
      NSLog("tlwu: keep-alive audio failed: \(error)")
    }
  }

  private func stopKeepAlive() {
    keepAlive?.stop()
    keepAlive = nil
  }

  private static func keyWindow() -> UIWindow? {
    let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    let windows = scenes.flatMap { $0.windows }
    return windows.first { $0.isKeyWindow } ?? windows.first
  }
}
