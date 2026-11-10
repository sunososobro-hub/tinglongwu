# tinglongWu one-click installer (Windows PowerShell 5.1). Safe to re-run: finished steps are skipped.
#   install.bat                     normal install
#   install.ps1 -DryRun             detect only, print every command, install nothing
#   -SkipModels  -SkipShortcut      skip step 5 / 6      -Cpu  force the CPU path
param([switch]$DryRun, [switch]$SkipModels, [switch]$SkipShortcut, [switch]$Cpu)

$ErrorActionPreference = 'Continue'
# CI-only: TLWU_SKIP_MODELS=1 / TLWU_SKIP_SHORTCUT=1 behave like -SkipModels / -SkipShortcut (used by .github/workflows/install-test.yml)
if ($env:TLWU_SKIP_MODELS) { $SkipModels = [switch]$true }
if ($env:TLWU_SKIP_SHORTCUT) { $SkipShortcut = [switch]$true }
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
$Venv = Join-Path $Root '.venv'
$VPy  = Join-Path $Venv 'Scripts\python.exe'
$VPyW = Join-Path $Venv 'Scripts\pythonw.exe'
$VGui = Join-Path $Venv 'Scripts\tlwu-gui.exe'
$VSet = Join-Path $Venv 'Scripts\tlwu-setup.exe'
$Installed = New-Object System.Collections.ArrayList
$Warnings  = New-Object System.Collections.ArrayList
$LlamaChk  = 'from tinglongwu.llamacheck import self_check as s; print(s(gpu=True)[0])'   # loads, fits this CPU (#74), has GPU offload

$LlamaWhy  = 'from tinglongwu.llamacheck import self_check as s; print(s(gpu=True)[1])'
function Step($n, $zh, $en) { Write-Host ""; Write-Host "[$n/7] $zh / $en" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  OK  $m" -ForegroundColor Green }
function Info($m) { Write-Host "      $m" }
function Warn($m) { Write-Host "  !!  $m" -ForegroundColor Yellow; [void]$Warnings.Add($m) }
function Die($zh, $en) {
    Write-Host ""; Write-Host "  XX  $zh" -ForegroundColor Red; Write-Host "      $en" -ForegroundColor Red
    exit 1
}
# Run a native command; in -DryRun only print it. Returns $true on exit code 0.
function Run($exe, [string[]]$a) {
    $line = "$exe " + ($a -join ' ')
    if ($DryRun) { Write-Host "  [dry-run] $line" -ForegroundColor DarkGray; return $true }
    Write-Host "  > $line" -ForegroundColor DarkGray
    & $exe @a
    return ($LASTEXITCODE -eq 0)
}
function Capture($exe, [string[]]$a) {   # stdout of a native command, '' on failure
    try { $o = & $exe @a 2>$null; if ($LASTEXITCODE -eq 0) { return (($o | Out-String).Trim()) } } catch { }
    return ''
}

Write-Host "=== 聽攏有 tinglongWu installer ===" -ForegroundColor White
if ($DryRun) { Write-Host "*** DRY RUN: nothing will be installed or downloaded / 試跑模式：不會安裝或下載任何東西 ***" -ForegroundColor Yellow }
Info "Folder / 資料夾: $Root"

# ---------------------------------------------------------------- 1. Python
Step 1 '檢查 Python 3.10-3.12' 'Check Python 3.10-3.12'
. (Join-Path $Root 'scripts\python_bootstrap.ps1')
function FindPy {
    foreach ($v in '3.12', '3.11', '3.10') {
        $p = Capture 'py' @("-$v", '-c', 'import sys;print(sys.executable)')
        if ($p -and (Test-Path $p)) { return $p }
    }
    $p = Capture 'python' @('-c', 'import sys;print(sys.executable if (3,10)<=sys.version_info[:2]<=(3,12) else "")')
    if ($p -and (Test-Path $p)) { return $p }
    return $null
}
$Py = FindPy
if (-not $Py) {
    # fresh Windows (#67): install Python ourselves, per-user, no admin (pinned python.org installer, SHA256-checked; winget as fallback)
    Info "找不到 Python 3.10 ~ 3.12，自動安裝 Python 3.12（僅目前使用者，不需系統管理員）"
    Info "Python 3.10-3.12 not found: installing Python 3.12 for this user (no admin needed)"
    $Py = Install-TlwuPython -DryRun:$DryRun
    if (-not $Py -and -not $DryRun) {   # installed somewhere else (e.g. winget): pick up the new user PATH and look again
        $env:Path = "$([Environment]::GetEnvironmentVariable('Path', 'User'));$env:Path"
        $Py = FindPy
    }
    if ($Py) { if (-not $DryRun) { [void]$Installed.Add('Python 3.12 (per-user)') } }
    else {
        Write-Host ""
        Write-Host "  自動安裝 Python 失敗 / Could not install Python automatically." -ForegroundColor Red
        Write-Host "  請到 https://www.python.org/downloads/windows/ 下載 Python 3.12，"
        Write-Host "  安裝時務必勾選 'Add python.exe to PATH'，裝好後再按一次 install.bat。"
        Write-Host "  Download Python 3.12 from python.org, TICK 'Add python.exe to PATH', then run install.bat again."
        exit 1
    }
}
Ok "Python: $Py  ($(Capture $Py @('--version')))"

# ---------------------------------------------------------------- 2. GPU
Step 2 '偵測 NVIDIA 顯示卡' 'Detect NVIDIA GPU'
$Gpu = $false; $FreeMB = 0; $TotalMB = 0; $GpuName = ''
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi -and -not $Cpu) {
    $q = Capture 'nvidia-smi' @('--query-gpu=name,memory.total,memory.free', '--format=csv,noheader,nounits')
    if ($q) {
        $f = ($q -split "`r?`n")[0] -split ',\s*'
        if ($f.Count -ge 3) { $GpuName = $f[0]; $TotalMB = [int]$f[1]; $FreeMB = [int]$f[2]; $Gpu = $true }
    }
}
if ($Cpu) { Info "-Cpu given: forcing CPU path / 已指定 -Cpu，走 CPU 路線" }
if ($Gpu) {
    Ok ("GPU: {0}, {1} MB total, {2} MB free" -f $GpuName, $TotalMB, $FreeMB)
    # plan by TOTAL VRAM (free VRAM is noisy at install time; the runtime --engine auto re-checks free VRAM at start-up)
    if     ($TotalMB -ge 7500) { $Whisper = 'large-v3-turbo'; $QwenFlag = '--qwen';  $QwenTxt = 'Qwen3-8B + Qwen3-4B (~7.5 GB)';  $SizeGB = '~10 GB' }
    elseif ($TotalMB -ge 5500) { $Whisper = 'large-v3-turbo'; $QwenFlag = '--qwen4b'; $QwenTxt = 'Qwen3-4B only (~2.5 GB)';       $SizeGB = '~5 GB' }
    elseif ($TotalMB -ge 3500) { $Whisper = 'small';          $QwenFlag = '--qwen4b'; $QwenTxt = 'Qwen3-4B only (~2.5 GB)';       $SizeGB = '~3.5 GB' }
    else                       { $Whisper = 'small';          $QwenFlag = $null;      $QwenTxt = 'none (low VRAM: OPUS engine)'; $SizeGB = '~1 GB' }
    Info "Tier chosen by total VRAM ($TotalMB MB; $FreeMB MB free now). 依顯示卡總容量選擇。"
    Info "Tip: close GPU-heavy apps (games, browsers with video, other tlwu) before the first run. 首次執行前請關閉佔用 GPU 的程式。"
    Ok "GPU path / GPU 路線: whisper=$Whisper, Qwen=$QwenTxt"
} else {
    if (-not $Cpu) { Warn "No NVIDIA GPU / nvidia-smi not found: CPU path (slower, OPUS engine). 找不到 NVIDIA 顯示卡，走 CPU 路線。" }
    $Whisper = 'small'; $QwenFlag = $null; $QwenTxt = 'none (CPU: OPUS engine)'; $SizeGB = '~1 GB'
}
Info "Expected model download / 預計下載模型: $SizeGB (plus ~3 GB of Python packages / 另有約 3 GB 套件)"

