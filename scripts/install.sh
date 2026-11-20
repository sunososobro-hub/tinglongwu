#!/usr/bin/env bash
# tinglongWu installer for Linux. Safe to re-run (repair/update).
#
#   curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh | bash
#
# It installs the tag linux-ok (#1596): the newest release whose Linux checks passed (a release that failed them leaves
# Linux on its last good version). No linux-ok tag on the server yet -> main. --dry-run prints the ref and exits.
# What it does: git clone (or update) ~/tinglongwu at that tag, create .venv, pip install -e .
# It asks y/N before any system-level step (apt, needs sudo; default = no). It also installs the Kokoro English voice
# (scripts/linux_kokoro.sh: ~120 MB, SHA256-checked); TLWU_SKIP_MODELS=1 skips that.
# Unattended: TLWU_YES=1 (or --yes) answers yes to the apt prompt, asks nothing else, and also pre-downloads the default
# first-run ASR/MT models (the same tier the first-run setup picks, SHA256-checked by tlwu-setup):
#   curl -fsSL .../install.sh | TLWU_YES=1 bash
# Without it ASR/MT models are never downloaded here. The Russian voice is opt-in (no upstream license) and never fetched.
# Override: TLWU_DIR (default ~/tinglongwu), TLWU_REF (tag or branch), TLWU_REPO, TLWU_UI_LANG=en|ru|zh-TW, TLWU_SKIP_MODELS=1,
# TLWU_YES=1.
set -u

MARKER="linux-ok"   # #1596: moved by release.yml only when the Linux jobs passed on that release
REF_EMBEDDED="__TLWU_REF__"
case "$REF_EMBEDDED" in __TLWU_*) REF_EMBEDDED="$MARKER" ;; esac   # release.yml embeds the same marker
REF="${TLWU_REF:-$REF_EMBEDDED}"
REPO_URL="${TLWU_REPO:-https://github.com/sunososobro-hub/tinglongwu.git}"
# the marker does not exist until the first release with per-platform lanes: fall back to main. Called only once git
# is there (on a fresh Mac /usr/bin/git is a shim that pops up the Xcode tools dialog).
resolve_ref() {
  # only "no such ref" (exit 2) falls back to main; a network error keeps the marker (the clone then fails loudly)
  [ "$REF" = "$MARKER" ] || return 0
  local rc=0
  GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code --tags "$REPO_URL" "refs/tags/$MARKER" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -eq 2 ]; then REF=main; fi
}
for a in "$@"; do
  if [ "$a" = "--dry-run" ]; then resolve_ref; echo "ref: $REF"; exit 0; fi
done
DIR="${TLWU_DIR:-$HOME/tinglongwu}"
YES="${TLWU_YES:-}"
for a in "$@"; do [ "$a" = "--yes" ] && YES=1; done

# --uninstall (#476): hand over to scripts/uninstall.sh (other args, e.g. --purge, pass through)
for a in "$@"; do
  if [ "$a" = "--uninstall" ]; then
    [ -f "$DIR/scripts/uninstall.sh" ] || { echo "[x] $DIR/scripts/uninstall.sh not found (older install, or wrong TLWU_DIR). Re-run this installer once to update, then run it with --uninstall." >&2; exit 1; }
    exec bash "$DIR/scripts/uninstall.sh" "$@"
  fi
done

