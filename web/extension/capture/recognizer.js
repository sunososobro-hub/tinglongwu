// #1159: the pluggable recognizer hook. The offscreen document calls `onFrame(frame)` with every 16 kHz mono
// Float32Array frame (100 ms) of tab audio, in order. Part 1 (#1158) chose whisper-small + opus-mt; the real
// recognizer replaces `defaultRecognizer` through setRecognizer() and must not block (queue + worker).
export const SAMPLE_RATE = 16000;

// Default: counts what arrives, so tests and the action badge can see the stream without a model.
export function statsRecognizer() {
  const st = { frames: 0, samples: 0, voicedSamples: 0, zeroCrossings: 0, peak: 0, last: 0 };
  return {
    stats: st,
    onFrame(frame) {
      st.frames += 1;
      st.samples += frame.length;
      for (let i = 0; i < frame.length; i += 160) { // 10 ms blocks
        const end = Math.min(i + 160, frame.length);
        let e = 0;
        for (let j = i; j < end; j++) {
          const x = frame[j];
          e += x * x;
          if (Math.abs(x) > st.peak) st.peak = Math.abs(x);
          if ((x >= 0) !== (st.last >= 0)) st.zeroCrossings += 1;
          st.last = x;
        }
        if (Math.sqrt(e / (end - i)) > 0.01) st.voicedSamples += end - i;
      }
    },
    reset() { Object.assign(st, { frames: 0, samples: 0, voicedSamples: 0, zeroCrossings: 0, peak: 0, last: 0 }); },
  };
}

let current = statsRecognizer();
const listeners = new Set();

export function setRecognizer(r) { current = r; }
export function getRecognizer() { return current; }
export function onFrame(cb) { listeners.add(cb); return () => listeners.delete(cb); }

export function deliver(frame) {
  current.onFrame(frame);
  for (const cb of listeners) cb(frame);
}
