"""Runtime messages (network guard, report prompts) in the UI language. Adding a language = adding a dict here
(and scripts/i18n/<lang>.sh / .psd1 for the menus). UI language: env TLWU_UI_LANG > config ui_lang > system > en.
Only UI text is translated; subtitle/translation target languages are unrelated to this module."""
from __future__ import annotations

import os
import re
import subprocess
import sys

from . import userdirs

DEFAULT = "en"

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "guard_blocked": "\U0001F6E1 tlwu blocked an outgoing connection: {host}:{port} ({pkg}). "
                         "tlwu does not need the network in normal use; you can report this from the menu next time.",
        "report_status": "{n} outgoing connection attempt(s) were blocked since the last report: {hosts}.",
        "report_preview": "This is exactly what would go into the GitHub issue (nothing is sent until you press "
                          "Submit on GitHub; no audio, no transcripts):",
        "report_opening": "Opening your browser with a pre-filled GitHub issue...",
        "report_manual": "If the browser did not open, copy this link:",
        "report_marked": "Marked as handled; you will not be asked about these again.",
        "vosk_ru_license": "Russian voice model license is not stated upstream; download at your own responsibility",
        "vosk_ru_missing": "Russian voice model not installed (optional, opt-in): showing subtitles only, nothing will be spoken in Russian. To enable it: run `tlwu-setup --vosk-tts-ru` (downloads vosk-model-tts-ru-0.7-multi; its license is not stated upstream), or point --vosk-model / VOSK_MODEL at a model you already have.",
        "gui_src_lang": "Source language",
        "gui_target": "Translate to",
        "gui_engine": "Engine",
        "gui_model": "Speech model",
        "gui_quality": "Quality (auto = best for this PC; manual = choose below)",
        "gui_source": "Audio source (system = PC sound)",
        "gui_mode": "Mode (meeting = full sentences, chat = faster)",
        "gui_glossary": "Glossary",
        "gui_save": "Save transcript",
        "gui_show_src_line": "Show source line",
        "gui_live_draft": "Live draft (uses more GPU)",
        "gui_voice": "Speak translation on",
        "gui_voice_default": "(default output)",
        "gui_start": "Start",
        "gui_stop": "Stop",
        "gui_pause": "Pause",
        "gui_resume": "Resume",
        "gui_report": "Report a problem",
        "gui_check_setup": "Check setup",
        "gui_checking": "Checking setup...",
        "gui_check_ok": "Setup check: ready for a meeting.",
        "gui_check_bad": "Setup check found problems: see the fix lines.",
        "gui_ready": "Ready. Fully offline.",
        "gui_stopping": "Stopping...",
        "gui_starting": "Starting...",
        "gui_loading": "Loading... {s} s",
        "gui_dl_title": "Download speech model",
        "gui_dl_ask": "The speech model {model} is not downloaded yet.\n\nDownload it now? This uses the network once (tlwu-setup); translating stays offline.",
        "gui_dl_running": "Downloading {model}...",
        "gui_dl_ok": "{model} downloaded. Ready.",
        "gui_dl_fail": "Download of {model} failed. Check the network, or run: tlwu-setup --whisper {model}",
        "gui_stopped": "Stopped.",
        "gui_error": "Error: {msg}",
        "gui_ui_lang": "Interface language",
        "gui_dark": "Dark theme",
        "mt_untranslated": "[not translated]",
        "mt_pair_missing": "[no {src} -> {tgt} translation model installed: run tlwu-setup]",
        "gpu_fallback": "GPU cannot be used; running in CPU mode (translation is slower).\nReason: {why}",
        "gpu_none": "no usable NVIDIA GPU was found.",
        "menu_glossary_on": "Glossary: {n} file(s) from {dir}",
        "menu_cpu_mt_ask": "No usable GPU: translation uses OPUS (fast, but going through English it can lose or flip the meaning, e.g. negations). h = Hy-MT2 (better meaning, slower on CPU, about 1-2 s per sentence; `tlwu --probe --speed` measures it) [Enter=OPUS / h]: ",
        "menu_cpu_mt_hymt": "Translation engine: Hy-MT2 on CPU (slower; lines may arrive later).",
        "menu_speak_mt_hint": "Hy-MT2 not installed (speak mode needs it): pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu && tlwu-setup --hymt",
        "gpu_driver_old": "NVIDIA driver {have} is too old for CUDA 12 (needs {need} or newer), so the GPU cannot be used. Update the driver to use the GPU: {url}",
        "gpu_no_cuda": "an NVIDIA GPU is present (driver {have}) but CUDA cannot be started. Re-run the installer or `tlwu-setup --gpu`; if it still fails, reinstall the NVIDIA driver.",
        "gui_listening": "Listening...",
        "gui_floating_bar": "Floating subtitle bar",
        "gui_listen": "Listen",
        "gui_speak": "Speak",
        "gui_adjust": "⚙ Adjust",
        "gui_lines": "Subtitle lines (1-5)",
        "gui_font_size": "Font size (12-72)",
        "gui_menu_show_src": "Show source text",
        "gui_menu_font": "Font size",
        "gui_menu_larger": "Larger (+)",
        "gui_menu_smaller": "Smaller (-)",
        "gui_menu_opacity": "Opacity",
        "gui_menu_more_opaque": "More opaque",
        "gui_menu_less_opaque": "More transparent",
        "gui_menu_copy": "Copy last line",
        "gui_menu_pause": "Pause display",
        "gui_menu_quit": "Quit",
        "gui_pick_glossary": "Glossary (.txt)",
        "gui_pick_save": "Transcript file",
        "gui_open_url": "Open this URL: {url}",
    },
    "ru": {
        "guard_blocked": "\U0001F6E1 tlwu заблокировал исходящее соединение: {host}:{port} ({pkg}). "
                         "В обычной работе tlwu не нужна сеть; об этом можно сообщить из меню при следующем запуске.",
        "report_status": "С прошлого отчёта заблокировано исходящих соединений: {n}; адреса: {hosts}.",
        "report_preview": "Именно этот текст попадёт в issue на GitHub (ничего не отправляется, пока вы сами не "
                          "нажмёте Submit на GitHub; ни аудио, ни транскриптов):",
        "report_opening": "Открываю браузер с заполненным issue на GitHub...",
        "report_manual": "Если браузер не открылся, скопируйте эту ссылку:",
        "report_marked": "Отмечено как обработанное; об этих записях больше не спросим.",
        "vosk_ru_license": "Лицензия модели русского голоса не указана автором; скачивайте на свою ответственность",
        "vosk_ru_missing": "Модель русского голоса не установлена (необязательная, включается отдельно): показываю только субтитры, по-русски ничего не произносится. Чтобы включить: выполните `tlwu-setup --vosk-tts-ru` (скачает vosk-model-tts-ru-0.7-multi; лицензия не указана автором) или укажите --vosk-model / VOSK_MODEL на уже имеющуюся модель.",
        "gui_src_lang": "Язык источника",
        "gui_target": "Перевести на",
        "gui_engine": "Движок",
        "gui_model": "Модель речи",
        "gui_quality": "Качество (auto = лучшее для этого ПК; manual = выбрать ниже)",
        "gui_source": "Источник звука (system = звук ПК)",
        "gui_mode": "Режим (meeting = целые фразы, chat = быстрее)",
        "gui_glossary": "Глоссарий",
        "gui_save": "Сохранять расшифровку",
        "gui_show_src_line": "Показывать оригинал",
        "gui_live_draft": "Живой черновик (больше нагрузка на GPU)",
        "gui_voice": "Озвучивать перевод на",
        "gui_voice_default": "(вывод по умолчанию)",
        "gui_start": "Старт",
        "gui_stop": "Стоп",
        "gui_pause": "Пауза",
        "gui_resume": "Продолжить",
        "gui_report": "Сообщить о проблеме",
        "gui_check_setup": "Проверить настройку",
        "gui_checking": "Проверка настройки...",
        "gui_check_ok": "Проверка: всё готово для встречи.",
        "gui_check_bad": "Проверка нашла проблемы: см. строки fix.",
        "gui_ready": "Готово. Полностью офлайн.",
        "gui_stopping": "Остановка...",
        "gui_starting": "Запуск...",
        "gui_loading": "Запуск… {s} с",
        "gui_dl_title": "Загрузка модели речи",
        "gui_dl_ask": "Модель речи {model} ещё не загружена.\n\nЗагрузить сейчас? Сеть нужна один раз (tlwu-setup); перевод остаётся офлайн.",
        "gui_dl_running": "Загрузка {model}...",
        "gui_dl_ok": "{model} загружена. Готово.",
        "gui_dl_fail": "Не удалось загрузить {model}. Проверьте сеть или выполните: tlwu-setup --whisper {model}",
        "gui_stopped": "Остановлено.",
        "gui_error": "Ошибка: {msg}",
        "gui_ui_lang": "Язык интерфейса",
        "gui_dark": "Тёмная тема",
        "mt_untranslated": "[не переведено]",
        "mt_pair_missing": "[модель перевода {src} -> {tgt} не установлена: запустите tlwu-setup]",
        "gpu_fallback": "GPU недоступен; работаем на CPU (перевод медленнее).\nПричина: {why}",
        "gpu_none": "подходящая видеокарта NVIDIA не найдена.",
        "menu_glossary_on": "Глоссарий: файлов {n} из {dir}",
        "menu_cpu_mt_ask": "Подходящей видеокарты нет: перевод через OPUS (быстро, но через английский смысл может теряться или меняться на противоположный, например отрицания). h = Hy-MT2 (точнее по смыслу, на CPU медленнее, около 1-2 с на фразу; замер: `tlwu --probe --speed`) [Enter=OPUS / h]: ",
        "menu_cpu_mt_hymt": "Движок перевода: Hy-MT2 на CPU (медленнее; строки могут приходить позже).",
        "menu_speak_mt_hint": "Hy-MT2 не установлен (нужен для режима речи): pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu && tlwu-setup --hymt",
        "gpu_driver_old": "Драйвер NVIDIA {have} слишком старый для CUDA 12 (нужен {need} или новее), поэтому GPU не используется. Обновите драйвер: {url}",
        "gpu_no_cuda": "видеокарта NVIDIA есть (драйвер {have}), но CUDA не запускается. Запустите установщик или `tlwu-setup --gpu` ещё раз; если не поможет, переустановите драйвер NVIDIA.",
        "gui_listening": "Слушаю...",
        "gui_floating_bar": "Плавающая строка субтитров",
        "gui_listen": "Слух",
        "gui_speak": "Голос",
        "gui_adjust": "⚙ Опции",
        "gui_lines": "Строк субтитров (1-5)",
        "gui_font_size": "Размер шрифта (12-72)",
        "gui_menu_show_src": "Показывать оригинал",
        "gui_menu_font": "Размер шрифта",
        "gui_menu_larger": "Крупнее (+)",
        "gui_menu_smaller": "Мельче (-)",
        "gui_menu_opacity": "Прозрачность",
        "gui_menu_more_opaque": "Менее прозрачно",
        "gui_menu_less_opaque": "Более прозрачно",
        "gui_menu_copy": "Копировать последнюю строку",
        "gui_menu_pause": "Приостановить показ",
        "gui_menu_quit": "Выход",
        "gui_pick_glossary": "Глоссарий (.txt)",
        "gui_pick_save": "Файл расшифровки",
        "gui_open_url": "Откройте этот адрес: {url}",
    },
    "zh-TW": {
        "guard_blocked": "\U0001F6E1 已擋下 tlwu 對外連線：{host}:{port}（{pkg}）。"
                         "tlwu 平常不需要網路，下次開選單可一鍵回報。",
        "report_status": "自上次回報以來，已擋下 {n} 次對外連線：{hosts}。",
        "report_preview": "以下就是會放進 GitHub issue 的全部內容（在 GitHub 按下送出前不會傳出任何東西；"
                          "不含音訊與逐字稿）：",
        "report_opening": "正在開啟瀏覽器，帶入預填好的 GitHub issue...",
        "report_manual": "如果瀏覽器沒有開啟，請複製這個連結：",
        "report_marked": "已標記為處理過，這些之後不會再問。",
        "vosk_ru_license": "俄文語音模型上游未標示授權；下載請自負責任",
        "vosk_ru_missing": "尚未安裝俄文語音模型（選用，需主動啟用）：目前只顯示字幕，不會用俄文發聲。啟用方式：執行 `tlwu-setup --vosk-tts-ru`（下載 vosk-model-tts-ru-0.7-multi，上游未標示授權），或用 --vosk-model / VOSK_MODEL 指到你已有的模型。",
        "gui_src_lang": "來源語言",
        "gui_target": "翻譯成",
        "gui_engine": "引擎",
        "gui_model": "語音模型",
        "gui_quality": "品質（auto = 依這台電腦自動選；manual = 自己選下面兩項）",
        "gui_source": "音訊來源（system = 電腦音效）",
        "gui_mode": "模式（meeting = 完整句子，chat = 較快）",
        "gui_glossary": "詞彙表",
        "gui_save": "儲存逐字稿",
        "gui_show_src_line": "顯示原文",
        "gui_live_draft": "即時草稿（較耗 GPU）",
        "gui_voice": "朗讀翻譯到",
        "gui_voice_default": "（預設輸出）",
        "gui_start": "開始",
        "gui_stop": "停止",
        "gui_pause": "暫停",
        "gui_resume": "繼續",
        "gui_report": "回報問題",
        "gui_check_setup": "檢查設定",
        "gui_checking": "檢查設定中...",
        "gui_check_ok": "設定檢查：可以開會了。",
        "gui_check_bad": "設定檢查發現問題：請看 fix 那幾行。",
        "gui_ready": "就緒，完全離線。",
        "gui_stopping": "停止中...",
        "gui_starting": "啟動中...",
        "gui_loading": "載入中... {s} 秒",
        "gui_dl_title": "下載語音模型",
        "gui_dl_ask": "語音模型「{model}」還沒下載。\n\n現在下載嗎？只有這次會用網路（tlwu-setup）；翻譯仍然完全離線。",
        "gui_dl_running": "下載 {model} 中...",
        "gui_dl_ok": "{model} 下載完成，可以開始。",
        "gui_dl_fail": "{model} 下載失敗。請檢查網路，或執行：tlwu-setup --whisper {model}",
        "gui_stopped": "已停止。",
        "gui_error": "錯誤：{msg}",
        "gui_ui_lang": "介面語言",
        "gui_dark": "深色主題",
        "mt_untranslated": "[未能翻譯]",
        "mt_pair_missing": "[尚未安裝 {src} -> {tgt} 翻譯模型：請執行 tlwu-setup]",
        "gpu_fallback": "GPU 無法使用，已自動改用 CPU 模式（翻譯會比較慢）。\n原因：{why}",
        "gpu_none": "沒有偵測到可用的 NVIDIA 顯示卡。",
        "menu_glossary_on": "詞彙表：{dir} 共 {n} 個檔案",
        "menu_cpu_mt_ask": "沒有可用的顯示卡：翻譯用 OPUS（快，但經過英文轉譯，意思可能遺失或相反，例如否定句）。h = Hy-MT2（意思較準，CPU 上較慢，每句約 1-2 秒；可用 `tlwu --probe --speed` 實測）[Enter=OPUS / h]：",
        "menu_cpu_mt_hymt": "翻譯引擎：Hy-MT2（CPU，較慢，字幕可能晚一點出現）。",
        "menu_speak_mt_hint": "未安裝 Hy-MT2（說話模式需要）：pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu && tlwu-setup --hymt",
        "gpu_driver_old": "NVIDIA 驅動程式 {have} 太舊，不支援 CUDA 12（需要 {need} 以上），所以無法使用 GPU。請更新驅動程式即可使用 GPU：{url}",
        "gpu_no_cuda": "偵測到 NVIDIA 顯示卡（驅動程式 {have}），但 CUDA 無法啟動。請重新執行安裝程式或 `tlwu-setup --gpu`；仍失敗請重新安裝 NVIDIA 驅動程式。",
        "gui_listening": "聆聽中…",
        "gui_floating_bar": "浮動字幕條",
        "gui_listen": "聆聽",
        "gui_speak": "說話",
        "gui_adjust": "⚙ 調整",
        "gui_lines": "字幕行數（1-5）",
        "gui_font_size": "字體大小（12-72）",
        "gui_menu_show_src": "顯示原文",
        "gui_menu_font": "字體大小",
        "gui_menu_larger": "放大 (+)",
        "gui_menu_smaller": "縮小 (-)",
        "gui_menu_opacity": "透明度",
        "gui_menu_more_opaque": "較不透明",
        "gui_menu_less_opaque": "較透明",
        "gui_menu_copy": "複製最後一行",
        "gui_menu_pause": "暫停顯示",
        "gui_menu_quit": "結束",
        "gui_pick_glossary": "詞彙表 (.txt)",
        "gui_pick_save": "逐字稿檔案",
        "gui_open_url": "請開啟此網址：{url}",
    },
}