# ---- messages (en / ru / zh-TW); the repo's scripts/i18n is not available before the clone, so they live here
L="${TLWU_UI_LANG:-}"
[ -n "$L" ] || L="${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}"
case "$(printf '%s' "$L" | tr 'A-Z_' 'a-z-')" in ru*) L=ru ;; zh*) L=zh-TW ;; *) L=en ;; esac
t() {
  case "$L:$1" in
    en:root)   echo "Do not run as root; run as a normal user (the script asks for sudo only when needed)." ;;
    ru:root)   echo "Не запускайте от root; запустите от обычного пользователя (sudo спросит сам, если нужно)." ;;
    zh-TW:root) echo "請不要用 root 執行；用一般使用者即可（需要時才會問 sudo）。" ;;
    en:ref)    echo "Installing tinglongWu version %s into %s" ;;
    ru:ref)    echo "Установка tinglongWu, версия %s, в %s" ;;
    zh-TW:ref) echo "安裝 tinglongWu 版本 %s 到 %s" ;;
    en:missing) echo "Missing system packages: %s" ;;
    ru:missing) echo "Не хватает системных пакетов: %s" ;;
    zh-TW:missing) echo "缺少系統套件：%s" ;;
    en:ask)    echo "Install them now with apt (uses sudo)? [y/N] " ;;
    ru:ask)    echo "Установить через apt (нужен sudo)? [y/N] " ;;
    zh-TW:ask) echo "要現在用 apt 安裝嗎（會用 sudo）？[y/N] " ;;
    en:declined) echo "Not installed. Install them yourself, then run this command again." ;;
    ru:declined) echo "Не установлено. Установите сами и запустите команду снова." ;;
    zh-TW:declined) echo "未安裝。請自行安裝後再執行一次這個指令。" ;;
    en:notk)   echo "tkinter (Tk for Python) is missing, so tlwu-gui will not start; install your distro's Tk package for Python (e.g. python3-tk / python3-tkinter / tk), or delete .venv and re-run this installer. The terminal menu works without it." ;;
    ru:notk)   echo "Нет tkinter (Tk для Python), tlwu-gui не запустится; установите пакет Tk для Python вашего дистрибутива (например python3-tk / python3-tkinter / tk). Или удалите .venv и запустите установщик снова. Меню в терминале работает и без него." ;;
    zh-TW:notk) echo "缺少 tkinter（Python 的 Tk），tlwu-gui 無法啟動；請安裝你發行版的 Python Tk 套件（例如 python3-tk / python3-tkinter / tk），或刪除 .venv 後重跑安裝程式。終端機選單不需要它。" ;;
    en:nogit)  echo "git, python3 (3.12 with venv) and curl are required; apt-get not found, install them manually." ;;
    ru:nogit)  echo "Нужны git, python3 (3.12 с venv) и curl; apt-get не найден, установите вручную." ;;
    zh-TW:nogit) echo "需要 git、python3（3.12，含 venv）與 curl；找不到 apt-get，請手動安裝。" ;;
    en:badpy)  echo "python3 must be 3.12 (found %s): the pinned numpy/scipy need 3.12. Install Python 3.12 (e.g. apt install python3.12 python3.12-venv) and re-run." ;;
    ru:badpy)  echo "Нужен python3 версии 3.12 (найден %s): закреплённые numpy/scipy требуют 3.12. Установите Python 3.12 и запустите снова." ;;
    zh-TW:badpy) echo "python3 必須是 3.12（目前 %s）：釘選的 numpy/scipy 需要 3.12。請安裝 Python 3.12 後重跑。" ;;
    en:dirty)  echo "%s is not a tinglongWu git folder (or has local changes); rename it or set TLWU_DIR." ;;
    ru:dirty)  echo "%s не является git-папкой tinglongWu (или есть локальные правки); переименуйте или задайте TLWU_DIR." ;;
    zh-TW:dirty) echo "%s 不是 tinglongWu 的 git 資料夾（或有本機改動）；請改名或設定 TLWU_DIR。" ;;
    en:diverged) echo "Local history diverged from origin/%s; saved the old state as branch %s and reset to origin/%s." ;;
    ru:diverged) echo "Локальная история разошлась с origin/%s; старое состояние сохранено в ветке %s, выполнен сброс на origin/%s." ;;
    zh-TW:diverged) echo "本機歷史與 origin/%s 分岔；舊狀態已存成分支 %s，並重設到 origin/%s。" ;;
    en:nff)    echo "Cannot fast-forward %s to origin/%s (not a network problem: the history diverged or the update cannot be applied)." ;;
    ru:nff)    echo "Не удаётся обновить %s до origin/%s (не сеть: история разошлась или обновление нельзя применить)." ;;
    zh-TW:nff) echo "無法把 %s 快轉到 origin/%s（不是網路問題：歷史分岔或更新無法套用）。" ;;
    en:at)     echo "Updated to %s." ;;
    ru:at)     echo "Обновлено до %s." ;;
    zh-TW:at)  echo "已更新到 %s。" ;;
    en:pip)    echo "Installing Python packages (1-5 min) ..." ;;
    ru:pip)    echo "Установка Python-пакетов (1-5 мин) ..." ;;
    zh-TW:pip) echo "安裝 Python 套件（約 1-5 分鐘）..." ;;
    en:done)   echo "Done. Next, start the menu:" ;;
    ru:done)   echo "Готово. Теперь запустите меню:" ;;
    zh-TW:done) echo "完成。接著開啟選單：" ;;
    en:models) echo "Models are NOT downloaded yet; the menu asks (default no) before every download." ;;
    ru:models) echo "Модели пока НЕ скачаны; меню спросит (по умолчанию нет) перед каждой загрузкой." ;;
    zh-TW:models) echo "尚未下載任何模型；選單在每次下載前都會先問你（預設不下載）。" ;;
    en:asrmt)  echo "Downloading the default ASR/MT models (SHA256-checked; large download) ..." ;;
    ru:asrmt)  echo "Загрузка моделей ASR/MT по умолчанию (проверка SHA256; большой объём) ..." ;;
    zh-TW:asrmt) echo "下載預設的語音辨識/翻譯模型（SHA256 驗證；檔案很大）..." ;;
    en:ready)  echo "The default ASR/MT models are installed; the first start needs no download." ;;
    ru:ready)  echo "Модели ASR/MT по умолчанию установлены; при первом запуске загрузка не нужна." ;;
    zh-TW:ready) echo "預設的辨識/翻譯模型已安裝；第一次啟動不需要再下載。" ;;
    en:llama)  echo "Translation engine Hy-MT2 (llama-cpp-python, CPU build) ..." ;;
    ru:llama)  echo "Движок перевода Hy-MT2 (llama-cpp-python, сборка для CPU) ..." ;;
    zh-TW:llama) echo "安裝翻譯引擎 Hy-MT2（llama-cpp-python，CPU 版）..." ;;
    en:nollama) echo "llama-cpp-python could not be installed: tlwu cannot translate until it is. Re-run this command, or send the error above to the maintainer." ;;
    ru:nollama) echo "Не удалось установить llama-cpp-python: перевод не работает, пока он не установлен. Запустите команду снова или отправьте ошибку выше разработчику." ;;
    zh-TW:nollama) echo "llama-cpp-python 安裝失敗：在它裝好之前無法翻譯。請重跑這個指令，或把上面的錯誤訊息給維護者。" ;;
    *) echo "$1" ;;
  esac
}
say()  { printf '\n==> %s\n' "$*"; }
die()  { printf '\n[x] %s\n' "$*" >&2; exit 1; }
find_py() {
  PY=""
  # only 3.12: requirements.txt pins numpy 2.5.4 / scipy 1.18.1, which publish wheels for Python >= 3.12 only (#138)
  for c in python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
}

