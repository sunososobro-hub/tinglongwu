# tinglongWu helper menu (Windows, PowerShell 5.1). Run from the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\windows_menu.ps1
# Env WHISPER sets the speech model (empty = default), e.g.  $env:WHISPER="medium"; powershell -File scripts\windows_menu.ps1
# Menu language follows the system (override: $env:TLWU_UI_LANG = en | ru | zh-TW, or I in the menu); texts live in scripts\i18n\*.psd1.
# Network is used only for install/setup and update (git); $env:TLWU_NO_UPDATE=1 forces updates off. Everything else is offline.
# "Speak" needs VB-CABLE (Meet microphone = "CABLE Output") and Vosk-TTS in its own venv:
#   python -m venv $HOME\tts-vosk; & $HOME\tts-vosk\Scripts\pip install "vosk-tts==0.3.61"
#   model: https://alphacephei.com/vosk/models/vosk-model-tts-ru-0.7-multi.zip -> $HOME\vosk-model-tts-ru-0.7-multi
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$py = ".\.venv\Scripts\python.exe"
$tlwu = ".\.venv\Scripts\tlwu.exe"

. (Join-Path $PSScriptRoot "tlwu_common.ps1")
Import-TlwuMessages

# Updating is opt-in: first run asks once (saved in the user config); then every menu start checks GitHub
# (git fetch, 6 s cap, never blocks) only if you said yes, otherwise press U. TLWU_NO_UPDATE=1 forces it off.
Invoke-TlwuConsent
if ($env:TLWU_OFFLINE -eq "1") { Write-Host (T 'OFFLINE_BANNER') }  # #254
if (Test-TlwuAutoUpdate) { Update-Tlwu $py | Out-Null }
$ErrorActionPreference = "Stop"
# Blocked-connection reports from the network guard: asks; nothing is sent unless you say y and submit on GitHub.
Invoke-TlwuReportCheck $py

$voskPy = if ($env:VOSK_PY) { $env:VOSK_PY } else { "$HOME\tts-vosk\Scripts\python.exe" }
$voskModel = if ($env:VOSK_MODEL) { $env:VOSK_MODEL } else { "$HOME\vosk-model-tts-ru-0.7-multi" }
$wL = @(); $wS = @()
if ($env:WHISPER) { $wL = @("--model", $env:WHISPER); $wS = @("--whisper", $env:WHISPER) }
$voice = if ($env:VOICE) { $env:VOICE } else { "4" }
# English speech: Kokoro in its own venv (it bundles GPL espeak, so never install it into .venv):
#   python -m venv $HOME\tts-kokoro; & $HOME\tts-kokoro\Scripts\pip install "kokoro-onnx==0.6.1"
#   model dir needs kokoro-v1.0.int8.onnx + voices-v1.0.bin
$kokoroPy = if ($env:KOKORO_PY) { $env:KOKORO_PY } else { "$HOME\tts-kokoro\Scripts\python.exe" }
# install.ps1 (#124) puts the pinned model in the repo models\kokoro; a manual install may use $HOME\models\kokoro
$kokoroModel = if ($env:KOKORO_MODEL) { $env:KOKORO_MODEL } elseif (Test-Path "$HOME\models\kokoro") { "$HOME\models\kokoro" } else { Join-Path (Get-Location) "models\kokoro" }
$kokoroVoice = if ($env:KOKORO_VOICE) { $env:KOKORO_VOICE } else { "0" }

