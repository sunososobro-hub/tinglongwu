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

Write-Host "tinglongWu"
Write-Host "  1) 聽  外語 -> 字幕：中文（預設）"
Write-Host "  2) 說  中文 -> 語音：外語"
Write-Host "  3) 圖形介面（GUI）"
$mode = Read-Host "選 1-3 [Enter=1]"
if (-not $mode) { $mode = "1" }
if ($mode -eq "3") { & $tlwu --gui; exit }

Write-Host "語言代碼：en 英文、ru 俄文、ja 日文、ko 韓文、de 德文、fr 法文、es 西文"
if ($mode -eq "1") {
    $L = Read-Host "聽哪種語言 [Enter=en]"; if (-not $L) { $L = "en" }
    Write-Host "聽  $L -> 字幕：中文"
    if ($L -eq "en") { $need = @("en-zh"); $setup = @("--to", "zh") } else { $need = @("$L-en", "en-zh"); $setup = @("--langs", $L, "--to", "zh") }
    & $py -c "import sys; from tinglongwu.models import models_dir; d = models_dir(None) / 'opus'; sys.exit(0 if all((d / p).is_dir() for p in sys.argv[1:]) else 1)" @need
    if ($LASTEXITCODE -ne 0) {
        Write-Host "還沒有 $L 的翻譯模型，現在連網下載（只有這一次）..."
        & .\.venv\Scripts\tlwu-setup.exe @setup
    }
    & $tlwu --lang $L
    exit
}
if ($mode -ne "2") { Write-Host "沒有這個選項"; exit 1 }
$L = Read-Host "說成哪種語言 [Enter=ru，目前只有 ru]"; if (-not $L) { $L = "ru" }; Write-Host "說  中文 -> 語音：$L"
if ($L -ne "ru") { Write-Host "「說」目前只支援 ru（俄文）；其他語言還沒有語音模型。"; exit 1 }

$rev = @("-m", "tinglongwu.reverse.proto", "--mic", "--tts-engine", "vosk", "--tts-python", $voskPy,
         "--tts-model", $voskModel, "--speaker", $voice)
Write-Host "每句先確認：Enter=送出  x=丟掉  v0-v4=換聲音"
Write-Host "  1) 送進 Meet（需 VB-CABLE；Meet 麥克風選 CABLE Output）"
Write-Host "  2) 送進 Meet，自己耳機也聽得到"
Write-Host "  3) 不進 Meet，直接從耳機/喇叭念出來"
Write-Host "  4) 只看翻譯文字（不發聲）"
$out = Read-Host "選 1-4"
switch ($out) {
    "1" { & $py @rev --device "CABLE Input" }
    "2" {
        $pv = Read-Host "你的耳機名稱（一部分即可，可先用 --list-speakers 查）"
        & $py @rev --device "CABLE Input" --preview-device $pv
    }
    "3" { & $py @rev }
    "4" { & $py -m tinglongwu.reverse.proto --mic --no-tts }
    default { Write-Host "沒有這個選項"; exit 1 }
}