# TLWU_YES: pre-download the default first-run ASR/MT models = the tier tlwu_app.tiers picks (GPU/CPU), via tlwu-setup.
prefetch_models() {
  local args
  # shellcheck disable=SC2016
  args="$(.venv/bin/python -c 'from tlwu_app.tiers import choose_tier, detect_gpu; print(" ".join(choose_tier(detect_gpu()).downloader_args()))')" \
    || die "cannot pick the model tier. Re-run this command to resume."
  say "$(t asrmt)"
  # #671: one question "Which language do you want to understand?" (--ask-lang); TLWU_PACKS / TLWU_YES=1 skip it
  [ -n "$YES" ] && export TLWU_YES=1
  # shellcheck disable=SC2086
  .venv/bin/python -m tinglongwu_setup $args --ask-lang || die "ASR/MT model download failed. Re-run this command to resume (or set TLWU_SKIP_MODELS=1 to skip)."
}

# #1060: Hy-MT2 is the only default translation engine (#276/#301), also on Linux: without llama-cpp-python every
# `tlwu` run stops at "Hy-MT2 failed to load" and prints no translation. Same CPU build + sdist fallback as macOS (#420).
install_llama() {
  .venv/bin/python -c 'import llama_cpp' >/dev/null 2>&1 && return 0
  say "$(t llama)"
  # shellcheck source=tlwu_common.sh
  . "$DIR/scripts/tlwu_common.sh"
  tlwu_install_llama .venv/bin/python requirements-llama-cpu.txt || { printf '\n[!] %s\n' "$(t nollama)" >&2; return 1; }
}