$L = if ($env:TLWU_LANG) { $env:TLWU_LANG } else { "auto" }
$cands = "en,ru,ja,zh"   # auto: language follows whoever speaks, per sentence
$followLangs = "en,ru,ja"   # languages the speak side prepares in meeting mode
$followFile = Join-Path $env:TEMP "tlwu_follow_lang"
function Get-LangName($l) {  # auto from the menu texts; languages from tinglongwu.langs (marks the experimental ones, #58)
    if ($l -ne "auto") { $n = & $py -m tinglongwu.langs --name $l 2>$null; if ($LASTEXITCODE -eq 0 -and $n) { return "$n" } }
    $k = "LN_$l"; if ($script:M.ContainsKey($k)) { return $script:M[$k] } else { return $l }
}
$both = $false
function Test-SpeakOk($l) { return @("auto", "en", "ru", "ja") -contains $l }
# Japanese needs Hy-MT2 (llama-cpp-python in .venv + the GGUF) and, for speaking, misaki[ja] in the Kokoro venv.
$hymtRel = "hymt2-1.8b/Hy-MT2-1.8B-Q4_K_M.gguf"
$haveHymtFile = "import sys; from tinglongwu.models import models_dir; sys.exit(0 if (models_dir(None) / '$hymtRel').is_file() else 1)"
function Test-HymtFile { & $py -c $haveHymtFile; return ($LASTEXITCODE -eq 0) }
function Test-Llama { & $py -c "import llama_cpp" 2>$null; return ($LASTEXITCODE -eq 0) }
function Test-HyReady { return ((Test-HymtFile) -and (Test-Llama)) }   # then ja is translated directly (no OPUS pivot)
function Get-JaMissing {   # install commands the Japanese voice/translator still needs (empty = ready)
    $out = @()
    if (-not (Test-Llama)) { $out += "  & $py -m pip install llama-cpp-python --only-binary=:all: --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu" }
    & $kokoroPy -c "import misaki.ja, fugashi, jaconv, unidic_lite" 2>$null
    if ($LASTEXITCODE -ne 0) { $out += "  & `"$kokoroPy`" -m pip install misaki==0.9.4 pyopenjtalk-plus fugashi unidic-lite mojimoji jaconv addict regex" }
    return $out
}
while ($true) {
    $N = Get-LangName $L
    $ns = if (Test-SpeakOk $L) { "" } else { T 'UNSUPPORTED' $N }
    $au = if (Test-TlwuAutoUpdate) { T 'ON' } else { T 'OFF' }
    Write-Host ""
    Write-Host (T 'HEAD' $N)
    Write-Host (T 'OPT_MEET' $ns)
    if ($L -eq "auto") { Write-Host (T 'OPT_SPEAK_AUTO') } else { Write-Host (T 'OPT_SPEAK' $N $ns) }
    Write-Host (T 'OPT_LISTEN' $N)
    Write-Host (T 'OPT_LANG')
    Write-Host (T 'OPT_GUI')
    Write-Host (T 'OPT_UPDATE')
    Write-Host (T 'OPT_AUTOUPD' $au)
    Write-Host (T 'OPT_UILANG' (T 'LANGNAME'))
    $pick = Read-Host ">"
    if ($pick -eq "g") { & $tlwu --gui; exit }
    if ($pick -eq "u") { Invoke-TlwuManualUpdate $py; continue }
    if ($pick -eq "a") { Switch-TlwuAutoUpdate; continue }
    if ($pick -eq "i") { Select-TlwuUiLang; continue }
    if (-not $pick -or $pick -eq "1") { $mode = "2"; $both = $true }
    elseif ($pick -eq "2") { $mode = "2"; $both = $false }
    elseif ($pick -eq "3") { $mode = "1"; $both = $false }
    elseif ($pick -eq "l") {
        & $py -m tinglongwu.langs   # ja/en/ru first, the rest marked experimental (#58)
        $L = Read-Host (T 'ASK_LANG'); if (-not $L) { $L = "auto" }
        continue
    }
    else { Write-Host (T 'BAD_OPTION'); continue }
    if ($mode -eq "2" -and -not (Test-SpeakOk $L)) { Write-Host (T 'NO_SPEAK' $N); $both = $false; continue }
    break
}

$havePairs = "import sys; from tinglongwu.models import models_dir; d = models_dir(None) / 'opus'; sys.exit(0 if all((d / p).is_dir() for p in sys.argv[1:]) else 1)"
# language we speak: en | ru | follow (= whatever the other side spoke last)
$speak = $L
$listenLang = @("--lang", $L)
if ($L -eq "auto") {
    $listenLang = @("--lang", "auto", "--lang-candidates", $cands)
    if ($both) { $speak = "follow" }
    elseif ($mode -eq "2") {
        $speak = Read-Host (T 'ASK_SPEAK')
        if ($speak -ne "ru" -and $speak -ne "ja" -and $speak -ne "zh") { $speak = "en" }   # zh = I speak English (#134)
    }
}
$setupExe = ".\.venv\Scripts\tlwu-setup.exe"
# Language packs (#178): the install has zh+en; picking ja / ru offers that pack once with its size (default no).
foreach ($pk in (@($L, $speak) | Select-Object -Unique)) { if ($pk -eq "ja" -or $pk -eq "ru") { & $setupExe --pack-prompt $pk } }
function Invoke-Setup {  # never download silently: ask first (default = no)
    $ans = ""
    if ($env:TLWU_OFFLINE -eq "1") { Write-Host (T 'OFFLINE_MISSING' "tlwu-setup --online $($args -join ' ')"); exit 1 }  # #254
    try { $ans = Read-Host (T 'ASK_DOWNLOAD') } catch { $ans = "" }
    if ($ans -notmatch '^(y|yes)$') { Write-Host (T 'DOWNLOAD_DECLINED'); exit 1 }
    & $setupExe --allow-unpinned @args  # consent given above; until every pair is pinned in manifest.json (#254)
}
if ($mode -eq "2" -and ($speak -eq "ja" -or $speak -eq "follow")) {
    $missing = Get-JaMissing
    if ($missing) {
        if ($speak -eq "ja") { Write-Host (T 'JA_MISSING_FATAL'); $missing | ForEach-Object { Write-Host $_ }; exit 1 }
        Write-Host (T 'JA_MISSING_SKIP'); $missing | ForEach-Object { Write-Host $_ }
        $cands = "en,ru,zh"; $followLangs = "en,ru"
        $listenLang = @("--lang", "auto", "--lang-candidates", $cands)
    } elseif (-not (Test-HymtFile)) {
        Write-Host (T 'NO_HYMT')
        Invoke-Setup --hymt
    }
}
# The Russian voice model has no stated upstream license: opt-in only, never part of a default setup. Asked here
# (default N) only when Russian speech is about to be used; declining = Russian stays subtitles only.
function Test-Vosk {
    if (Test-Path $script:voskModel) { return $true }
    $d = & $py -c "from tinglongwu.models import models_dir, VOSK_TTS_RU_DIR; print(models_dir(None) / VOSK_TTS_RU_DIR)" 2>$null
    if ($d -and (Test-Path "$d")) { $script:voskModel = "$d"; return $true }
    return $false
}
$voskOff = $false
if ($mode -eq "2" -and ($speak -eq "ru" -or ($speak -eq "follow" -and $followLangs -match "ru")) -and -not (Test-Vosk)) {
    Write-Host (T 'VOSK_LICENSE')
    $vans = ""
    try { $vans = Read-Host (T 'ASK_VOSK') } catch { $vans = "" }
    if ($vans -match '^(y|yes)$') { & $setupExe --vosk-tts-ru }
    if (-not (Test-Vosk)) { Write-Host (T 'VOSK_OPTIN'); $voskOff = $true }
}
$hyNote = T 'HY_NOTE'
# Listen side extras (#42): --glossary when the user glossary folder has .txt files; on CPU with Hy-MT2 installed,
# ask whether to use it instead of OPUS (tinglongwu.menuopts: questions on stderr, one argument per line on stdout)
$xl = @()
if ($mode -eq "1" -or $both) {
    try { $xl = @(& $py -m tinglongwu.menuopts listen | Where-Object { $_ }) } catch { $xl = @() }
}
if ($both) {
    $need = if ($L -eq "en") { @("en-zh") } elseif ($L -eq "auto") { @("en-zh", "ru-en", "zh-en", "en-ru") } else { @("$L-en", "en-zh", "zh-en", "en-ru") }
    & $py -c $havePairs @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host (T 'NO_MODEL_FOR' $L)
        if ($L -eq "en") { Invoke-Setup --to zh } elseif ($L -eq "auto") { Invoke-Setup --pair zh-en --pair en-zh --pair ru-en --pair en-ru } else { Invoke-Setup --langs $L --to zh --pair en-ru }
    }
    if ($speak -eq "follow") {
        if (-not (Test-Path $kokoroModel)) { Write-Host (T 'NO_KOKORO_EN' $kokoroModel); exit 1 }
    }
    # #285: one window: the speak side runs the listen side in a thread (--listen, last on its command line),
    # both directions share one Hy-MT2 and one CPU thread budget (TLWU_BOTH, tinglongwu/threads.py)
    $env:TLWU_BOTH = "1"
    Remove-Item $followFile -ErrorAction SilentlyContinue
    $listenArgs = @($listenLang) + @($wL) + @($xl)
    if ($L -eq "auto") { $listenArgs += @("--lang-file", $followFile) }
}
if ($mode -eq "1") {
    Write-Host (T 'BANNER_LISTEN' $L)
    if ($L -eq "en") { $need = @("en-zh"); $setup = @("--to", "zh") }
    elseif ($L -eq "auto") {
        $need = @("en-zh", "ru-en"); $setup = @("--langs", "ru", "--to", "zh")
        if ($cands -match "ja" -and -not (Test-HyReady)) { $need += "ja-en"; $setup = @("--langs", "ru,ja", "--to", "zh"); Write-Host $hyNote }
    }
    elseif ($L -eq "ja" -and (Test-HyReady)) { $need = @(); $setup = @() }   # Hy-MT2: direct ja -> zh, no OPUS pair
    else { $need = @("$L-en", "en-zh"); $setup = @("--langs", $L, "--to", "zh"); if ($L -eq "ja") { Write-Host $hyNote } }
    & $py -c $havePairs @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host (T 'NO_MODEL_FOR' $L)
        Invoke-Setup @setup
    }
    $stream = @(Get-TlwuStreamArgs $py)   # live drafts (#60), default no
    & $tlwu @listenLang @wL @xl @stream
    exit
}
Write-Host (T 'BANNER_SPEAK' $(if ($speak -eq 'follow') { T 'FOLLOW_LABEL' } else { $speak }))

if ($speak -eq "follow") {
    # both directions; Kokoro (en, and ja on its first Japanese sentence) loads now, Vosk (ru) on the first Russian sentence
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "follow", "--follow-file", $followFile, "--mt", "opus",
             "--follow-langs", $followLangs,
             "--tts-engine", "kokoro", "--tts-python", $kokoroPy, "--tts-model", $kokoroModel,
             "--vosk-python", $voskPy, "--vosk-model", $voskModel) + $wS
    $txt = @()
} elseif ($speak -eq "ja") {
    # zh->ja and the back-translation ja->zh are both direct Hy-MT2 (no OPUS pairs); Kokoro speaks with misaki phonemes
    if (-not (Test-Path $kokoroModel)) { Write-Host (T 'NO_KOKORO' $kokoroModel); exit 1 }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "ja", "--mt", "opus", "--tts-engine", "kokoro",
             "--tts-python", $kokoroPy, "--tts-model", $kokoroModel, "--speaker", $kokoroVoice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "ja", "--mt", "opus") + $wS
} elseif ($speak -eq "zh") {
    # reverse direction (#134): you speak English, the other side hears Chinese (Hy-MT2 en->zh if installed, else
    # OPUS en-zh; zh-en is the back-translation). Kokoro Chinese voices need misaki[zh] in the Kokoro venv.
    & $py -c $havePairs en-zh zh-en
    if ($LASTEXITCODE -ne 0 -and -not (Test-HyReady)) {
        Write-Host (T 'NO_MODEL_ZH_EN')
        Invoke-Setup --pair zh-en --pair en-zh
    }
    if (-not (Test-Path $kokoroModel)) { Write-Host (T 'NO_KOKORO' $kokoroModel); exit 1 }
    & $kokoroPy -c "import misaki.zh, jieba, pypinyin, cn2an" 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "  & `"$kokoroPy`" -m pip install `"misaki[zh]==0.9.4`""; exit 1 }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--from", "en", "--mt", "opus", "--tts-engine", "kokoro",
             "--tts-python", $kokoroPy, "--tts-model", $kokoroModel, "--speaker", $kokoroVoice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--from", "en", "--mt", "opus") + $wS
} elseif ($speak -eq "en") {
    # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
    & $py -c $havePairs zh-en en-zh
    if ($LASTEXITCODE -ne 0) {
        Write-Host (T 'NO_MODEL_ZH_EN')
        Invoke-Setup --pair zh-en --pair en-zh
    }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "en", "--mt", "opus", "--tts-engine", "kokoro",
             "--tts-python", $kokoroPy, "--tts-model", $kokoroModel, "--speaker", $kokoroVoice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "en", "--mt", "opus") + $wS
} else {
    & $py -c $havePairs zh-en en-ru ru-en en-zh
    if ($LASTEXITCODE -ne 0) {
        Write-Host (T 'NO_MODEL_ZH_RU')
        Invoke-Setup --pair zh-en --pair en-ru --pair ru-en --pair en-zh
    }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--tts-engine", "vosk", "--tts-python", $voskPy,
             "--tts-model", $voskModel, "--speaker", $voice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "ru") + $wS
    if ($voskOff -and -not $both) { & $py @txt; exit }   # no Russian voice: subtitles only
}
if ($both) {
    $ac = Read-Host (T 'ASK_AUTOSEND')
    if ($ac -eq "y") { $rev += "--no-confirm" }
    Write-Host (T 'MEETING_BANNER')
    & $py @rev --device "CABLE Input" --listen @listenArgs
    exit
}
Write-Host (T 'CONFIRM_HELP')
Write-Host (T 'OUT1')
Write-Host (T 'OUT2')
Write-Host (T 'OUT3')
Write-Host (T 'OUT4')
$out = Read-Host (T 'ASK_OUT')
if ($out -ne "4") {
    $ac = Read-Host (T 'ASK_AUTOSEND')
    if ($ac -eq "y") { $rev += "--no-confirm" }
}
switch ($out) {
    "1" { & $py @rev --device "CABLE Input" }
    "2" {
        $pv = Read-Host (T 'ASK_PHONES')
        & $py @rev --device "CABLE Input" --preview-device $pv
    }
    "3" { & $py @rev }
    "4" { & $py @txt }
    default { Write-Host (T 'BAD_OPTION'); exit 1 }
}
