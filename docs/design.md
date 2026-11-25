# Desktop GUI design

Rule: GUI code takes every colour, font size and spacing step from `src/ui/tinglongwu/gui/theme.py`. No raw hex colours in other GUI files (`tests/test_gui_theme.py` enforces it).

## Style (owner decision 2026-11-19, #1645)

- Linear-like calm: dark-first, low saturation, one accent colour, high information density. Chrome stays quiet so the subtitles are the brightest thing on screen.
- Theme follows the system (dark or light); both themes are first-class. The `theme` setting is `auto` by default; `dark` or `light` forces one.
- Accent is indigo-violet (around `#4a63d8`), used only for the primary action, selection and progress. Status colours never reuse it.
- Compact: spacing steps 4 / 8 / 12 / 16 / 24; the main window stays usable at 1366x768.
- No gradients, glass, large radii or decorative shadows.

## Tokens (`TOKENS["dark"]` and `TOKENS["light"]` have the same names)

| token | meaning |
|---|---|
| `bg` | main content background (chat log, notes) |
| `surface` | window and panel background, one step off `bg` |
| `separator` | thin lines between regions |
| `text` | primary text |
| `text_muted` | secondary text, hints, timestamps |
| `accent` | primary action, selection, progress |
| `warning` | something needs attention but works |
| `error` | something failed |
| `success` | ok / listening |
| `grip` | resize grip idle |
| `grip_hover` | resize grip hovered |

Type scale (points): `caption` 10, `body` 11, `title` 12, `heading` 14, `display` 15. The user's subtitle size setting scales the transcript fonts through `small_size` / `bump`.

## Tkinter limits

- No native rounded corners, shadows or blur.
- The native Windows menu bar cannot be themed; the app draws its own in-window menu row.
- The Start button colour comes from the sv_ttk images, not from our tokens.
