import CoreMedia
import ReplayKit

/// 看影片翻譯 on iOS (#1076): ReplayKit broadcast upload extension. Receives other apps' audio
/// (`.audioApp`), mixes it to mono, downsamples to 16 kHz PCM16 and writes it to the App Group
/// ring (BroadcastRing.swift). No ASR here: the extension has a ~50 MB memory limit, the app
/// does the recognition and translation. Video frames are ignored and nothing is stored.
final class SampleHandler: RPBroadcastSampleHandler {
  private var writer: BroadcastRingWriter?
  private let resampler = MonoResampler(outRate: 16000)
  private var observing = false
  /// processSampleBuffer runs on a ReplayKit queue, the stop request on the main thread.
  private let lock = NSLock()

  override func broadcastStarted(withSetupInfo setupInfo: [String: NSObject]?) {
    guard let w = BroadcastRingWriter() else {
      finishBroadcastWithError(
        Self.error("聽龍無法開啟共用資料夾（App Group），請重新安裝聽龍。"))
      return
    }
    lock.lock()
    writer = w
    lock.unlock()
    let center = CFNotificationCenterGetDarwinNotifyCenter()
    CFNotificationCenterAddObserver(
      center, Unmanaged.passUnretained(self).toOpaque(),
      { _, observer, _, _, _ in
        guard let observer = observer else { return }
        Unmanaged<SampleHandler>.fromOpaque(observer).takeUnretainedValue().stopRequested()
      },
      BroadcastShared.stopRequest as CFString, nil, .deliverImmediately)
    observing = true
    BroadcastShared.post(BroadcastShared.started)
  }

  override func broadcastFinished() {
    cleanUp()
  }

  override func processSampleBuffer(_ sampleBuffer: CMSampleBuffer, with sampleBufferType: RPSampleBufferType) {
    guard sampleBufferType == .audioApp else { return }
    lock.lock()
    defer { lock.unlock() }
    guard let w = writer else { return }
    if let pcm = resampler.convert(sampleBuffer), !pcm.isEmpty {
      w.write(pcm)
    }
  }

  /// The app switched back to 說話翻譯 (or closed the mode): end the broadcast.
  private func stopRequested() {
    cleanUp()
    finishBroadcastWithError(Self.error("聽龍已停止看影片翻譯。"))
  }

  private func cleanUp() {
    if observing {
      CFNotificationCenterRemoveEveryObserver(
        CFNotificationCenterGetDarwinNotifyCenter(), Unmanaged.passUnretained(self).toOpaque())
      observing = false
    }
    lock.lock()
    let w = writer
    writer = nil
    lock.unlock()
    if let w = w {
      w.close()
      BroadcastShared.post(BroadcastShared.finished)
    }
  }

  private static func error(_ text: String) -> NSError {
    NSError(
      domain: BroadcastShared.extensionBundleId, code: 1,
      userInfo: [NSLocalizedDescriptionKey: text])
  }
}

/// Any linear-PCM layout ReplayKit delivers (Int16 / Int32 / Float32, either endianness,
/// interleaved or not, any channel count and rate) -> mono 16 kHz PCM16 little-endian.
/// Downsampling = box filter (mean of the input samples in each output period), carried across
/// buffers so chunk boundaries do not click.
final class MonoResampler {
  private let outRate: Double
  private var phase = 0.0
  private var acc: Float = 0
  private var count = 0

  init(outRate: Double) {
    self.outRate = outRate
  }

  func convert(_ sb: CMSampleBuffer) -> Data? {
    guard let fmt = CMSampleBufferGetFormatDescription(sb),
      let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
    else { return nil }
    let asbd = asbdPtr.pointee
    guard asbd.mFormatID == kAudioFormatLinearPCM, asbd.mSampleRate > 0 else { return nil }
    let flags = asbd.mFormatFlags
    let isFloat = flags & kAudioFormatFlagIsFloat != 0
    let bigEndian = flags & kAudioFormatFlagIsBigEndian != 0
    let nonInterleaved = flags & kAudioFormatFlagIsNonInterleaved != 0
    let bits = Int(asbd.mBitsPerChannel)
    guard (isFloat && bits == 32) || (!isFloat && (bits == 16 || bits == 32)) else { return nil }
    let bytesPerSample = bits / 8
    let channels = max(1, Int(asbd.mChannelsPerFrame))

    var sizeNeeded = 0
    var status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
      sb, bufferListSizeNeededOut: &sizeNeeded, bufferListOut: nil, bufferListSize: 0,
      blockBufferAllocator: nil, blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: nil)
    guard status == noErr, sizeNeeded > 0 else { return nil }
    let raw = UnsafeMutableRawPointer.allocate(
      byteCount: sizeNeeded, alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    let abl = raw.bindMemory(to: AudioBufferList.self, capacity: 1)
    var block: CMBlockBuffer?
    status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
      sb, bufferListSizeNeededOut: nil, bufferListOut: abl, bufferListSize: sizeNeeded,
      blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
      flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, blockBufferOut: &block)
    guard status == noErr else { return nil }
    // The block buffer owns the audio bytes: keep it alive until we are done reading.
    defer { withExtendedLifetime(block) {} }
    let buffers = UnsafeMutableAudioBufferListPointer(abl)
    guard buffers.count > 0 else { return nil }

    // Frames actually present in every buffer.
    var frames = Int.max
    if nonInterleaved {
      for b in buffers { frames = min(frames, Int(b.mDataByteSize) / bytesPerSample) }
    } else {
      frames = Int(buffers[0].mDataByteSize) / (bytesPerSample * channels)
    }
    frames = min(frames, CMSampleBufferGetNumSamples(sb))
    guard frames > 0, frames != Int.max else { return nil }

    func sample(_ p: UnsafeMutableRawPointer, _ index: Int) -> Float {
      let off = index * bytesPerSample
      if isFloat {
        var u = p.loadUnaligned(fromByteOffset: off, as: UInt32.self)
        if bigEndian { u = u.byteSwapped }
        return Float(bitPattern: u)
      }
      if bits == 16 {
        var u = p.loadUnaligned(fromByteOffset: off, as: UInt16.self)
        if bigEndian { u = u.byteSwapped }
        return Float(Int16(bitPattern: u)) / 32768
      }
      var u = p.loadUnaligned(fromByteOffset: off, as: UInt32.self)
      if bigEndian { u = u.byteSwapped }
      return Float(Int32(bitPattern: u)) / 2_147_483_648
    }

    var ptrs: [UnsafeMutableRawPointer] = []
    for b in buffers {
      guard let p = b.mData else { return nil }
      ptrs.append(p)
    }
    let usedChannels = nonInterleaved ? min(channels, ptrs.count) : channels
    let step = asbd.mSampleRate / outRate
    var out = Data(capacity: Int(Double(frames) / step) * 2 + 4)
    for i in 0..<frames {
      var s: Float = 0
      for ch in 0..<usedChannels {
        s += nonInterleaved ? sample(ptrs[ch], i) : sample(ptrs[0], i * channels + ch)
      }
      acc += s / Float(usedChannels)
      count += 1
      phase += 1
      if phase >= step {
        phase -= step
        let m = acc / Float(count)
        let v = m.isFinite ? max(-1, min(1, m)) : 0
        acc = 0
        count = 0
        var le = Int16(v * 32767).littleEndian
        withUnsafeBytes(of: &le) { out.append(contentsOf: $0) }
      }
    }
    return out
  }
}
