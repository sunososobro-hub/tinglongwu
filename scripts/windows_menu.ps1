# tinglongWu helper menu (Windows, PowerShell 5.1). Run from the repo:
#   powershell -ExecutionPolicy Bypass -File scripts\windows_menu.ps1
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
$voice = if ($env:VOICE) { $env:VOICE } else { "4" }
# English speech: Kokoro in its own venv (it bundles GPL espeak, so never install it into .venv):
#   python -m venv $HOME\tts-kokoro; & $HOME\tts-kokoro\Scripts\pip install "kokoro-onnx==0.6.1"
#   model dir needs kokoro-v1.0.int8.onnx + voices-v1.0.bin
$kokoroPy = if ($env:KOKORO_PY) { $env:KOKORO_PY } else { "$HOME\tts-kokoro\Scripts\python.exe" }
$kokoroModel = if ($env:KOKORO_MODEL) { $env:KOKORO_MODEL } else { "$HOME\models\kokoro" }
$kokoroVoice = if ($env:KOKORO_VOICE) { $env:KOKORO_VOICE } else { "0" }

Write-Host "tinglongWu"
Write-Host "  Enter) 聽  英文 -> 中文字幕（預設）"
Write-Host "  1)     聽  其他語言 -> 中文字幕"
Write-Host "  2)     說  中文 -> 英文語音"
Write-Host "  3)     說  中文 -> 俄文語音"
Write-Host "  4)     圖形介面（GUI）"
$pick = Read-Host ">"
if ($pick -eq "4") { & $tlwu --gui; exit }
if (-not $pick) { $mode = "1"; $L = "en" }
elseif ($pick -eq "1") {
    $mode = "1"
    Write-Host "語言代碼：ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文"
    $L = Read-Host "聽哪種語言 [Enter=en]"; if (-not $L) { $L = "en" }
}
elseif ($pick -eq "2") { $mode = "2"; $L = "en" }
elseif ($pick -eq "3") { $mode = "2"; $L = "ru" }
else { Write-Host "沒有這個選項"; exit 1 }

$havePairs = "import sys; from tinglongwu.models import models_dir; d = models_dir(None) / 'opus'; sys.exit(0 if all((d / p).is_dir() for p in sys.argv[1:]) else 1)"
if ($mode -eq "1") {
    Write-Host "聽  $L -> 中文字幕"
    if ($L -eq "en") { $need = @("en-zh"); $setup = @("--to", "zh") } else { $need = @("$L-en", "en-zh"); $setup = @("--langs", $L, "--to", "zh") }
    & $py -c $havePairs @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
        & .\.venv\Scripts\tlwu-setup.exe @setup
    }
    & $tlwu --lang $L
    exit
}
Write-Host "說  中文 -> $L 語音"

if ($L -eq "en") {
    # zh->en is a direct OPUS pair; en-zh is only for the back-translation shown while confirming
    & $py -c $havePairs zh-en en-zh
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 中文->英文 的翻譯模型，現在連網下載（只有這一次）..."
        & .\.venv\Scripts\tlwu-setup.exe --pair zh-en --pair en-zh
    }
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--to", "en", "--mt", "opus", "--tts-engine", "kokoro",
             "--tts-python", $kokoroPy, "--tts-model", $kokoroModel, "--speaker", $kokoroVoice)
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "en", "--mt", "opus")
} else {
    $rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--tts-engine", "vosk", "--tts-python", $voskPy,
             "--tts-model", $voskModel, "--speaker", $voice)
    $txt = @("-m", "tinglongwu.reverse.proto", "--mic", "--no-tts", "--to", "ru")
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
