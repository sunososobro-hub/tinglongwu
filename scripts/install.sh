#!/usr/bin/env bash
# tinglongWu installer for Linux. Safe to re-run (repair/update).
#
#   curl -fsSL https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.sh | bash
#
# The release workflow replaces the ref placeholder below with the release tag, so this script installs exactly the
# version it was released with. Run straight from the repo (unreleased) it falls back to main.
# What it does: git clone (or update) ~/tinglongwu at that tag, create .venv, pip install -e .
# It asks y/N before any system-level step (apt, needs sudo; default = no). It also installs the Kokoro English voice
# (scripts/linux_kokoro.sh: ~120 MB, SHA256-checked); TLWU_SKIP_MODELS=1 skips that. ASR/MT models are never downloaded here.
# Override: TLWU_DIR (default ~/tinglongwu), TLWU_REF (tag or branch), TLWU_REPO, TLWU_UI_LANG=en|ru|zh-TW, TLWU_SKIP_MODELS=1.
set -u

REF_EMBEDDED="__TLWU_REF__"
case "$REF_EMBEDDED" in __TLWU_*) REF_EMBEDDED=main ;; esac   # placeholder not substituted = unreleased
REF="${TLWU_REF:-$REF_EMBEDDED}"
REPO_URL="${TLWU_REPO:-https://github.com/sunososobro-hub/tinglongwu.git}"
DIR="${TLWU_DIR:-$HOME/tinglongwu}"

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
    en:nogit)  echo "git, python3 (3.10-3.12 with venv) and curl are required; apt-get not found, install them manually." ;;
    ru:nogit)  echo "Нужны git, python3 (3.10-3.12 с venv) и curl; apt-get не найден, установите вручную." ;;
    zh-TW:nogit) echo "需要 git、python3（3.10-3.12，含 venv）與 curl；找不到 apt-get，請手動安裝。" ;;
    en:badpy)  echo "python3 must be 3.10 to 3.12 (found %s). Install a matching Python and re-run." ;;
    ru:badpy)  echo "Нужен python3 версии 3.10-3.12 (найден %s). Установите подходящий и запустите снова." ;;
    zh-TW:badpy) echo "python3 必須是 3.10 到 3.12（目前 %s）。請安裝合適版本後重跑。" ;;
    en:dirty)  echo "%s is not a tinglongWu git folder (or has local changes); rename it or set TLWU_DIR." ;;
    ru:dirty)  echo "%s не является git-папкой tinglongWu (или есть локальные правки); переименуйте или задайте TLWU_DIR." ;;
    zh-TW:dirty) echo "%s 不是 tinglongWu 的 git 資料夾（或有本機改動）；請改名或設定 TLWU_DIR。" ;;
    en:pip)    echo "Installing Python packages (1-5 min) ..." ;;
    ru:pip)    echo "Установка Python-пакетов (1-5 мин) ..." ;;
    zh-TW:pip) echo "安裝 Python 套件（約 1-5 分鐘）..." ;;
    en:done)   echo "Done. Next, start the menu:" ;;
    ru:done)   echo "Готово. Теперь запустите меню:" ;;
    zh-TW:done) echo "完成。接著開啟選單：" ;;
    en:models) echo "Models are NOT downloaded yet; the menu asks (default no) before every download." ;;
    ru:models) echo "Модели пока НЕ скачаны; меню спросит (по умолчанию нет) перед каждой загрузкой." ;;
    zh-TW:models) echo "尚未下載任何模型；選單在每次下載前都會先問你（預設不下載）。" ;;
    *) echo "$1" ;;
  esac
}
say()  { printf '\n==> %s\n' "$*"; }
die()  { printf '\n[x] %s\n' "$*" >&2; exit 1; }
find_py() {
  PY=""
  for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,12) else 1)' 2>/dev/null; then PY="$c"; break; fi
  done
}

main() {
  # keep the keyboard for prompts even under `curl | bash`
  if ( : </dev/tty ) 2>/dev/null; then exec </dev/tty; fi
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
  if [ -n "$MISSING" ]; then
    # shellcheck disable=SC2059
    printf "$(t missing)\n" "$MISSING"
    command -v apt-get >/dev/null 2>&1 || die "$(t nogit)"
    ans=""; read -rp "$(t ask)" ans || ans=""
    case "$ans" in y|Y|yes|YES|Yes) ;; *) t declined; exit 1 ;; esac
    # shellcheck disable=SC2086
    sudo apt-get update && sudo apt-get install -y $MISSING || die "apt-get failed"
    find_py
  fi
  if [ -z "$PY" ]; then
    # shellcheck disable=SC2059
    die "$(printf "$(t badpy)" "$(python3 --version 2>&1)")"
  fi

  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" fetch --quiet --tags origin || die "git fetch failed (network?)"
    if ! git -C "$DIR" checkout --quiet "$REF" 2>/dev/null; then
      # shellcheck disable=SC2059
      die "$(printf "$(t dirty)" "$DIR")"
    fi
    git -C "$DIR" merge --ff-only --quiet "origin/$REF" 2>/dev/null || true   # branch ref only; a tag stays as is
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    # shellcheck disable=SC2059
    die "$(printf "$(t dirty)" "$DIR")"
  else
    git clone --quiet --branch "$REF" "$REPO_URL" "$DIR" || die "git clone failed (network, or unknown version $REF)"
  fi
  cd "$DIR" || die "cannot enter $DIR"

  [ -x .venv/bin/python ] || "$PY" -m venv .venv || die "venv creation failed"
  say "$(t pip)"
  .venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1 || true
  .venv/bin/python -m pip install -e . || die "pip install failed (network?). Re-run this command to resume."

  if [ -z "${TLWU_SKIP_MODELS:-}" ]; then
    bash scripts/linux_kokoro.sh || die "Kokoro install failed. Re-run this command to resume (or set TLWU_SKIP_MODELS=1 to skip)."
  fi

  say "$(t done)"
  printf '\n    bash %s/scripts/linux_menu.sh\n\n' "$DIR"
  t models
  exit 0
}
main "$@"
