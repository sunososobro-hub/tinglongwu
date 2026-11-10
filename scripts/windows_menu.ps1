# tinglongWu helper menu (Windows, PowerShell 5.1). Run from the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\windows_menu.ps1
# Env WHISPER sets the speech model (empty = default), e.g.  $env:WHISPER="medium"; powershell -File scripts\windows_menu.ps1
# "Speak" needs VB-CABLE (Meet microphone = "CABLE Output") and Vosk-TTS in its own venv:
#   python -m venv $HOME\tts-vosk; & $HOME\tts-vosk\Scripts\pip install "vosk-tts==0.3.61"
#   model: https://alphacephei.com/vosk/models/vosk-model-tts-ru-0.7-multi.zip -> $HOME\vosk-model-tts-ru-0.7-multi
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$env:PYTHONUTF8 = "1"
$py = ".\.venv\Scripts\python.exe"
$tlwu = ".\.venv\Scripts\tlwu.exe"
$voskPy = if ($env:VOSK_PY) { $env:VOSK_PY } else { "$HOME\tts-vosk\Scripts\python.exe" }
$voskModel = if ($env:VOSK_MODEL) { $env:VOSK_MODEL } else { "$HOME\vosk-model-tts-ru-0.7-multi" }
$wL = @(); $wS = @()
if ($env:WHISPER) { $wL = @("--model", $env:WHISPER); $wS = @("--whisper", $env:WHISPER) }
$voice = if ($env:VOICE) { $env:VOICE } else { "4" }
# English speech: Kokoro in its own venv (it bundles GPL espeak, so never install it into .venv):
#   python -m venv $HOME\tts-kokoro; & $HOME\tts-kokoro\Scripts\pip install "kokoro-onnx==0.6.1"
#   model dir needs kokoro-v1.0.int8.onnx + voices-v1.0.bin
$kokoroPy = if ($env:KOKORO_PY) { $env:KOKORO_PY } else { "$HOME\tts-kokoro\Scripts\python.exe" }
$kokoroModel = if ($env:KOKORO_MODEL) { $env:KOKORO_MODEL } else { "$HOME\models\kokoro" }
$kokoroVoice = if ($env:KOKORO_VOICE) { $env:KOKORO_VOICE } else { "0" }

$L = if ($env:TLWU_LANG) { $env:TLWU_LANG } else { "auto" }
$cands = "en,ru,zh"   # auto: language follows whoever speaks, per sentence
$followFile = Join-Path $env:TEMP "tlwu_follow_lang"
$names = @{ auto = "自動（英/俄）"; en = "英文"; ru = "俄文"; ja = "日文"; ko = "韓文"; de = "德文"; fr = "法文"; es = "西文" }
$both = $false
while ($true) {
    $N = if ($names.ContainsKey($L)) { $names[$L] } else { $L }
    Write-Host ""
    Write-Host "tinglongWu    對方語言：$N    （按 L 可改語言）"
    Write-Host "  1) 開會  聽＋說，送進 Meet（要戴耳機麥克風）"
    if ($L -eq "auto") { Write-Host "  2) 純說  中文 -> 英文或俄文語音" } else { Write-Host "  2) 純說  中文 -> ${N}語音" }
    Write-Host "  3) 純聽  $N -> 中文字幕"
    Write-Host "  L) 改語言"
    Write-Host "  G) 圖形介面（GUI）"
    $pick = Read-Host ">"
    if ($pick -eq "g") { & $tlwu --gui; exit }
    if (-not $pick -or $pick -eq "1") { $mode = "2"; $both = $true }
    elseif ($pick -eq "2") { $mode = "2" }
    elseif ($pick -eq "3") { $mode = "1" }
    elseif ($pick -eq "l") {
        Write-Host "語言代碼：auto 自動（英/俄）、en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文（開會/純說目前只支援 auto、en、ru）"
        $L = Read-Host "對方說哪種語言 [Enter=auto]"; if (-not $L) { $L = "auto" }
        continue
    }
    else { Write-Host "沒有這個選項"; continue }
    if ($mode -eq "2" -and $L -ne "auto" -and $L -ne "en" -and $L -ne "ru") { Write-Host "$N 目前只能「純聽」；開會/純說只支援英文、俄文。"; continue }
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
        $speak = Read-Host "你要說給對方的語言？[Enter=en 英文 / ru 俄文]"
        if ($speak -ne "ru") { $speak = "en" }
    }
}
$setupExe = ".\.venv\Scripts\tlwu-setup.exe"
if ($both) {
    $need = if ($L -eq "en") { @("en-zh") } elseif ($L -eq "auto") { @("en-zh", "ru-en", "zh-en", "en-ru") } else { @("$L-en", "en-zh", "zh-en", "en-ru") }
    & $py -c $havePairs @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
        if ($L -eq "en") { & $setupExe --to zh } elseif ($L -eq "auto") { & $setupExe --pair zh-en --pair en-zh --pair ru-en --pair en-ru } else { & $setupExe --langs $L --to zh --pair en-ru }
    }
    if ($speak -eq "follow") {
        if (-not (Test-Path $kokoroModel)) { Write-Host "找不到英文語音模型 $kokoroModel（Kokoro）"; exit 1 }
        if (-not (Test-Path $voskModel)) { Write-Host "找不到俄文語音模型 $voskModel（Vosk）"; exit 1 }
    }
    # listen in its own window; speak in this one
    Remove-Item $followFile -ErrorAction SilentlyContinue
    $listenArgs = $listenLang + $wL
    if ($L -eq "auto") { $listenArgs += @("--lang-file", $followFile) }
    $listen = Start-Process -FilePath (Resolve-Path $tlwu) -ArgumentList $listenArgs -PassThru
}
if ($mode -eq "1") {
    Write-Host "聽  $L -> 中文字幕"
    if ($L -eq "en") { $need = @("en-zh"); $setup = @("--to", "zh") } elseif ($L -eq "auto") { $need = @("en-zh", "ru-en"); $setup = @("--langs", "ru", "--to", "zh") } else { $need = @("$L-en", "en-zh"); $setup = @("--langs", $L, "--to", "zh") }
    & $py -c $havePairs @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
        & .\.venv\Scripts\tlwu-setup.exe @setup
    }
    & $tlwu @listenLang @wL
    exit
}
Write-Host "說  中文 -> $speak 語音"

