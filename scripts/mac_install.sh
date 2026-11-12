#!/bin/bash
# tinglongWu one-line installer for macOS (Apple Silicon and Intel). Safe to re-run: re-running = repair/update.
#
#   curl -fsSL https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/mac_install.sh | bash
#
# The release workflow replaces the ref placeholder below with the release tag, so this script installs exactly the
# version it was released with. Run straight from the repo (unreleased) it falls back to main.
#
# What it does (nothing outside your home folder except Homebrew packages):
#   1. checks Homebrew (does NOT install Homebrew silently: prints the official command and stops if missing)
#   2. brew install python@3.12 git, and the BlackHole 2ch + 16ch audio drivers (asks for your Mac password once)
#   3. clones/updates ~/tinglongwu, creates its .venv, pip install -e .
#   4. English voice: ~/tts-kokoro (kokoro-onnx 0.6.1, own venv) + model in ~/models/kokoro
#   5. downloads the speech/translation models (English listening, zh<->en speaking)
#   6. installs the `tlwu-menu` command in ~/.local/bin
# The installer never asks about auto-update: the menu (tlwu-menu) asks once on its first run and stores the answer.
# Override: TLWU_DIR (default ~/tinglongwu), TLWU_BRANCH (default: the release tag, or main), TLWU_REPO.
# CI-only switches (used by .github/workflows/install-test.yml; leave unset for a normal install):
#   TLWU_SKIP_BLACKHOLE=1  skip the BlackHole cask install (needs sudo/GUI approval)
#   TLWU_SKIP_MODELS=1     skip the Kokoro voice and the model downloads (steps 5 and 6)
# Written for macOS bash 3.2 (no arrays, no ${var,,}). UNTESTED on a real Mac when written: every step checks itself.

set -u

REPO_URL="${TLWU_REPO:-https://github.com/sunososobro-hub/tinglongwu.git}"
REF_EMBEDDED="__TLWU_REF__"
case "$REF_EMBEDDED" in __TLWU_*) REF_EMBEDDED=main ;; esac   # placeholder not substituted = unreleased
BRANCH="${TLWU_BRANCH:-$REF_EMBEDDED}"
DIR="${TLWU_DIR:-$HOME/tinglongwu}"
KOKORO_VENV="$HOME/tts-kokoro"
KOKORO_DIR="$HOME/models/kokoro"
KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_ONNX_SIZE=92361271
KOKORO_VOICES_SIZE=28214398
# SHA256 pins (same as scripts/linux_kokoro.sh). A mismatch deletes the file and aborts (fail closed).
# Env-overridable (tests only); setting a pin to empty skips the hash check and leaves only the size check.
KOKORO_ONNX_SHA256="${KOKORO_ONNX_SHA256-6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb}"
KOKORO_VOICES_SHA256="${KOKORO_VOICES_SHA256-bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d}"
BIN_DIR="$HOME/.local/bin"
WARNINGS=""

