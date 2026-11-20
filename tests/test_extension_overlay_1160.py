"""#1160 (Chrome extension part 3, parent #711): the subtitle overlay on the captured tab.

Loads web/extension/capture in Playwright's Chromium (a copy whose content-script match also covers the local test page),
starts capture, injects 3 fake recognizer results through the offscreen document -> service worker -> content script
path, and checks: each translated line shows up in order within 500 ms, the overlay survives a page scroll, and it is
removed when capture stops. Skips when Playwright / Chromium are missing, unless TLWU_REQUIRE_EXTENSION=1 (then fails).
"""
from __future__ import annotations

import json
import shutil
import time

import pytest

from tests.test_extension_capture_1159 import EXT, QuietHandler, _launch, _need, _tone_wav  # noqa: F401
import functools
import http.server
import threading


@pytest.fixture()
def tall_page(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    _tone_wav(site / "tone.wav")
    (site / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>meeting</title><body style="margin:0">'
        '<audio id="a" src="tone.wav" preload="auto"></audio><div style="height:4000px">meeting</div>',
        encoding="utf-8")
    handler = functools.partial(QuietHandler, directory=str(site))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}/index.html"
    finally:
        srv.shutdown()


LINES = [("Hello everyone", "大家好"), ("Let's start", "我們開始吧"), ("Any questions?", "有問題嗎？")]


def test_overlay_shows_three_lines_in_order_survives_scroll_and_goes_away(tmp_path, tall_page):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _need("playwright is not installed")
    ext = tmp_path / "ext"
    shutil.copytree(EXT, ext)
    mp = ext / "manifest.json"
    m = json.loads(mp.read_text("utf-8"))
    m["content_scripts"][0]["matches"].append("http://127.0.0.1/*")  # test-only: the page is not a meeting site
    mp.write_text(json.dumps(m), encoding="utf-8")
    with sync_playwright() as p:
        ctx, sw = _launch(p, tmp_path / "profile", ext.resolve())
        try:
            page = ctx.new_page()
            page.goto(tall_page)
            page.wait_for_function("document.getElementById('a').readyState >= 4")
            assert sw.evaluate("""async () => {
                const [t] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
                return (await tlwu.startCapture(t.id)).ok;
            }""") is True
            assert page.locator("#tlwu-overlay-host").count() == 0  # nothing before the first line
            times = []
            for i, (src, text) in enumerate(LINES):
                t0 = time.monotonic()
                sw.evaluate("""async (l) => { await chrome.runtime.sendMessage(
                    {target: "offscreen", type: "emit", lines: [l]}); }""", {"src": src, "text": text})
                page.wait_for_function(
                    "n => document.querySelector('#tlwu-overlay-host').shadowRoot.querySelectorAll('.text').length >= n",
                    arg=i + 1, timeout=500, polling=10)
                times.append(time.monotonic() - t0)
            shown = page.evaluate("[...document.querySelector('#tlwu-overlay-host').shadowRoot"
                                  ".querySelectorAll('.text')].map(e => e.textContent)")
            srcs = page.evaluate("[...document.querySelector('#tlwu-overlay-host').shadowRoot"
                                 ".querySelectorAll('.src')].map(e => e.textContent)")
            before = page.evaluate("JSON.stringify(document.querySelector('#tlwu-overlay-host').getBoundingClientRect())")
            page.evaluate("window.scrollTo(0, 1500)")
            page.wait_for_function("window.scrollY >= 1500")
            after = page.evaluate("JSON.stringify(document.querySelector('#tlwu-overlay-host').getBoundingClientRect())")
            still_there = page.locator("#tlwu-overlay-host").count()
            sw.evaluate("() => tlwu.stopCapture()")
            page.wait_for_function("!document.querySelector('#tlwu-overlay-host')", timeout=2000)
        finally:
            ctx.close()
    assert shown == [t for _, t in LINES], shown
    assert srcs == [s for s, _ in LINES], srcs
    assert all(t < 0.5 for t in times), times
    assert before == after and still_there == 1  # position: fixed, visible in the viewport after scrolling
