// #1159: streaming downsampler, any input rate -> 16 kHz mono float32. Box filter over each output sample's input
// span (anti-aliasing good enough for speech ASR, which low-passes anyway), carries the fractional position across
// blocks so a long stream neither drifts nor drops samples.
export const TARGET_RATE = 16000;

export class Downsampler {
  constructor(inRate, outRate = TARGET_RATE) {
    if (!(inRate >= outRate)) throw new RangeError(`input rate ${inRate} below ${outRate}`);
    this.step = inRate / outRate; // input samples per output sample
    this.pos = 0; // fractional input index (relative to the pending buffer start) of the next output's span start
    this.pending = new Float32Array(0);
  }

  // mono: Float32Array of input samples. Returns the Float32Array of 16 kHz samples completed by this block.
  push(mono) {
    const buf = new Float32Array(this.pending.length + mono.length);
    buf.set(this.pending);
    buf.set(mono, this.pending.length);
    const n = Math.floor((buf.length - this.pos) / this.step);
    const out = new Float32Array(Math.max(n, 0));
    let pos = this.pos;
    for (let i = 0; i < out.length; i++) {
      const a = Math.floor(pos), b = Math.floor(pos + this.step);
      let s = 0;
      for (let j = a; j < b; j++) s += buf[j];
      out[i] = b > a ? s / (b - a) : buf[a];
      pos += this.step;
    }
    const keep = Math.floor(pos);
    this.pending = buf.slice(keep);
    this.pos = pos - keep;
    return out;
  }
}

// mix a block of channels (array of Float32Array, same length) to mono
export function toMono(channels) {
  if (channels.length === 1) return Float32Array.from(channels[0]);
  const out = new Float32Array(channels[0].length);
  for (const ch of channels) for (let i = 0; i < out.length; i++) out[i] += ch[i];
  for (let i = 0; i < out.length; i++) out[i] /= channels.length;
  return out;
}