# ---------------------------------------------------------------- 3. venv + packages
Step 3 '建立 .venv 並安裝套件' 'Create .venv and install packages'
if (Test-Path $VPy) { Ok ".venv already exists / .venv 已存在，沿用" }
else {
    if (-not (Run $Py @('-m', 'venv', $Venv))) { Die '建立 .venv 失敗' 'venv creation failed' }
    [void]$Installed.Add('.venv')
}
if (-not $DryRun -and -not (Test-Path $VPy)) { Die '.venv 不完整' '.venv is incomplete; delete the .venv folder and re-run' }
$ok = Run $VPy @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
if ($ok) { $ok = Run $VPy @('-m', 'pip', 'install', '-r', 'requirements.txt') }
if ($ok) { $ok = Run $VPy @('-m', 'pip', 'install', '-e', '.', '--no-deps') }
if (-not $ok) { Die 'pip 安裝失敗（請確認網路連線後重跑）' 'pip install failed (check your internet connection, then re-run install.bat)' }
if (-not $DryRun -and -not (Test-Path $VSet)) { Die '找不到 tlwu-setup.exe' 'entry points were not created' }
[void]$Installed.Add('Python packages + tlwu / tlwu-gui / tlwu-setup')
Ok "Packages installed / 套件完成"

# ---------------------------------------------------------------- 4. GPU libs + llama-cpp
Step 4 'GPU 函式庫與 Qwen 引擎' 'GPU libraries and Qwen engine'
$Engine = 'opus'
if (-not $Gpu) { Info "CPU path: skipped / CPU 路線略過" }
else {
    if (Run $VSet @('--gpu')) { [void]$Installed.Add('CUDA 12 libs (cuBLAS, cuDNN)') }
    else { Warn "tlwu-setup --gpu failed; Whisper will run on CPU. 失敗，將用 CPU 跑語音辨識。" }
    if ($QwenFlag) {
        $have = $false
        if (-not $DryRun) { $have = ((Capture $VPy @('-c', $LlamaChk)) -eq 'True') }
        if ($have) { Ok "llama-cpp-python (GPU) already works / 已可用"; $Engine = 'qwen' }
        else {
            $ok = Run $VPy @('-m', 'pip', 'install', '-r', 'requirements-qwen.txt', '--only-binary=:all:')
            if ($ok -and -not $DryRun) {
                $ok = ((Capture $VPy @('-c', $LlamaChk)) -eq 'True')
                if (-not $ok) {   # same version already installed from another index (e.g. an AVX-512 build, #74): replace it
                    [void](Run $VPy @('-m', 'pip', 'install', '-r', 'requirements-qwen.txt', '--only-binary=:all:', '--force-reinstall', '--no-deps'))
                    $ok = ((Capture $VPy @('-c', $LlamaChk)) -eq 'True')
                }
                if (-not $ok) { Warn "llama-cpp-python installed but failed the self-check: $(Capture $VPy @('-c', $LlamaWhy))" }
            }
            if ($ok) { $Engine = 'qwen'; [void]$Installed.Add('llama-cpp-python (CUDA wheel)') }
            else {
                Warn "No usable prebuilt llama-cpp-python CUDA wheel for this PC: using the OPUS engine (still works, less accurate). 找不到合適的預編譯 wheel，改用 OPUS 引擎。"
                $QwenFlag = $null
            }
            if ($DryRun) { $Engine = 'qwen (if the wheel installs)' }
        }
    } else { Info "Low VRAM: Qwen skipped, OPUS engine / 顯存不足，略過 Qwen" }
}