say()  { printf '\n==> %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '    [注意] %s\n' "$*" >&2; WARNINGS="${WARNINGS}  - $*
"; }
die()  {
  printf '\n[失敗] %s\n' "$*" >&2
  printf '       可以直接再貼一次安裝指令（會從中斷處修復）。若還是失敗，請把這個視窗的全部文字複製給維護者。\n' >&2
  exit 1
}

fsize() { wc -c < "$1" 2>/dev/null | tr -d ' '; }

# Everything runs inside main() so that bash has parsed the whole script before running it: with `curl | bash`
# the script arrives on stdin, and a child process (brew, git, pip) reading stdin could otherwise swallow it.
main() {
# give children the keyboard instead of the pipe (brew/sudo may ask for the Mac password)
if ( : </dev/tty ) 2>/dev/null; then exec </dev/tty; fi

# ---------------------------------------------------------------- 0. environment
say "0/8 檢查環境"
[ "$(uname -s)" = "Darwin" ] || die "這個安裝腳本只能在 Mac 上執行（偵測到 $(uname -s)）。"
[ "$(id -u)" -ne 0 ] || die "請不要用 sudo 或 root 執行；用一般使用者直接貼指令即可（需要密碼時腳本會自己問）。"
ARCH="$(uname -m)"
OSVER="$(sw_vers -productVersion 2>/dev/null || echo 0)"
info "macOS $OSVER, CPU $ARCH"
OSMAJOR="${OSVER%%.*}"
case "$OSMAJOR" in ''|*[!0-9]*) OSMAJOR=0 ;; esac
if [ "$OSMAJOR" -gt 0 ] && [ "$OSMAJOR" -lt 12 ]; then
  warn "macOS $OSVER 偏舊（建議 12 以上），套件可能裝不起來。"
fi
if [ "$ARCH" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)" = "1" ]; then
  warn "終端機正在 Rosetta 下執行（Intel 模式）。能用但較慢；建議到「終端機 > 取得資訊」取消「使用 Rosetta 打開」。"
fi
command -v curl >/dev/null 2>&1 || die "找不到 curl。"
if ! curl -fsS -o /dev/null --max-time 15 https://github.com 2>/dev/null; then
  die "連不上 github.com，請確認網路（或公司 VPN/代理）後再貼一次。"
fi

# ---------------------------------------------------------------- 1. Homebrew
say "1/8 檢查 Homebrew"
BREW=""
if command -v brew >/dev/null 2>&1; then BREW="$(command -v brew)"
elif [ -x /opt/homebrew/bin/brew ]; then BREW=/opt/homebrew/bin/brew
elif [ -x /usr/local/bin/brew ]; then BREW=/usr/local/bin/brew
fi
if [ -z "$BREW" ]; then
  cat <<'EOF'

這台 Mac 還沒有 Homebrew（Mac 的套件管理程式）。為了安全，本腳本不會偷偷幫你裝它。
請先自己貼下面這一行（官方指令，來源 https://brew.sh ），照畫面指示做完（會要你輸入 Mac 密碼）：

  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

裝完 Homebrew 後，照它最後印出的「Next steps」把兩行指令貼上（讓 brew 進入 PATH），
然後「關掉終端機、重新開一個」，再貼一次 tinglongWu 的安裝指令即可。
EOF
  exit 1
fi
eval "$("$BREW" shellenv)" || die "無法載入 Homebrew 環境（$BREW shellenv 失敗）。"
info "Homebrew: $BREW"
export HOMEBREW_NO_ENV_HINTS=1

# ---------------------------------------------------------------- 2. brew packages
say "2/8 安裝 Python 3.12 與 git"
for f in python@3.12 git; do
  if brew list --formula "$f" >/dev/null 2>&1; then info "$f 已安裝"
  else
    info "安裝 $f ..."
    brew install "$f" || die "brew install $f 失敗。請看上面的錯誤訊息；可先執行 brew doctor。"
  fi
done
PYBREW="$(brew --prefix python@3.12 2>/dev/null)/bin/python3.12"
[ -x "$PYBREW" ] || die "找不到 ${PYBREW}（python@3.12 安裝不完整）。可試：brew reinstall python@3.12"
GITBIN="$(command -v git)" || die "找不到 git。"
info "python: $PYBREW ($("$PYBREW" --version 2>&1))"

say "3/8 安裝 BlackHole 虛擬音效裝置（2ch 用來聽、16ch 當你的虛擬麥克風）"
info "接下來可能會跳出要你輸入「Mac 開機密碼」，輸入時畫面不會顯示字，輸完按 Enter。"
BH_FAIL=0
for c in blackhole-2ch blackhole-16ch; do
  if [ -n "${TLWU_SKIP_BLACKHOLE:-}" ]; then info "TLWU_SKIP_BLACKHOLE set: skipping $c"
  elif brew list --cask "$c" >/dev/null 2>&1; then info "$c 已安裝"
  else
    info "安裝 $c ..."
    if ! brew install --cask "$c"; then
      BH_FAIL=1
      warn "$c 安裝失敗（可能是密碼輸入錯誤或被系統安全性設定擋住）。之後重貼安裝指令即可重試；也可手動下載 https://existential.audio/blackhole/"
    fi
  fi
done

# ---------------------------------------------------------------- 4. repo + venv
say "4/8 下載 tinglongWu 並建立環境（${DIR}）"
if [ -d "$DIR/.git" ]; then
  info "已存在，更新中..."
  if ! FETCH_ERR=$(git -C "$DIR" fetch --quiet --force --tags origin 2>&1); then
    warn "更新失敗，先用現有版本繼續。git: ${FETCH_ERR}"
  elif git -C "$DIR" rev-parse -q --verify "refs/tags/$BRANCH" >/dev/null 2>&1; then
    git -C "$DIR" checkout --quiet "$BRANCH" 2>&1 || warn "$DIR 裡有本機改動，無法切到版本 ${BRANCH}；先用現有版本繼續。"
  elif ! git -C "$DIR" merge --ff-only --quiet "origin/$BRANCH" 2>&1; then
    warn "$DIR 裡有本機改動，無法自動更新；先用現有版本繼續（想強制還原：cd $DIR && git stash && 再貼一次安裝指令）。"
  fi
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  die "$DIR 已存在但不是 tinglongWu 的 git 資料夾。請改名或刪除它再重來（或用 TLWU_DIR=別的路徑 執行）。"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$DIR" || die "git clone 失敗（網路？）。"
fi
cd "$DIR" || die "無法進入 $DIR"

VENV_OK=0
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1; then
  VENV_OK=1
fi
if [ "$VENV_OK" -ne 1 ]; then
  info "建立 Python 環境 .venv ..."
  rm -rf .venv
  "$PYBREW" -m venv .venv || die "建立 .venv 失敗。"
fi
.venv/bin/python -m pip install -q --upgrade pip || warn "pip 升級失敗（不影響，繼續）。"
info "安裝 tinglongWu 套件（約 1-3 分鐘）..."
.venv/bin/python -m pip install -e . || die "pip install -e . 失敗。若錯誤和編譯/wheel 有關，請把完整訊息給維護者。"
.venv/bin/python -c "import soundcard, faster_whisper, ctranslate2, soundfile, numpy" \
  || die "套件匯入檢查失敗（上面應有 Python 錯誤訊息）。"
[ -x .venv/bin/tlwu-setup ] || die "找不到 .venv/bin/tlwu-setup，套件安裝不完整。"

# ---------------------------------------------------------------- 5. Kokoro (English voice)
say "5/8 英文語音 Kokoro（獨立環境，因為它含 GPL 的 espeak，不放進主環境）"
if [ -n "${TLWU_SKIP_MODELS:-}" ]; then info "TLWU_SKIP_MODELS set: skipping Kokoro and model downloads (steps 5-6)"
else
if [ ! -x "$KOKORO_VENV/bin/python" ]; then
  "$PYBREW" -m venv "$KOKORO_VENV" || die "建立 $KOKORO_VENV 失敗。"
fi
"$KOKORO_VENV/bin/python" -m pip install -q --upgrade pip || warn "Kokoro 環境的 pip 升級失敗（不影響）。"
"$KOKORO_VENV/bin/python" -m pip install "kokoro-onnx==0.6.1" || die "安裝 kokoro-onnx==0.6.1 失敗。"
"$KOKORO_VENV/bin/python" -c "import kokoro_onnx" || die "kokoro_onnx 匯入失敗。"

mkdir -p "$KOKORO_DIR" || die "無法建立 $KOKORO_DIR"
sha256_of() { shasum -a 256 "$1" | cut -d' ' -f1; }
fetch_model() {  # name expected_size expected_sha256(optional)
  local name="$1" want="$2" sha="${3:-}" got
  got="$(fsize "$KOKORO_DIR/$name")"
  if [ -f "$KOKORO_DIR/$name" ] && [ "$got" = "$want" ]; then
    if [ -z "$sha" ] || [ "$(sha256_of "$KOKORO_DIR/$name")" = "$sha" ]; then info "$name 已就緒"; return 0; fi
    info "$name 的 SHA256 不符，刪除後重新下載"; rm -f "$KOKORO_DIR/$name"
  fi
  info "下載 ${name}（約 $((want / 1000000)) MB）..."
  rm -f "$KOKORO_DIR/$name.part"
  curl -fL --retry 3 --retry-delay 3 -o "$KOKORO_DIR/$name.part" "$KOKORO_BASE/$name"     || die "下載 $name 失敗（網路？）。"
  got="$(fsize "$KOKORO_DIR/$name.part")"
  [ "$got" = "$want" ] || { rm -f "$KOKORO_DIR/$name.part"; die "$name 大小不符（拿到 ${got}，預期 ${want}），已刪除；請重貼指令。"; }
  if [ -n "$sha" ] && [ "$(sha256_of "$KOKORO_DIR/$name.part")" != "$sha" ]; then
    rm -f "$KOKORO_DIR/$name.part"; die "$name SHA256 不符，已刪除（拒絕使用）。"
  fi
  mv "$KOKORO_DIR/$name.part" "$KOKORO_DIR/$name" || die "無法儲存 $name"
}
fetch_model kokoro-v1.0.int8.onnx "$KOKORO_ONNX_SIZE" "$KOKORO_ONNX_SHA256"
fetch_model voices-v1.0.bin "$KOKORO_VOICES_SIZE" "$KOKORO_VOICES_SHA256"
( cd "$KOKORO_DIR" && shasum -a 256 kokoro-v1.0.int8.onnx voices-v1.0.bin > SHA256SUMS ) 2>/dev/null || true

# ---------------------------------------------------------------- 6. models
say "6/8 下載語音辨識與翻譯模型（只有這一次要連網；約 2-3 GB）"
# #178: core + language packs (default zh+en); ja / ru are offered with their size when first picked
.venv/bin/tlwu-setup --whisper small --packs "${TLWU_PACKS:-zh,en}" --allow-unpinned \
  || die "tlwu-setup 下載模型失敗（網路？）。重貼安裝指令會從中斷處繼續。"
# #276/#301: Hy-MT2 1.8B is the only default translation engine (runs on the CPU here): llama-cpp-python + the GGUF.
# No macOS wheel on the CPU index -> pip builds it from source with the Xcode command line tools (a few minutes).
info "安裝翻譯引擎 Hy-MT2（llama-cpp-python，可能要編譯幾分鐘）..."
.venv/bin/python -m pip install -r requirements-llama-cpu.txt \
  || warn "llama-cpp-python 安裝失敗：翻譯無法使用。請把上面的錯誤訊息給維護者，或重貼安裝指令重試。"
.venv/bin/tlwu-setup --hymt \
  || die "Hy-MT2 翻譯模型下載失敗（網路？）。重貼安裝指令會從中斷處繼續。"
fi

# ---------------------------------------------------------------- 7. launcher
say "7/8 安裝 tlwu-menu 指令"
mkdir -p "$BIN_DIR" || die "無法建立 $BIN_DIR"
cat > "$BIN_DIR/tlwu-menu" <<EOF
#!/bin/bash
# generated by mac_install.sh
[ -f "$DIR/scripts/linux_menu.sh" ] || { echo "找不到 $DIR/scripts/linux_menu.sh，請重貼安裝指令修復。"; exit 1; }
exec bash "$DIR/scripts/linux_menu.sh" "\$@"
EOF
chmod +x "$BIN_DIR/tlwu-menu" || die "無法設定 tlwu-menu 為可執行。"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"  # added by tinglongWu'
for rc in "$HOME/.zprofile" "$HOME/.zshrc"; do
  if ! grep -qs 'added by tinglongWu' "$rc" 2>/dev/null; then
    printf '\n%s\n' "$PATH_LINE" >> "$rc" 2>/dev/null || warn "無法寫入 ${rc}；請自己把 $BIN_DIR 加進 PATH，或直接用完整路徑 $BIN_DIR/tlwu-menu。"
  fi
done

# ---------------------------------------------------------------- 8. self-check
say "8/8 檢查音效裝置與自我測試"
SPK="$(.venv/bin/python -m tinglongwu.reverse.proto --list-speakers 2>/dev/null)"
MICS="$(.venv/bin/python -m tinglongwu.reverse.proto --list-mics 2>/dev/null)"
if printf '%s\n%s\n' "$SPK" "$MICS" | grep -qi 'BlackHole 2ch'; then info "BlackHole 2ch: 已偵測到"
else warn "還偵測不到 BlackHole 2ch。BlackHole 剛裝好時通常要「重新開機一次」才會出現（之後再貼一次安裝指令可重新檢查）。"; fi
if printf '%s\n' "$SPK" | grep -qi 'BlackHole 16ch'; then info "BlackHole 16ch: 已偵測到"
else warn "還偵測不到 BlackHole 16ch（同上，重新開機後再檢查）。"; fi
[ "$BH_FAIL" -eq 0 ] || warn "BlackHole 有安裝失敗，見上方訊息。"
if [ -z "${TLWU_SKIP_MODELS:-}" ]; then   # #99: do the downloaded models load and translate? (offline)
  # shellcheck source=tlwu_common.sh
  . "$DIR/scripts/tlwu_common.sh"
  tlwu_install_selftest .venv/bin/tlwu-setup --model small \
    || warn "安裝完成但自我測試失敗，詳見 install.log（選單的「回報問題」會附上）；已安裝的檔案都保留。"
fi

cat <<EOF

========================================================================
 安裝完成。tinglongWu 已裝在 $DIR

 還有「一件」要手動做（只做一次，約 2 分鐘）：設定 Mac 的聲音，讓程式聽得到對方的聲音

  1. 若上面顯示偵測不到 BlackHole，先「重新開機」一次。
  2. 按 Command+空白鍵，輸入「音訊 MIDI 設定」（Audio MIDI Setup），打開它。
  3. 左下角按「+」，選「建立多重輸出裝置」。
  4. 在右邊勾選兩個：你平常用的喇叭/耳機（排在第一個，當主要裝置）、以及「BlackHole 2ch」。
     「BlackHole 2ch」那一列的「偏移修正」(Drift Correction) 打勾；喇叭/耳機那列不要勾。
     （不要勾 BlackHole 16ch。）
  5. 在這個「多重輸出裝置」上按右鍵 > 選「將此裝置用於聲音輸出」。
     （多重輸出裝置沒有音量鍵，請直接用耳機/喇叭本身或 App 內的音量。）
     建議在左側把它改名為 tlwu-out，程式會據此避免把你自己的聲音送回去。
  6. 開 Google Meet / Zoom：「麥克風」選 BlackHole 16ch（不是 2ch）；
     喇叭維持「系統預設」即可（對方的聲音會經多重輸出裝置同時進你的耳機和 tinglongWu）。
  7. 第一次執行時，macOS 會跳出「"終端機" 想要使用麥克風」，請按「好」。
     若不小心按了不允許：系統設定 > 隱私權與安全性 > 麥克風 > 打開「終端機」。
  8. 戴耳機，避免對方的聲音被你的麥克風再收進去。

 之後使用：關掉這個終端機視窗、重新開一個，輸入

     tlwu-menu

 （選單會自動更新程式；離線時會略過，照常使用。）
 這次預設只裝了「英文」（聽英文 → 中文字幕；你說中文 → 英文語音）。
 之後要加：
   日文：開選單時按提示貼指令（需要 Hy-MT2：tlwu-menu 選日文時會告訴你要貼什麼）
   俄文語音（選用、預設不下載，模型上游未標示授權）：需另外裝 Vosk-TTS，再自行執行 tlwu-setup --vosk-tts-ru（選單會問 y/N，預設 N；見 $DIR/README.zh-TW.md）
========================================================================
EOF
if [ -n "$WARNINGS" ]; then
  printf '\n[注意事項彙整]\n%s\n' "$WARNINGS"
fi
exit 0
}

main "$@"
