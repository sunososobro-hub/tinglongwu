"""UI text: GUI meeting notes (topic notes) panel (#1917).
One topic per module so unrelated tickets stop editing one file; `tinglongwu.i18n` merges them.
A new key goes into every language block of the matching topic (or a new i18n_<topic>.py listed in i18n.TOPICS)."""
from __future__ import annotations

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "gui_topics_menu": "Topic notes...",  # #611
        "gui_topics_title": "Topic notes",  # #611
        "gui_topics_model": "Model (.gguf)...",  # #611
        "gui_topics_no_model": "Turn on Meeting notes to download the notes model, or pick your own GGUF model.",  # #611
        "gui_topics_switch": "Meeting notes",  # #1379
        "gui_topics_lang": "Notes are written in {lang}, the language you read.",  # #1379
        "gui_topics_cpu_off": "No GPU found: meeting notes are off by default on this PC. On a CPU a 5-minute topic takes about 4 minutes, so the notes arrive after the meeting. Turn them on if that is fine.",  # #1379
        "gui_topics_downloading": "Downloading the notes model (about {mb} MB, once). Works offline afterwards.",  # #1379
        "gui_topics_cancel": "Cancel download",  # #1379
        "gui_topics_popout": "Pop out",  # #1705
        "gui_topics_dock": "Dock",  # #1705
        "gui_topics_float": "Meeting notes in a floating window",  # #1705
        "gui_topics_empty": "Each finished topic shows up here during the meeting.",  # #611
        "gui_topics_busy": "Summarizing the latest topic... {s} s",
    },
    "ru": {
        "gui_topics_menu": "Заметки по темам...",  # #611
        "gui_topics_title": "Заметки по темам",  # #611
        "gui_topics_model": "Модель (.gguf)...",  # #611
        "gui_topics_no_model": "Включите «Заметки встречи», чтобы скачать модель заметок, или выберите свою GGUF-модель.",  # #611
        "gui_topics_switch": "Заметки встречи",  # #1379
        "gui_topics_lang": "Заметки пишутся на языке, который вы читаете: {lang}.",  # #1379
        "gui_topics_cpu_off": "GPU не найден: на этом ПК заметки встречи по умолчанию выключены. На CPU 5-минутная тема обрабатывается около 4 минут, поэтому заметки появятся после встречи. Включите их, если это подходит.",  # #1379
        "gui_topics_downloading": "Загрузка модели заметок (около {mb} МБ, один раз). Потом работает офлайн.",  # #1379
        "gui_topics_cancel": "Отменить загрузку",  # #1379
        "gui_topics_popout": "Вынести",  # #1705
        "gui_topics_dock": "Вернуть",  # #1705
        "gui_topics_float": "Заметки встречи в отдельном окне",  # #1705
        "gui_topics_empty": "Каждая завершённая тема появится здесь во время встречи.",  # #611
        "gui_topics_busy": "Подводим итог последней темы... {s} с",
    },
    "zh-TW": {
        "gui_topics_menu": "重點整理...",  # #611
        "gui_topics_title": "重點整理",  # #611
        "gui_topics_model": "模型 (.gguf)...",  # #611
        "gui_topics_no_model": "開啟「重點整理」即可下載重點模型，或自選一個 GGUF 模型。",  # #611
        "gui_topics_switch": "重點整理",  # #1379
        "gui_topics_lang": "重點以你閱讀的語言撰寫：{lang}。",  # #1379
        "gui_topics_cpu_off": "沒有偵測到 GPU：這台電腦預設不開重點整理。用 CPU 整理一段 5 分鐘的主題約需 4 分鐘，重點會在會議結束後才出來。可以接受的話再開啟。",  # #1379
        "gui_topics_downloading": "正在下載重點模型（約 {mb} MB，只需一次）。之後可離線使用。",  # #1379
        "gui_topics_cancel": "取消下載",  # #1379
        "gui_topics_popout": "獨立視窗",  # #1705
        "gui_topics_dock": "收回",  # #1705
        "gui_topics_float": "重點整理用浮動視窗",  # #1705
        "gui_topics_empty": "會議中每講完一個主題，就會出現在這裡。",  # #611
        "gui_topics_busy": "正在整理最新一段重點… 已 {s} 秒",
    },
    "ja": {
        "gui_topics_menu": "トピックメモ...",  # #611
        "gui_topics_title": "トピックメモ",  # #611
        "gui_topics_model": "モデル (.gguf)...",  # #611
        "gui_topics_no_model": "「会議メモ」をオンにするとメモ用モデルをダウンロードします。自分の GGUF モデルを選ぶこともできます。",  # #611
        "gui_topics_switch": "会議メモ",  # #1379
        "gui_topics_lang": "メモはあなたが読む言語で書かれます：{lang}。",  # #1379
        "gui_topics_cpu_off": "GPU が見つかりません：この PC では会議メモは既定でオフです。CPU では 5 分の話題に約 4 分かかるため、メモは会議の後に届きます。それでよければオンにしてください。",  # #1379
        "gui_topics_downloading": "メモ用モデルをダウンロード中（約 {mb} MB、初回のみ）。その後はオフラインで使えます。",  # #1379
        "gui_topics_cancel": "ダウンロードを中止",  # #1379
        "gui_topics_popout": "切り離す",  # #1705
        "gui_topics_dock": "戻す",  # #1705
        "gui_topics_float": "会議メモを別ウィンドウで表示",  # #1705
        "gui_topics_empty": "会議中、話し終えたトピックがここに表示されます。",  # #611
        "gui_topics_busy": "最新のトピックを要約中... {s} 秒",
    },
}
