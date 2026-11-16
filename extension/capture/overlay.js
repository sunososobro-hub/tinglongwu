// #1160: subtitle overlay, injected as a content script into the meeting tab (see manifest "content_scripts").
// Inert until the service worker sends {target:"overlay", type:"line"|"stop"}. Everything it shows arrives from the
// offscreen document through the service worker; it makes no network request and reads nothing from the page.
(() => {
  if (globalThis.__tlwuOverlay) return;
  const MAX_LINES = 3;
  let host = null, list = null;

  function build() {
    host = document.createElement("div");
    host.id = "tlwu-overlay-host";
    host.style.cssText = "all:initial;position:fixed;left:10%;bottom:8%;width:80%;max-width:960px;z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" }); // open: tests (and devtools) can look inside
    const style = document.createElement("style");
    style.textContent = `
      .box{font-family:system-ui,sans-serif;background:rgba(0,0,0,.72);color:#fff;border-radius:10px;padding:6px 14px 10px;}
      .grip{height:14px;cursor:grab;text-align:center;line-height:10px;color:#aaa;user-select:none;touch-action:none}
      .line{margin-top:4px}.src{font-size:13px;color:#bbb}.text{font-size:24px;font-weight:600;line-height:1.3}`;
    const box = document.createElement("div");
    box.className = "box";
    const grip = document.createElement("div");
    grip.className = "grip";
    grip.textContent = "⋯";
    list = document.createElement("div");
    list.className = "lines";
    box.append(grip, list);
    root.append(style, box);
    grip.addEventListener("pointerdown", (ev) => {
      const r = host.getBoundingClientRect();
      const dx = ev.clientX - r.left, dy = ev.clientY - r.top;
      grip.setPointerCapture(ev.pointerId);
      const move = (e) => {
        host.style.left = Math.max(0, e.clientX - dx) + "px";
        host.style.top = Math.max(0, e.clientY - dy) + "px";
        host.style.bottom = "auto";
      };
      const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
    });
    (document.body || document.documentElement).appendChild(host);
  }

  function line(src, text) {
    if (!host || !host.isConnected) build();
    const el = document.createElement("div");
    el.className = "line";
    if (src) {
      const s = document.createElement("div");
      s.className = "src";
      s.textContent = src; // textContent only: recognized text is never parsed as HTML
      el.append(s);
    }
    const t = document.createElement("div");
    t.className = "text";
    t.textContent = text;
    el.append(t);
    list.append(el);
    while (list.children.length > MAX_LINES) list.firstElementChild.remove();
  }

  function stop() {
    if (host) host.remove();
    host = list = null;
  }

  globalThis.__tlwuOverlay = { line, stop };
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.target !== "overlay") return;
    if (msg.type === "line" && typeof msg.text === "string") line(typeof msg.src === "string" ? msg.src : "", msg.text);
    else if (msg.type === "stop") stop();
  });
})();