# After fetch+checkout of $REF in $DIR: fast-forward a branch ref; a tag stays as is.
# Diverged (e.g. rewritten upstream history) + clean tree -> backup branch, reset to origin/$REF (#383).
# Dirty tree -> stop. Never continue silently on stale code.
update_branch() {
  git -C "$DIR" show-ref --verify --quiet "refs/remotes/origin/$REF" || return 0
  if ! git -C "$DIR" merge --ff-only --quiet "origin/$REF" 2>/dev/null; then
    # shellcheck disable=SC2059
    [ -z "$(git -C "$DIR" status --porcelain --untracked-files=no 2>/dev/null)" ] || die "$(printf "$(t dirty)" "$DIR")"
    if git -C "$DIR" merge-base --is-ancestor HEAD "origin/$REF" 2>/dev/null; then
      # shellcheck disable=SC2059
      die "$(printf "$(t nff)" "$REF" "$REF")"
    fi
    local bk="backup-$(date +%Y%m%d-%H%M%S)"
    git -C "$DIR" branch "$bk" HEAD >/dev/null 2>&1 || die "cannot create backup branch $bk"
    git -C "$DIR" reset --hard --quiet "origin/$REF" || die "git reset failed"
    # shellcheck disable=SC2059
    printf "$(t diverged)\n" "$REF" "$bk" "$REF"
  fi
  # shellcheck disable=SC2059
  printf "$(t at)\n" "$(git -C "$DIR" rev-parse --short HEAD)"
}

# #1678: app-menu shortcut (one fixed file, rewritten on every run; scripts/uninstall.sh removes it)
install_desktop_shortcut() {
  local d="$HOME/.local/share/applications" f
  f="$d/tinglongwu.desktop"
  mkdir -p "$d" || return 0
  printf '[Desktop Entry]\nType=Application\nName=tinglongWu\nComment=Offline voice translator\nExec=%s/.venv/bin/tlwu-gui\nIcon=%s/src/ui/tinglongwu/gui/assets/tinglongwu.png\nTerminal=false\nCategories=AudioVideo;Audio;\n' "$DIR" "$DIR" > "$f.tmp" && mv -f "$f.tmp" "$f" || rm -f "$f.tmp"
}

# #1677: the window version (GUI) comes first; start it when a desktop is there and a person is at the keyboard, else print both commands.
finish_gui() {
  local gui="$DIR/.venv/bin/tlwu-gui"
  printf '\n    %q\n    bash %q\n\n' "$gui" "$DIR/scripts/linux_menu.sh"
  if { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; } && [ -t 0 ] && [ -x "$gui" ]; then exec "$gui"; fi
  return 0
}