if ($speak -eq "follow") {
    # both directions; Kokoro (en) loads now, Vosk (ru) on the first Russian sentence
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "follow", "--follow-file", $followFile, "--mt", "opus",
             "--tts-engine", "kokoro", "--tts-python", $kokoroPy, "--tts-model", $kokoroModel,
             "--vosk-python", $voskPy, "--vosk-model", $voskModel) + $wS
    $txt = @()
} elseif ($speak -eq "en") {
    # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
    & $py -c $havePairs zh-en en-zh
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 中文->英文 的翻譯模型，現在連網下載（只有這一次）..."
        & .\.venv\Scripts\tlwu-setup.exe --pair zh-en --pair en-zh
    }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "en", "--mt", "opus", "--tts-engine", "kokoro",
             "--tts-python", $kokoroPy, "--tts-model", $kokoroModel, "--speaker", $kokoroVoice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "en", "--mt", "opus") + $wS
} else {
    & $py -c $havePairs zh-en en-ru ru-en en-zh
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 中文->俄文 的翻譯模型，現在連網下載（只有這一次）..."
        & $setupExe --pair zh-en --pair en-ru --pair ru-en --pair en-zh
    }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--tts-engine", "vosk", "--tts-python", $voskPy,
             "--tts-model", $voskModel, "--speaker", $voice) + $wS
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "ru") + $wS
}
if ($both) {
    $ac = Read-Host "每句直接送、不確認？[Enter=要確認 / y=直接送]"
    if ($ac -eq "y") { $rev += "--no-confirm" }
    Write-Host "開會模式：另一個視窗是對方的話（[EN]/[RU]/[ZH] 原文＋中文字幕）；你在這裡講中文會送進 Meet（Meet 麥克風選 CABLE Output）。請戴耳機。"
    try { & $py @rev --device "CABLE Input" }
    finally { if ($listen -and -not $listen.HasExited) { Stop-Process -Id $listen.Id -Force } }
    exit
}
Write-Host "每句先確認：Enter=送出  x=丟掉  v0-v4=換聲音"
Write-Host "  1) 送進 Meet（需 VB-CABLE；Meet 麥克風選 CABLE Output）"
Write-Host "  2) 送進 Meet，自己耳機也聽得到"
Write-Host "  3) 不進 Meet，直接從耳機/喇叭念出來"
Write-Host "  4) 只看翻譯文字（不發聲）"
$out = Read-Host "選 1-4"
if ($out -ne "4") {
    $ac = Read-Host "每句直接送、不確認？[Enter=要確認 / y=直接送]"
    if ($ac -eq "y") { $rev += "--no-confirm" }
}
switch ($out) {
    "1" { & $py @rev --device "CABLE Input" }
    "2" {
        $pv = Read-Host "你的耳機名稱（一部分即可，可先用 --list-speakers 查）"
        & $py @rev --device "CABLE Input" --preview-device $pv
    }
    "3" { & $py @rev }
    "4" { & $py @txt }
    default { Write-Host "沒有這個選項"; exit 1 }
}