def normalize(tag: str | None) -> str:
    """Locale tag (ru_RU.UTF-8, zh-Hant-TW, en-US...) -> a supported UI language. zh-CN also uses the zh-TW text."""
    t = (tag or "").strip().lower().replace("_", "-")
    if t.startswith("ru"):
        return "ru"
    if t.startswith("zh"):
        return "zh-TW"
    return DEFAULT


def _system_tag() -> str:
    if sys.platform == "win32":
        try:
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value
        except Exception:  # noqa: BLE001
            pass
        return ""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True,
                                 timeout=3).stdout
            m = re.search(r'"?([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)*)"?', out.split("(", 1)[-1])
            if m:
                return m.group(1)
        except Exception:  # noqa: BLE001
            pass
    for k in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(k):
            return os.environ[k]
    return ""


_cached: str | None = None
_override: str | None = None  # set by the GUI language dropdown; beats env/config for this process


def set_ui_lang(lang: str, persist: bool = True) -> str:
    """Switch the UI language now (GUI dropdown) and remember it in the config file."""
    global _override
    _override = normalize(lang)
    if persist:
        try:
            userdirs.write_config_value("ui_lang", _override)
        except OSError:
            pass
    return _override


def ui_lang() -> str:
    global _cached
    if _override:
        return _override
    env = os.environ.get("TLWU_UI_LANG", "").strip()
    if env and env != "auto":
        return normalize(env)
    cfg = userdirs.read_config().get("ui_lang", "").strip()
    if cfg and cfg != "auto":
        return normalize(cfg)
    if _cached is None:
        _cached = normalize(_system_tag())
    return _cached


def t(key: str, **kw) -> str:
    table = MESSAGES.get(ui_lang(), {})
    s = table.get(key) or MESSAGES[DEFAULT][key]
    return s.format(**kw) if kw else s