main() {
  # keep the keyboard for prompts even under `curl | bash` (not needed, and not wanted, when unattended)
  if [ -z "$YES" ] && ( : </dev/tty ) 2>/dev/null; then exec </dev/tty; fi
  [ "$(id -u)" -ne 0 ] || die "$(t root)"
  [ "$(uname -s)" = "Linux" ] || die "Linux only (found $(uname -s)); on macOS use mac_install.sh."
  # shellcheck disable=SC2059
  say "$(printf "$(t ref)" "$REF" "$DIR")"

  find_py
  MISSING=""
  command -v git  >/dev/null 2>&1 || MISSING="$MISSING git"
  command -v curl >/dev/null 2>&1 || MISSING="$MISSING curl"
  if [ -z "$PY" ]; then MISSING="$MISSING python3 python3-venv python3-pip"
  elif ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then MISSING="$MISSING python3-venv python3-pip"; fi
  # audio libraries imported by the package (same as CI)
  if command -v dpkg >/dev/null 2>&1; then
    for p in libsndfile1 libpulse0; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
  fi
  # tlwu-gui needs tkinter (#328); the venv uses the system Python's Tk, which Debian/Ubuntu ship separately
  if [ -n "$PY" ] && "$PY" -c 'import tkinter' 2>/dev/null; then :
  elif command -v apt-get >/dev/null 2>&1; then
    # Ubuntu 24.04: python3 is 3.12 -> python3-tk; a separate python3.12 (e.g. deadsnakes) -> python3.12-tk
    if [ "$PY" = "python3.12" ] && ! python3 -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null; then
      MISSING="$MISSING python3.12-tk"
    else
      MISSING="$MISSING python3-tk"
    fi
  else
    printf '\n[!] %s\n' "$(t notk)" >&2
  fi
  if [ -n "$MISSING" ]; then
    # shellcheck disable=SC2059
    printf "$(t missing)\n" "$MISSING"
    command -v apt-get >/dev/null 2>&1 || die "$(t nogit)"
    if [ -n "$YES" ]; then ans=y; else ans=""; read -rp "$(t ask)" ans || ans=""; fi
    case "$ans" in y|Y|yes|YES|Yes) ;; *) t declined; exit 1 ;; esac
    # shellcheck disable=SC2086
    sudo env DEBIAN_FRONTEND=noninteractive apt-get update && sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y $MISSING || die "apt-get failed"
    find_py
  fi
  if [ -z "$PY" ]; then
    # shellcheck disable=SC2059
    die "$(printf "$(t badpy)" "$(python3 --version 2>&1)")"
  fi

  resolve_ref
  if [ -d "$DIR/.git" ]; then
    # --force: tags that were rewritten upstream must not block an update (#370)
    if ! FETCH_ERR=$(git -C "$DIR" fetch --quiet --force --tags origin 2>&1); then
      die "git fetch failed: ${FETCH_ERR:-unknown error}"
    fi
    if ! git -C "$DIR" checkout --quiet "$REF" 2>/dev/null; then
      # shellcheck disable=SC2059
      die "$(printf "$(t dirty)" "$DIR")"
    fi
    update_branch
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    # shellcheck disable=SC2059
    die "$(printf "$(t dirty)" "$DIR")"
  else
    git clone --quiet --branch "$REF" "$REPO_URL" "$DIR" || die "git clone failed (network, or unknown version $REF)"
  fi
  cd "$DIR" || die "cannot enter $DIR"
  # #1664: pre-move root packages (even just __pycache__) shadow src/ as namespace packages; only once src/ is here
  if [ -f src/core/tinglongwu/__init__.py ]; then rm -rf tinglongwu tinglongwu_setup tlwu_app tlwu_eval; fi

  [ -x .venv/bin/python ] || "$PY" -m venv .venv || die "venv creation failed"
  say "$(t pip)"
  .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
  .venv/bin/python -m pip install -e . || die "pip install failed (network?). Re-run this command to resume."
  # an older .venv may come from another Python than $PY: check the interpreter tlwu-gui really runs (#328)
  .venv/bin/python -c 'import tkinter' 2>/dev/null || printf '\n[!] %s\n' "$(t notk)" >&2
  install_desktop_shortcut || true
  install_llama || true   # warned above; the rest of the install still works (transcript, menu)

  if [ -z "${TLWU_SKIP_MODELS:-}" ]; then
    bash scripts/linux_kokoro.sh || die "Kokoro install failed. Re-run this command to resume (or set TLWU_SKIP_MODELS=1 to skip)."
  fi

  # unattended, or a person at the keyboard (#671): ask which language, then fetch only that pack
  FETCHED=""
  if [ -z "${TLWU_SKIP_MODELS:-}" ] && { [ -n "$YES" ] || [ -t 0 ]; }; then prefetch_models; FETCHED=1; fi

  say "$(t done)"
  if [ -n "$FETCHED" ]; then t ready; else t models; fi
  finish_gui
  exit 0
}
[ -n "${TLWU_SOURCE_ONLY:-}" ] || main "$@"
