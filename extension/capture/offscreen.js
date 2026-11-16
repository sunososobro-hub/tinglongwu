// #1159: offscreen document. Opens the tab-capture stream, plays it back so the tab stays audible (Chrome mutes a
// captured tab otherwise), and feeds 16 kHz mono frames to the recognizer hook (recognizer.js).
import { deliver, getRecognizer } from "./recognizer.js";

let session = null;

// #1160: the recognizer calls emitLine({src, text}) for every recognized + translated line
export function emitLine(l) {
  return chrome.runtime.sendMessage({ target: "sw", type: "line", src: l.src || "", text: String(l.text) });
}
globalThis.tlwuEmitLine = emitLine;

async function start(streamId) {
  if (session) await stop();
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule("pcm-worklet.js");
  const src = ctx.createMediaStreamSource(stream);
  const frames = new AudioWorkletNode(ctx, "tlwu-pcm-frames", { numberOfOutputs: 0 });
  frames.port.onmessage = (e) => deliver(e.data);
  // what the user hears: the tab audio routed back to the speakers through a pass-through meter
  const speaker = new AudioWorkletNode(ctx, "tlwu-playback-meter", { outputChannelCount: [2] });
  src.connect(frames);
  src.connect(speaker);
  speaker.connect(ctx.destination);
  const rec = getRecognizer();
  if (rec.reset) rec.reset();
  session = { stream, ctx, playbackPeak: 0, inputRate: ctx.sampleRate };
  speaker.port.onmessage = (e) => { if (session) session.playbackPeak = e.data.peak; };
  if (ctx.state !== "running") await ctx.resume();
  return { ok: true, inputRate: ctx.sampleRate, contextState: ctx.state };
}

async function stop() {
  if (!session) return { ok: true };
  session.stream.getTracks().forEach((t) => t.stop());
  await session.ctx.close();
  session = null;
  return { ok: true };
}

function stats() {
  const rec = getRecognizer();
  return {
    ok: true,
    capturing: !!session,
    inputRate: session ? session.inputRate : null,
    contextState: session ? session.ctx.state : null,
    playbackPeak: session ? session.playbackPeak : 0,
    recognizer: rec.stats ? { ...rec.stats } : null,
  };
}

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (!msg || msg.target !== "offscreen") return false;
  const run = { start: () => start(msg.streamId), stop, stats: async () => stats(),
    emit: async () => { for (const l of msg.lines || []) await emitLine(l); return { ok: true }; } }[msg.type];
  if (!run) return false;
  run().then(reply, (e) => reply({ ok: false, error: String(e && e.message || e) }));
  return true; // async reply
});
