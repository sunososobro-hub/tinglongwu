"""#1809: plain / technical wording for the setup app (`tlwu_app`). Plain is the default; the "Show technical details"
setting (or `--tech-labels`) shows the old technical text. One key set, so the two views never drift. Pure logic.

Languages: the setup app ships zh-TW and en (zh-TW unless the UI language is explicitly `en`). Labels only: no
behaviour, default or saved value changes with the switch.
"""
from __future__ import annotations

import os

LANGS = ("zh-TW", "en")
VARIANTS = ("plain", "tech")
_TECH = {"flag": False}

# key -> {lang: (plain, tech)}. `tech` is the text this app showed before #1809.
TEXT: dict[str, dict[str, tuple[str, str]]] = {
    "tier.gpu": {
        "zh-TW": ("最準確（用顯示晶片加速，聽得最準）", "GPU：最準確（Whisper large-v3-turbo + Hy-MT2 翻譯）"),
        "en": ("Most accurate (uses your graphics chip to go faster)",
               "GPU: most accurate (Whisper large-v3-turbo + Hy-MT2 translation)")},
    "tier.gpu-min": {
        "zh-TW": ("精簡（較小的聽寫模型，翻譯用一般速度，顯示晶片記憶體不夠）",
                  "精簡（Whisper small + Hy-MT2 翻譯在 CPU 執行，顯示卡記憶體不足 4 GB）"),
        "en": ("Compact (lighter listening model; your graphics chip has too little memory, so normal speed)",
               "Compact (Whisper small + Hy-MT2 translation on the CPU, GPU memory under 4 GB)")},
    "tier.cpu": {
        "zh-TW": ("一般速度（較小的聽寫模型，較慢但不需要顯示晶片）",
                  "CPU 模式（Whisper small + Hy-MT2 翻譯，較慢但不需顯示卡）"),
        "en": ("Normal speed (lighter listening model, slower but needs no graphics chip)",
               "CPU mode (Whisper small + Hy-MT2 translation, slower but no GPU needed)")},
    "detect.force_cpu": {
        "zh-TW": ("已指定一般速度。", "已指定 CPU 模式。"),
        "en": ("Normal speed selected.", "CPU mode selected.")},
    "detect.gpu": {
        "zh-TW": ("偵測到顯示晶片：{name}（可以加速）", "偵測到顯示卡：{name}（{gb} GB 顯存）"),
        "en": ("Graphics chip found: {name} (can speed things up)", "GPU found: {name} ({gb} GB VRAM)")},
    "detect.vulkan": {
        "zh-TW": ("偵測到顯示晶片：{names}。要加速可在設定 > 附加元件安裝加速套件，目前先用一般速度。",
                  "偵測到 Vulkan 顯示卡：{names}（沒有 NVIDIA 顯示卡；Vulkan 加速包可在設定 > 附加元件安裝），先使用 CPU 模式。"),
        "en": ("Graphics chip found: {names}. To speed things up, install the speed-up add-on in Settings > Add-ons; "
               "normal speed for now.",
               "Vulkan GPU found: {names} (no NVIDIA GPU; the Vulkan pack can be installed in Settings > Add-ons), "
               "CPU mode for now.")},
    "detect.none": {
        "zh-TW": ("沒有偵測到可加速的顯示晶片，使用一般速度（較慢，但一樣能用）。",
                  "沒有偵測到 NVIDIA 顯示卡，將使用 CPU 模式（速度較慢，但一樣能用）。"),
        "en": ("No graphics chip that can speed things up was found; normal speed (slower, but it works).",
               "No NVIDIA GPU found; CPU mode (slower, but it works).")},
    "driver.old": {
        "zh-TW": ("偵測到顯示晶片 {name}，但它的驅動程式 {driver} 太舊，先用一般速度，翻譯照常可用。"
                  "更新驅動程式後重新設定即可加速：{url}",
                  "偵測到顯示卡 {name}，但 NVIDIA 驅動程式 {driver} 太舊（CUDA 12 需要 {need} 以上），"
                  "先使用 CPU 模式。更新驅動程式後重新設定即可使用 GPU：{url}\n"
                  "NVIDIA driver {driver} is too old for CUDA 12 (needs {need}+): CPU mode for now. "
                  "Update the driver to use the GPU."),
        "en": ("Graphics chip {name} found, but its driver {driver} is too old; normal speed for now, translation "
               "still works. Update the driver and run setup again to go faster: {url}",
               "NVIDIA driver {driver} is too old for CUDA 12 (needs {need}+): CPU mode for now. "
               "Update the driver to use the GPU: {url}")},
    "ask.title": {"zh-TW": ("加速套件", "GPU 加速包"), "en": ("Speed-up download", "GPU pack")},
    "ask.body": {
        "zh-TW": ("你的電腦可以更快。要現在下載加速套件嗎？\n\n大小約 1.5 GB，只需下載一次；"
                  "依網路速度約需 3–15 分鐘。裝好後翻譯會快很多。\n\n選「否」＝稍後再說：之後可在主畫面上方的提示，"
                  "或「設定 > 附加元件」一鍵安裝。",
                  "偵測到 NVIDIA 顯示卡。要現在下載 GPU 加速包嗎？\n\n大小約 1.5 GB，只需下載一次；"
                  "依網路速度約需 3–15 分鐘。裝好後翻譯會快很多。\n\n選「否」＝稍後再說：之後可在主畫面上方的提示，"
                  "或「設定 > 附加元件」一鍵安裝。"),
        "en": ("Your computer can go faster. Download the speed-up add-on now?\n\nAbout 1.5 GB, only once; roughly "
               "3-15 minutes depending on your connection. Translation is much quicker afterwards.\n\nChoose No to "
               "decide later: the banner at the top of the main window or Settings > Add-ons installs it in one click.",
               "An NVIDIA GPU was found. Download the GPU pack now?\n\nAbout 1.5 GB, only once; roughly 3-15 minutes "
               "depending on your connection. Translation is much faster afterwards.\n\nChoose No to decide later: "
               "the banner at the top of the main window or Settings > Add-ons installs it in one click.")},
    "ask.later": {
        "zh-TW": ("已略過加速套件（目前用一般速度）；之後可在主畫面或「設定 > 附加元件」一鍵安裝。",
                  "已略過 GPU 加速包（目前用 CPU）；之後可在主畫面或「設定 > 附加元件」一鍵安裝。"),
        "en": ("Speed-up add-on skipped (normal speed for now); install it later from the main window or "
               "Settings > Add-ons.",
               "GPU pack skipped (CPU for now); install it later from the main window or Settings > Add-ons.")},
    "pack.ok": {"zh-TW": ("加速套件已安裝。", "GPU 加速包已安裝。"), "en": ("Speed-up add-on installed.", "GPU pack installed.")},
    "pack.none": {
        "zh-TW": ("這個版本沒有加速套件，先用一般速度（可稍後在「設定 > 附加元件」安裝）。",
                  "這個版本沒有 GPU 加速包，改用 CPU（可稍後在「設定 > 附加元件」安裝）。"),
        "en": ("This version has no speed-up add-on; normal speed (you can look in Settings > Add-ons later).",
               "This version has no GPU pack; using the CPU (check Settings > Add-ons later).")},
    "pack.fail": {
        "zh-TW": ("加速套件沒裝成功（需要網路），先用一般速度，翻譯照常可用；可稍後在「設定 > 附加元件」再試一次。",
                  "GPU 加速包沒有安裝成功（需要網路），目前改用 CPU；可稍後在「設定 > 附加元件」再試一次。"),
        "en": ("The speed-up add-on did not install (needs internet); normal speed, translation still works. Try "
               "again later in Settings > Add-ons.",
               "The GPU pack did not install (needs internet); using the CPU for now. Try again later in "
               "Settings > Add-ons.")},
    "pack.installing": {
        "zh-TW": ("安裝加速套件（需連網一次）…", "安裝 GPU 加速包（NVIDIA，需連網一次）…"),
        "en": ("Installing the speed-up add-on (needs internet once)...",
               "Installing the GPU pack (NVIDIA, needs internet once)...")},
    "cpu.checkbox": {
        "zh-TW": ("使用一般速度（下載較少，但翻譯較慢）", "改用 CPU 模式（下載較少，但翻譯較慢）"),
        "en": ("Use normal speed (lighter download, slower translation)",
               "Use CPU mode (lighter download, slower translation)")},
    "fallback": {
        "zh-TW": ("加速沒有成功，先用一般速度，翻譯照常可用：{reason}", "GPU 無法使用，已改用 CPU：{reason}"),
        "en": ("Speed-up did not work; normal speed, translation still works: {reason}",
               "GPU unavailable, switched to the CPU: {reason}")},
}


