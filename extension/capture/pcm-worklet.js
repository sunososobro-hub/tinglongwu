// #1159: AudioWorklet on the audio thread. Mixes the tab stream to mono, downsamples to 16 kHz and posts 100 ms
// frames (1600 samples) to the offscreen document. Never blocks the audio thread on the recognizer.
import { Downsampler, TARGET_RATE, toMono } from "./resample.js";

const FRAME = TARGET_RATE / 10;

class PcmFrames extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ds = new Downsampler(sampleRate);
    this.frame = new Float32Array(FRAME);
    this.fill = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (input && input.length) {
      const out = this.ds.push(toMono(input));
      let i = 0;
      while (i < out.length) {
        const n = Math.min(out.length - i, FRAME - this.fill);
        this.frame.set(out.subarray(i, i + n), this.fill);
        this.fill += n;
        i += n;
        if (this.fill === FRAME) {
          this.port.postMessage(this.frame, [this.frame.buffer]);
          this.frame = new Float32Array(FRAME);
          this.fill = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("tlwu-pcm-frames", PcmFrames);

// #1159: pass-through on the speaker path (tab stream -> this -> destination); its peak shows the captured tab audio
// is on the way to the speakers (Chrome mutes a captured tab, so this playback is what keeps the tab audible).
class PlaybackMeter extends AudioWorkletProcessor {
  constructor() {
    super();
    this.peak = 0;
    this.blocks = 0;
  }

  process(inputs, outputs) {
    const input = inputs[0], output = outputs[0];
    for (let c = 0; c < output.length; c++) {
      const src = input[c] || input[0];
      if (!src) continue;
      output[c].set(src);
      for (let i = 0; i < src.length; i++) if (Math.abs(src[i]) > this.peak) this.peak = Math.abs(src[i]);
    }
    if (++this.blocks % 40 === 0) this.port.postMessage({ peak: this.peak }); // ~ every 100 ms at 48 kHz
    return true;
  }
}

registerProcessor("tlwu-playback-meter", PlaybackMeter);
