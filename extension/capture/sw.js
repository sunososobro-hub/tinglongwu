// #1159: service worker. Action click on a tab -> tab-capture stream id -> offscreen document (audio lives there,
// a service worker has no Web Audio). Second click on the same tab stops. No network, no host permissions.
const OFFSCREEN = "offscreen.html";
let capturedTab = null;

async function ensureOffscreen() {
  if (await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN,
    reasons: ["USER_MEDIA"],
    justification: "Turn the tab's audio into 16 kHz frames for on-device speech recognition",
  });
}

export async function startCapture(tabId) {
  await ensureOffscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  const r = await chrome.runtime.sendMessage({ target: "offscreen", type: "start", streamId });
  if (!r || !r.ok) throw new Error("offscreen start failed: " + (r && r.error));
  capturedTab = tabId;
  await chrome.action.setBadgeText({ tabId, text: "ON" });
  return r;
}

async function toTab(msg) {
  if (capturedTab === null) return;
  try { await chrome.tabs.sendMessage(capturedTab, { target: "overlay", ...msg }); } catch (_) { /* tab has no overlay (not a meeting site) */ }
}

export async function stopCapture() {
  let r = { ok: true };
  await toTab({ type: "stop" });
  if (await chrome.offscreen.hasDocument()) r = await chrome.runtime.sendMessage({ target: "offscreen", type: "stop" });
  if (capturedTab !== null) await chrome.action.setBadgeText({ tabId: capturedTab, text: "" });
  capturedTab = null;
  return r;
}

export async function captureStats() {
  if (!(await chrome.offscreen.hasDocument())) return null;
  return chrome.runtime.sendMessage({ target: "offscreen", type: "stats" });
}

chrome.action.onClicked.addListener(async (tab) => {
  try {
    if (capturedTab === tab.id) await stopCapture();
    else {
      if (capturedTab !== null) await stopCapture();
      await startCapture(tab.id);
    }
  } catch (e) {
    console.error("tlwu capture:", e);
    await chrome.action.setBadgeText({ tabId: tab.id, text: "ERR" });
  }
});

// #1160: recognized + translated lines from the offscreen document go to the captured tab's overlay
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.target === "sw" && msg.type === "line") toTab({ type: "line", src: msg.src, text: msg.text });
  return false;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabId === capturedTab) stopCapture();
});

// test / devtools handle (the service worker is a module, so its exports are not globals)
globalThis.tlwu = { startCapture, stopCapture, captureStats };