def set_tech(flag: bool) -> None:
    """The "Show technical details" setting or the `--tech-labels` launch flag."""
    _TECH["flag"] = bool(flag)


def tech() -> bool:
    if _TECH["flag"]:
        return True
    try:  # the GUI setting, saved by the main window; a missing / broken file means plain
        from tinglongwu.gui import core

        return bool(core.load_settings().get("tech_labels"))
    except Exception:  # noqa: BLE001 - wording never blocks setup
        return False


def lang() -> str:
    """zh-TW (what this app showed so far) unless the UI language is explicitly en (env or saved setting)."""
    v = os.environ.get("TLWU_UI_LANG", "").strip().lower()
    if not v or v == "auto":
        try:
            from tinglongwu import userdirs

            v = str(userdirs.read_config().get("ui_lang", "")).strip().lower()
        except Exception:  # noqa: BLE001
            v = ""
    return "en" if v.startswith("en") else "zh-TW"


def text(key: str, *, tech_view: bool | None = None, language: str | None = None, **kw) -> str:
    plain, technical = TEXT[key][language or lang()]
    s = technical if (tech() if tech_view is None else tech_view) else plain
    return s.format(**kw) if kw else s


def fallback_display(line: str, tech_view: bool | None = None) -> str:
    """The self-test's "GPU 無法使用，已改用 CPU：<reason>" line (a protocol line between processes, left as is) as
    shown in the window: plain wording unless technical details are on."""
    if not line or (tech() if tech_view is None else tech_view):
        return line
    return text("fallback", tech_view=False, reason=line.split("：", 1)[-1])