# ---------------------------------------------------------------- 5. models
Step 5 '下載模型（最花時間）' 'Download models (the slow part)'
if ($SkipModels) { Info "-SkipModels: skipped / 略過" }
else {
    Info "Downloading $SizeGB. Already downloaded files are skipped. 已下載的會自動跳過。"
    $ok = Run $VSet @('--whisper', $Whisper, '--langs', 'ru,ja,en', '--to', 'zh')
    if ($ok) { [void]$Installed.Add("Whisper '$Whisper' + OPUS translation ru/ja/en -> zh") }
    else { Die '模型下載失敗（請確認網路後重跑）' 'model download failed (check internet, then re-run install.bat; finished parts are kept)' }
    if ($QwenFlag) {
        if (Run $VSet @($QwenFlag)) { [void]$Installed.Add("Qwen model ($QwenTxt)") }
        else { Warn "Qwen download failed; will use the OPUS engine. Re-run install.bat to retry."; $Engine = 'opus' }
    }
}

# ---------------------------------------------------------------- 6. shortcut
Step 6 '建立桌面捷徑' 'Create desktop shortcut'
if ($SkipShortcut) { Info "-SkipShortcut: skipped / 略過" }
else {
    $Desktop = [Environment]::GetFolderPath('Desktop')
    $Lnk = Join-Path $Desktop '聽攏有 tinglongWu.lnk'
    if (Test-Path $VGui) { $target = $VGui; $args2 = '' } else { $target = $VPyW; $args2 = '-m tinglongwu.gui' }
    if ($DryRun) { Write-Host "  [dry-run] shortcut: $Lnk -> $target $args2 (start in $Root)" -ForegroundColor DarkGray }
    else {
        try {
            $sh = New-Object -ComObject WScript.Shell
            $s = $sh.CreateShortcut($Lnk)
            $s.TargetPath = $target; $s.Arguments = $args2; $s.WorkingDirectory = $Root
            $s.Description = '聽攏有 tinglongWu - offline speech translator'
            $s.Save()
            Ok "Shortcut: $Lnk"; [void]$Installed.Add('Desktop shortcut')
        } catch { Warn "Could not create the shortcut ($_). Start it from $target instead." }
    }
}

# ---------------------------------------------------------------- 7. summary
Step 7 '完成' 'Summary'
Write-Host ""
Write-Host "  Installed / 已安裝:" -ForegroundColor White
foreach ($i in $Installed) { Write-Host "    - $i" }
Write-Host "  Translation engine / 翻譯引擎: $Engine" -ForegroundColor White
if ($Warnings.Count -gt 0) {
    Write-Host "  Warnings / 注意:" -ForegroundColor Yellow
    foreach ($w in $Warnings) { Write-Host "    - $w" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Launch / 啟動: double-click the desktop shortcut '聽攏有 tinglongWu' (or $VGui)" -ForegroundColor White
Write-Host "  Uninstall / 移除: delete this folder ($Root; models are in its models\ folder) and the desktop shortcut."
Write-Host "                    刪除此資料夾（模型在 models\ 內）與桌面捷徑即可。"
if ($DryRun) { Write-Host ""; Write-Host "  (dry run finished, nothing was changed / 試跑結束，未做任何更動)" -ForegroundColor Yellow }
exit 0
