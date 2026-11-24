"""Colour tokens for the desktop GUI (#1820). The GUI reads colours only from here (plus the speaker palette in
core.py); no raw hex anywhere else. Every token exists in both themes with the same names."""

# semantic tokens per theme: one warning, one error, one muted per theme
TOKENS = {
    "dark": {"bg": "#0e1015", "surface": "#14161c", "separator": "#262a34", "text": "#e6e8ee", "text_muted": "#8a90a0",
             "accent": "#3f5fe0", "warning": "#d29b3c", "error": "#e5534b", "success": "#2ea043",
             "grip": "#555555", "grip_hover": "#2e86de"},
    "light": {"bg": "#ffffff", "surface": "#f6f7fa", "separator": "#dfe2ea", "text": "#1d2447", "text_muted": "#5b6390",
              "accent": "#3f5fe0", "warning": "#a85400", "error": "#c0392b", "success": "#2ea043",
              "grip": "#555555", "grip_hover": "#2e86de"},
}
GRIP, GRIP_HOVER = TOKENS["dark"]["grip"], TOKENS["dark"]["grip_hover"]
WARNING_LIGHT = TOKENS["light"]["warning"]
SIDE_ACTIVE_FG = "#ffffff"  # text on the accent pill / active menu entry
BAR_BTN, BAR_BTN_ACTIVE = "#2a2a2a", "#3a3a3a"  # floating-subtitle bar buttons


def _pal(t):
    return {"win": t["surface"], "bg": t["bg"], "sep": t["separator"], "fg": t["text"], "muted": t["text_muted"],
            "accent": t["accent"], "warn": t["warning"], "err": t["error"]}


# #1662 Linear-style palette (see TOKENS); PAL keeps the short keys the GUI code uses
PAL = {k: _pal(v) for k, v in TOKENS.items()}
BUBBLE = {"dark": {"bg": "#0e1015", "other": ("#1a1d27", "#e6e8ee"), "me": ("#3f5fe0", "#ffffff"),
                   "latest": ("#2a3560", "#ffffff"), "sub": "#8a90a0", "head": "#8a90a0"},
          "light": {"bg": "#ffffff", "other": ("#e6e9f5", "#1d2447"), "me": ("#cfe0ff", "#10224d"),
                    "latest": ("#cdd6ff", "#10224d"), "sub": "#5b6390", "head": "#5b6390"}}
# #1644: notes (重點整理) window per theme: time range muted, bullets in the notes accent (violet)
NOTES = {"dark": {"bg": "#0e1015", "fg": "#e6e8ee", "title": "#ffffff", "when": "#8a90a0", "mark": "#b79cff"},
         "light": {"bg": "#ffffff", "fg": "#1d2447", "title": "#10224d", "when": "#5b6390", "mark": "#6b4fd8"}}
FG_FINAL, FG_DRAFT, FG_SRC, BG = "#ffffff", "#8a8a8a", "#b8c4d6", "#101010"
SIDE = {"dark": {"top": "#1d2447", "bottom": "#2c2350", "fg": "#eef0ff", "sub": "#a9b1d6", "pill": "#4a63d8",
                 "wave": "#3a3f72"},
        "light": {"top": "#e9eeff", "bottom": "#e3dcfa", "fg": "#1d2447", "sub": "#5b6390", "pill": "#4a63d8",
                  "wave": "#cfd3f3"}}
# #455: control-bar status dot: idle / models loading / listening (fully offline) / stopped on an error
DOT = {"idle": "#8a8a8a", "loading": "#e0a000", "on": "#2ea043", "error": "#d0453a"}
