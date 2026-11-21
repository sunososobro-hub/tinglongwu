"""#1182: the captured tab id survives an MV3 service-worker restart (state in chrome.storage.session).

Behaviour test: sw.js runs in node against a fake `chrome`; a "restart" re-imports the module (fresh globals) while the
fake storage.session and offscreen document persist, like in Chrome.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

SW = Path(__file__).resolve().parent.parent / "web" / "extension" / "capture" / "sw.js"

JS = r"""
import { pathToFileURL } from "node:url";
const store = {}, sent = [], badges = [];
let offscreen = true, clicked = null;
globalThis.chrome = {
  storage: { session: {
    get: async (k) => ({ [k]: store[k] }),
    set: async (o) => { Object.assign(store, o); },
    remove: async (k) => { delete store[k]; },
  } },
  offscreen: { hasDocument: async () => offscreen, createDocument: async () => { offscreen = true; } },
  tabCapture: { getMediaStreamId: async () => "sid" },
  action: { setBadgeText: async (o) => { badges.push(o); }, onClicked: { addListener: (f) => { clicked = f; } } },
  tabs: { sendMessage: async (id, m) => { sent.push([id, m]); }, onRemoved: { addListener() {} } },
  runtime: { sendMessage: async () => ({ ok: true }), onMessage: { addListener: (f) => { globalThis.__onMsg = f; } } },
};
const url = pathToFileURL(process.argv[2]).href;
await import(url + "?boot=1");
await globalThis.tlwu.startCapture(7);
// service worker killed and restarted: new module instance, same storage
await import(url + "?boot=2");
globalThis.__onMsg({ target: "sw", type: "line", src: "hi", text: "line" });
await new Promise((r) => setTimeout(r, 20));
const lineSent = sent.filter(([, m]) => m.type === "line");
await clicked({ id: 7 });  // second click on the same tab must stop, not start again
console.log(JSON.stringify({ lineSent, stored: store.capturedTab ?? null,
  stopSent: sent.filter(([, m]) => m.type === "stop").length, badges }));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_captured_tab_survives_service_worker_restart(tmp_path):
    drv = tmp_path / "drv.mjs"
    drv.write_text(JS, "utf-8")
    out = subprocess.run(["node", str(drv), str(SW)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout.strip().splitlines()[-1])
    assert r["lineSent"] == [[7, {"target": "overlay", "type": "line", "src": "hi", "text": "line"}]]
    assert r["stopSent"] == 1
    assert r["stored"] is None
    assert {"tabId": 7, "text": ""} in r["badges"]
