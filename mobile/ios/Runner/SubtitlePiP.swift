import AVKit
import UIKit

/// 看影片翻譯 on iOS (#1076): iOS apps cannot draw over other apps, so the subtitles float in a
/// Picture-in-Picture window. Each update renders the lines into a video frame and enqueues it on
/// an AVSampleBufferDisplayLayer that backs the PiP controller. Android twin: SubtitleOverlay.kt.
/// The Dart side already picked the lines for the display mode (中文 / 原文 / 雙語).
final class SubtitlePiP: NSObject, AVPictureInPictureControllerDelegate,
  AVPictureInPictureSampleBufferPlaybackDelegate
{
  struct Line {
    let src: String?
    let tr: String?
  }

  /// Frame size in pixels. Fixed, so the PiP window keeps one aspect ratio (3:1).
  private static let frameSize = CGSize(width: 1200, height: 400)

  private let displayLayer = AVSampleBufferDisplayLayer()
  private var hostView: UIView?
  private var controller: AVPictureInPictureController?
  private var possibleObservation: NSKeyValueObservation?
  private var wantActive = false
  private var refreshTimer: Timer?

  private var lines: [Line] = []
  private var hint: String?
  private var draft = ""
  private var draftTr = ""
  private var fontSize: CGFloat = 20

  static var supported: Bool { AVPictureInPictureController.isPictureInPictureSupported() }

  /// Starts PiP now if the app is in front, otherwise as soon as iOS allows it.
  func show() {
    guard Self.supported else { return }
    wantActive = true
    if controller == nil { setUp() }
    render()
    startIfPossible()
    if refreshTimer == nil {
      // Re-send the last frame now and then so PiP never shows a stale "no video" state.
      let t = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in self?.render() }
      RunLoop.main.add(t, forMode: .common)
      refreshTimer = t
    }
  }

  func hide() {
    wantActive = false
    refreshTimer?.invalidate()
    refreshTimer = nil
    possibleObservation?.invalidate()
    possibleObservation = nil
    controller?.stopPictureInPicture()
    controller = nil
    displayLayer.flush()
    hostView?.removeFromSuperview()
    hostView = nil
  }

  func setLines(_ lines: [Line], hint: String?, draft: String, draftTr: String) {
    self.lines = lines
    self.hint = hint
    self.draft = draft
    self.draftTr = draftTr
    if controller != nil { render() }
  }

  func setFontSize(_ size: CGFloat) {
    fontSize = size
    if controller != nil { render() }
  }

  private func setUp() {
    guard let window = Self.keyWindow() else { return }
    // The source layer must be in the window for PiP to start; keep it tiny and out of the way.
    let host = UIView(frame: CGRect(x: 0, y: 0, width: 3, height: 1))
    host.isUserInteractionEnabled = false
    displayLayer.frame = host.bounds
    displayLayer.videoGravity = .resizeAspect
    host.layer.addSublayer(displayLayer)
    window.addSubview(host)
    hostView = host

    let source = AVPictureInPictureController.ContentSource(
      sampleBufferDisplayLayer: displayLayer, playbackDelegate: self)
    let c = AVPictureInPictureController(contentSource: source)
    c.delegate = self
    c.canStartPictureInPictureAutomaticallyFromInline = true
    c.requiresLinearPlayback = true
    controller = c
    possibleObservation = c.observe(\.isPictureInPicturePossible, options: [.new]) { [weak self] _, _ in
      DispatchQueue.main.async { self?.startIfPossible() }
    }
  }

  private func startIfPossible() {
    guard wantActive, let c = controller, c.isPictureInPicturePossible, !c.isPictureInPictureActive,
      UIApplication.shared.applicationState == .active
    else { return }
    c.startPictureInPicture()
  }

  private static func keyWindow() -> UIWindow? {
    let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
    let windows = scenes.flatMap { $0.windows }
    return windows.first { $0.isKeyWindow } ?? windows.first
  }

  // MARK: rendering

  private func text() -> NSAttributedString {
    let px = fontSize * 2.6
    let para = NSMutableParagraphStyle()
    para.alignment = .center
    para.lineBreakMode = .byWordWrapping
    let srcAttrs: [NSAttributedString.Key: Any] = [
      .font: UIFont.systemFont(ofSize: px * 0.7), .foregroundColor: UIColor(white: 0.75, alpha: 1),
      .paragraphStyle: para,
    ]
    let trAttrs: [NSAttributedString.Key: Any] = [
      .font: UIFont.boldSystemFont(ofSize: px), .foregroundColor: UIColor.white, .paragraphStyle: para,
    ]
    let grayAttrs: [NSAttributedString.Key: Any] = [
      .font: UIFont.systemFont(ofSize: px * 0.8), .foregroundColor: UIColor(white: 0.6, alpha: 1),
      .paragraphStyle: para,
    ]
    let out = NSMutableAttributedString()
    func add(_ s: String, _ a: [NSAttributedString.Key: Any]) {
      if out.length > 0 { out.append(NSAttributedString(string: "\n", attributes: a)) }
      out.append(NSAttributedString(string: s, attributes: a))
    }
    for l in lines {
      if let s = l.src, !s.isEmpty { add(s, srcAttrs) }
      if let t = l.tr, !t.isEmpty { add(t, trAttrs) }
    }
    if !draft.isEmpty { add(draft, grayAttrs) }
    if !draftTr.isEmpty { add(draftTr, grayAttrs) }
    if let h = hint, !h.isEmpty { add(h, grayAttrs) }
    if out.length == 0 { add("聽龍字幕…", grayAttrs) }
    return out
  }

  private func render() {
    let size = Self.frameSize
    let fmt = UIGraphicsImageRendererFormat()
    fmt.scale = 1
    fmt.opaque = true
    let str = text()
    let image = UIGraphicsImageRenderer(size: size, format: fmt).image { ctx in
      UIColor.black.setFill()
      ctx.fill(CGRect(origin: .zero, size: size))
      let width = size.width - 48
      let h = ceil(
        str.boundingRect(
          with: CGSize(width: width, height: .greatestFiniteMagnitude),
          options: [.usesLineFragmentOrigin, .usesFontLeading], context: nil
        ).height)
      // Newest text at the bottom; when too tall the oldest lines are cut at the top.
      let y = h <= size.height - 32 ? (size.height - h) / 2 : size.height - 16 - h
      str.draw(
        with: CGRect(x: 24, y: y, width: width, height: h),
        options: [.usesLineFragmentOrigin, .usesFontLeading], context: nil)
    }
    guard let cg = image.cgImage, let sb = Self.sampleBuffer(from: cg) else { return }
    if displayLayer.status == .failed { displayLayer.flush() }
    displayLayer.enqueue(sb)
  }

  private static func sampleBuffer(from image: CGImage) -> CMSampleBuffer? {
    let w = image.width
    let h = image.height
    var pb: CVPixelBuffer?
    let attrs: [CFString: Any] = [
      kCVPixelBufferCGImageCompatibilityKey: true,
      kCVPixelBufferCGBitmapContextCompatibilityKey: true,
      kCVPixelBufferIOSurfacePropertiesKey: [:] as CFDictionary,
    ]
    guard
      CVPixelBufferCreate(kCFAllocatorDefault, w, h, kCVPixelFormatType_32BGRA, attrs as CFDictionary, &pb)
        == kCVReturnSuccess, let buffer = pb
    else { return nil }
    CVPixelBufferLockBaseAddress(buffer, [])
    let ctx = CGContext(
      data: CVPixelBufferGetBaseAddress(buffer), width: w, height: h, bitsPerComponent: 8,
      bytesPerRow: CVPixelBufferGetBytesPerRow(buffer), space: CGColorSpaceCreateDeviceRGB(),
      bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue)
    ctx?.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
    CVPixelBufferUnlockBaseAddress(buffer, [])
    guard ctx != nil else { return nil }

    var desc: CMVideoFormatDescription?
    guard
      CMVideoFormatDescriptionCreateForImageBuffer(
        allocator: kCFAllocatorDefault, imageBuffer: buffer, formatDescriptionOut: &desc) == noErr,
      let format = desc
    else { return nil }
    var timing = CMSampleTimingInfo(
      duration: .invalid, presentationTimeStamp: CMClockGetTime(CMClockGetHostTimeClock()),
      decodeTimeStamp: .invalid)
    var out: CMSampleBuffer?
    guard
      CMSampleBufferCreateReadyWithImageBuffer(
        allocator: kCFAllocatorDefault, imageBuffer: buffer, formatDescription: format,
        sampleTiming: &timing, sampleBufferOut: &out) == noErr, let sb = out
    else { return nil }
    if let arr = CMSampleBufferGetSampleAttachmentsArray(sb, createIfNecessary: true),
      CFArrayGetCount(arr) > 0
    {
      let dict = unsafeBitCast(CFArrayGetValueAtIndex(arr, 0), to: CFMutableDictionary.self)
      CFDictionarySetValue(
        dict, Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque(),
        Unmanaged.passUnretained(kCFBooleanTrue).toOpaque())
    }
    return sb
  }

  // MARK: AVPictureInPictureSampleBufferPlaybackDelegate (live, never paused, no seeking)

  func pictureInPictureController(
    _ pictureInPictureController: AVPictureInPictureController, setPlaying playing: Bool
  ) {}

  func pictureInPictureControllerTimeRangeForPlayback(
    _ pictureInPictureController: AVPictureInPictureController
  ) -> CMTimeRange {
    CMTimeRange(start: .negativeInfinity, duration: .positiveInfinity)
  }

  func pictureInPictureControllerIsPlaybackPaused(
    _ pictureInPictureController: AVPictureInPictureController
  ) -> Bool { false }

  func pictureInPictureController(
    _ pictureInPictureController: AVPictureInPictureController,
    didTransitionToRenderSize newRenderSize: CMVideoDimensions
  ) {}

  func pictureInPictureController(
    _ pictureInPictureController: AVPictureInPictureController, skipByInterval skipInterval: CMTime,
    completion completionHandler: @escaping () -> Void
  ) { completionHandler() }

  // MARK: AVPictureInPictureControllerDelegate

  func pictureInPictureController(
    _ pictureInPictureController: AVPictureInPictureController,
    restoreUserInterfaceForPictureInPictureStopWithCompletionHandler completionHandler:
      @escaping (Bool) -> Void
  ) { completionHandler(true) }
}
