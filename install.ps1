# tinglongWu one-click installer (Windows PowerShell 5.1). Safe to re-run: finished steps are skipped.
#   install.bat                     normal install
#   install.ps1 -DryRun             detect only, print every command, install nothing
#   -SkipModels  -SkipShortcut      skip steps 5-6 / 7   -Cpu  force the CPU path
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
$LlamaChkCpu = 'from tinglongwu.llamacheck import self_check as s; print(s()[0])'   # CPU build for Japanese (#126)
$LlamaWhyCpu = 'from tinglongwu.llamacheck import self_check as s; print(s()[1])'
. (Join-Path (Join-Path $Root 'scripts') 'install_helpers.ps1')   # time estimates + Invoke-TlwuNative (#87)
$Gpu = $false; $ModelGB = 0; $JaHymt = $false
function Step($n, $zh, $en) {
    Write-Host ""; Write-Host "[$n/8] $zh / $en" -ForegroundColor Cyan
    Write-Host "      $(Format-TlwuEstimate (Get-TlwuStepEstimate $n $Gpu $ModelGB))" -ForegroundColor DarkCyan
}
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
    return ((Invoke-TlwuNative $exe $a) -eq 0)   # output goes to the console, never into this return value (#87)
}
function Capture($exe, [string[]]$a) {   # stdout of a native command, '' on failure
    try { $o = & $exe @a 2>$null; if ($LASTEXITCODE -eq 0) { return (($o | Out-String).Trim()) } } catch { }
    return ''
}

Write-Host "=== 聽攏有 tinglongWu installer ===" -ForegroundColor White
if ($DryRun) { Write-Host "*** DRY RUN: nothing will be installed or downloaded / 試跑模式：不會安裝或下載任何東西 ***" -ForegroundColor Yellow }
Info "Folder / 資料夾: $Root"
Info "Whole install: about 10-110 min depending on your PC and connection (exact estimate after step 2). 全程約 10-110 分鐘，第 2 步後顯示準確預估。"

# ---------------------------------------------------------------- 1. Python
Step 1 '檢查 Python 3.10-3.12' 'Check Python 3.10-3.12'
$Py = $null
foreach ($v in '3.12', '3.11', '3.10') {
    $p = Capture 'py' @("-$v", '-c', 'import sys;print(sys.executable)')
    if ($p -and (Test-Path $p)) { $Py = $p; break }
}
if (-not $Py) {
    $p = Capture 'python' @('-c', 'import sys;print(sys.executable if (3,10)<=sys.version_info[:2]<=(3,12) else "")')
    if ($p -and (Test-Path $p)) { $Py = $p }
}
if (-not $Py) {
    Write-Host ""
    Write-Host "  找不到 Python 3.10 ~ 3.12 / Python 3.10-3.12 not found." -ForegroundColor Red
    Write-Host "  請到 https://www.python.org/downloads/windows/ 下載 Python 3.12，"
    Write-Host "  安裝時務必勾選 'Add python.exe to PATH'，裝好後再按一次 install.bat。"
    Write-Host "  Download Python 3.12 from python.org, TICK 'Add python.exe to PATH', then run install.bat again."
    exit 1
}
Ok "Python: $Py  ($(Capture $Py @('--version')))"

# ---------------------------------------------------------------- 2. GPU
Step 2 '偵測 NVIDIA 顯示卡' 'Detect NVIDIA GPU'
$Gpu = $false; $FreeMB = 0; $TotalMB = 0; $GpuName = ''; $DriverOld = $false
$smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($smi -and -not $Cpu) {
    $g = Get-TlwuGpu (Capture 'nvidia-smi' @('--query-gpu=name,memory.total,memory.free,driver_version', '--format=csv,noheader,nounits'))
    if ($g -and $g.DriverOld) {   # #95: CUDA 12 cannot run on it -> CPU tier now, not a surprise "switched to CPU" later
        $DriverOld = $true
        Warn ("NVIDIA driver {0} on {1} is too old for CUDA 12 (needs {2}+): CPU path. Update the driver to use the GPU, then re-run install.bat: {3}" -f $g.Driver, $g.Name, $TlwuMinDriver, $TlwuDriverUrl)
        Warn ("NVIDIA 驅動程式 {0} 太舊（CUDA 12 需要 {1} 以上），先走 CPU 路線。更新驅動程式後重跑 install.bat 即可使用 GPU：{2}" -f $g.Driver, $TlwuMinDriver, $TlwuDriverUrl)
    } elseif ($g) { $GpuName = $g.Name; $TotalMB = $g.TotalMB; $FreeMB = $g.FreeMB; $Gpu = $true }
}
if ($Cpu) { Info "-Cpu given: forcing CPU path / 已指定 -Cpu，走 CPU 路線" }
if ($Gpu) {
    Ok ("GPU: {0}, {1} MB total, {2} MB free" -f $GpuName, $TotalMB, $FreeMB)
    # plan by TOTAL VRAM (free VRAM is noisy at install time; the runtime --engine auto re-checks free VRAM at start-up)
    if     ($TotalMB -ge 7500) { $Whisper = 'large-v3-turbo'; $QwenFlag = '--qwen';  $QwenTxt = 'Qwen3-8B + Qwen3-4B (~7.5 GB)';  $SizeGB = '~10 GB'; $ModelGB = 10 }
    elseif ($TotalMB -ge 5500) { $Whisper = 'large-v3-turbo'; $QwenFlag = '--qwen4b'; $QwenTxt = 'Qwen3-4B only (~2.5 GB)';       $SizeGB = '~5 GB'; $ModelGB = 5 }
    elseif ($TotalMB -ge 3500) { $Whisper = 'small';          $QwenFlag = '--qwen4b'; $QwenTxt = 'Qwen3-4B only (~2.5 GB)';       $SizeGB = '~3.5 GB'; $ModelGB = 3.5 }
    else                       { $Whisper = 'small';          $QwenFlag = $null;      $QwenTxt = 'none (low VRAM: OPUS engine)'; $SizeGB = '~1 GB'; $ModelGB = 1 }
    Info "Tier chosen by total VRAM ($TotalMB MB; $FreeMB MB free now). 依顯示卡總容量選擇。"
    Info "Tip: close GPU-heavy apps (games, browsers with video, other tlwu) before the first run. 首次執行前請關閉佔用 GPU 的程式。"
    Ok "GPU path / GPU 路線: whisper=$Whisper, Qwen=$QwenTxt"
} else {
    if (-not $Cpu -and -not $DriverOld) { Warn "No NVIDIA GPU / nvidia-smi not found: CPU path (slower, OPUS engine). 找不到 NVIDIA 顯示卡，走 CPU 路線。" }
    $Whisper = 'small'; $QwenFlag = $null; $QwenTxt = 'none (CPU: OPUS engine)'; $SizeGB = '~1 GB'; $ModelGB = 1
    # #126: OPUS cannot do Japanese; with enough RAM the CPU tier also gets Hy-MT2 (+ a CPU llama-cpp-python build)
    $RamMB = Get-TlwuRamMB
    $JaHymt = Test-TlwuCpuJapanese $RamMB
    if ($JaHymt) {
        $QwenTxt = 'none (CPU: OPUS engine; Japanese: Hy-MT2 1.8B ~1.1 GB)'; $SizeGB = '~2 GB'; $ModelGB = 2
        Ok "CPU path / CPU 路線: whisper=$Whisper, $QwenTxt"
    }
    else { Warn "Only ${RamMB} MB RAM: Japanese translation is not supported on this PC (needs 4 GB); English and Russian work. 記憶體不足 4 GB：此電腦不支援日文翻譯，英文、俄文可用。" }
}
if ($SkipModels) { $ModelGB = 0 }
Info "Expected model download / 預計下載模型: $SizeGB (plus ~3 GB of Python packages / 另有約 3 GB 套件)"
Write-Host "  Whole install / 全部安裝: $(Format-TlwuEstimate (Get-TlwuTotalEstimate $Gpu $ModelGB))" -ForegroundColor White

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
if (-not $Gpu -and -not $JaHymt) { Info "CPU path: skipped / CPU 路線略過" }
elseif (-not $Gpu) {   # #126: CPU build of llama-cpp-python, only for Hy-MT2 Japanese translation
    Info "CPU path: Japanese translation engine (llama-cpp-python, CPU build) / CPU 路線：安裝日文翻譯引擎"
    $have = $false
    if (-not $DryRun) { $have = ((Capture $VPy @('-c', $LlamaChkCpu)) -eq 'True') }
    if ($have) { Ok "llama-cpp-python already works / 已可用" }
    else {
        $ok = Run $VPy @('-m', 'pip', 'install', '-r', 'requirements-llama-cpu.txt', '--only-binary=:all:')
        if ($ok -and -not $DryRun) {
            $ok = ((Capture $VPy @('-c', $LlamaChkCpu)) -eq 'True')
            if (-not $ok) {   # same version from another index (e.g. an AVX-512 CUDA build, #74): replace it
                [void](Run $VPy @('-m', 'pip', 'install', '-r', 'requirements-llama-cpu.txt', '--only-binary=:all:', '--force-reinstall', '--no-deps'))
                $ok = ((Capture $VPy @('-c', $LlamaChkCpu)) -eq 'True')
            }
            if (-not $ok) {   # never leave a build behind that would crash tlwu on this CPU
                Warn "llama-cpp-python failed the self-check: $(Capture $VPy @('-c', $LlamaWhyCpu))"
                [void](Run $VPy @('-m', 'pip', 'uninstall', '-y', 'llama-cpp-python'))
            }
        }
        if ($ok) { [void]$Installed.Add('llama-cpp-python (CPU build, for Japanese)') }
        else {
            $JaHymt = $false
            Warn "No usable llama-cpp-python CPU build: Japanese falls back to the low-quality OPUS route. 日文翻譯引擎安裝失敗，日文改用品質較差的 OPUS 路線。"
        }
    }
}
else {
    if (Run $VSet @('--gpu')) { [void]$Installed.Add('CUDA 12 libs (cuBLAS, cuDNN)') }
    else { Warn "tlwu-setup --gpu failed; Whisper will run on CPU. 失敗，將用 CPU 跑語音辨識。" }
    if (-not $DryRun) {   # #95: nvidia-smi said GPU; ask CTranslate2 itself (what tlwu will actually use)
        $n = Capture $VPy @('-c', 'import ctranslate2; print(ctranslate2.get_cuda_device_count())')
        if ($n -eq '0') { Warn "nvidia-smi sees $GpuName but CTranslate2 sees no CUDA device: tlwu will run on the CPU. Run '.venv\Scripts\tlwu.exe --probe' for the reason; updating the NVIDIA driver usually fixes it ($TlwuDriverUrl). 顯示卡偵測到了但 CUDA 無法使用，執行時會改用 CPU；通常更新 NVIDIA 驅動程式即可。" }
        elseif ($n) { Ok "CTranslate2 sees $n CUDA device(s) / CUDA 可用" }
    }
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
Step 5 '下載模型（最花時間，會顯示下載百分比）' 'Download models (the slow part; shows percent done)'
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
    if ($JaHymt) {
        if (Run $VSet @('--hymt')) { [void]$Installed.Add('Hy-MT2 1.8B (Japanese translation on CPU)') }
        else { Warn "Hy-MT2 download failed: Japanese uses the low-quality OPUS route until you re-run install.bat. Hy-MT2 下載失敗，重跑 install.bat 可補裝。" }
    }
}

# ---------------------------------------------------------------- 6. talk mode
Step 6 '說話模式（中文說、Kokoro 念英文給 Meet）' 'Talk mode (you speak Chinese, Kokoro speaks English into Meet)'
$TalkPs1 = Join-Path $Root 'tlwu-talk.ps1'
if ($SkipModels) { Info "-SkipModels: skipped / 略過" }
else {
    # Kokoro venv (~\tts-kokoro, own venv: it bundles GPL espeak) + SHA256-pinned model + smoke wav + tlwu-talk.ps1;
    # finished parts are skipped on a re-run, a hash mismatch stops this step with the reason printed above.
    $tc = if ($Gpu) { 'cuda' } else { 'cpu' }
    $tm = if ($Engine -like 'qwen*') { 'auto' } else { 'opus' }
    if (Run $VSet @('--talk', '--talk-whisper', $Whisper, '--talk-compute', $tc, '--talk-mt', $tm)) {
        [void]$Installed.Add('Talk mode: Kokoro voice (~\tts-kokoro, models\kokoro, SHA256 verified) + tlwu-talk.ps1')
    } else { Warn "Talk mode setup failed (see above); listening still works. Re-run install.bat to retry. 說話模式安裝失敗，重跑 install.bat 即可。" }
}
$Cable = $false
try { $Cable = [bool](Get-CimInstance Win32_SoundDevice -ErrorAction Stop | Where-Object { $_.Name -match 'VB-Audio' }) } catch { }
if ($Cable) { Ok "VB-CABLE found / 已偵測到 VB-CABLE" }
else { Warn "VB-CABLE not found: talk mode needs it (free: https://vb-audio.com/Cable/), then Meet microphone = 'CABLE Output'. 說話模式需要 VB-CABLE。" }

# ---------------------------------------------------------------- 7. shortcuts
Step 7 '建立桌面捷徑' 'Create desktop shortcuts'
function Make-Shortcut($name, $target, $arguments) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk"
    if ($DryRun) { Write-Host "  [dry-run] shortcut: $lnk -> $target $arguments (start in $Root)" -ForegroundColor DarkGray; return }
    try {
        $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $s.TargetPath = $target; $s.Arguments = $arguments; $s.WorkingDirectory = $Root
        $s.Description = "$name - offline speech translator"
        $s.Save()
        Ok "Shortcut: $lnk"; [void]$Installed.Add("Desktop shortcut '$name'")
    } catch { Warn "Could not create the shortcut $lnk ($_). Start it from $target $arguments instead." }
}
if ($SkipShortcut) { Info "-SkipShortcut: skipped / 略過" }
else {
    if (Test-Path $VGui) { Make-Shortcut '聽攏有 tinglongWu' $VGui '' } else { Make-Shortcut '聽攏有 tinglongWu' $VPyW '-m tinglongwu.gui' }
    if ($DryRun -or (Test-Path $TalkPs1)) {
        Make-Shortcut '聽攏有 說話模式' "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" "-NoExit -ExecutionPolicy Bypass -File `"$TalkPs1`""
    } else { Info "No tlwu-talk.ps1 (talk mode not set up): talk shortcut skipped / 未建立說話模式捷徑" }
}

# ---------------------------------------------------------------- 8. self-test + summary
Step 8 '自我測試與完成' 'Self-test and summary'
# #99: load the installed models and translate one test sentence (tlwu --selftest, offline); a failure only warns.
$SelfTest = 'skipped'
if ($SkipModels) { Info "-SkipModels: self-test skipped / 未下載模型，略過自我測試" }
else {
    $StEngine = 'opus'
    if ($Engine -like 'qwen*') { $StEngine = $(if ($QwenFlag -eq '--qwen4b') { 'qwen4b' } else { 'qwen' }) }
    $StArgs = @('--model', $Whisper, '--engine', $StEngine)
    if (-not $Gpu) { $StArgs += @('--device', 'cpu') }
    if ($DryRun) { Write-Host "  [dry-run] $VSet --selftest $($StArgs -join ' ')" -ForegroundColor DarkGray }
    else {
        Info "Self-test: loading the models and translating one test sentence (offline) / 自我測試：載入模型並翻譯一句測試句"
        $SelfTest = Invoke-TlwuSelftest $VSet $StArgs
        if ($SelfTest -eq 'ok') { Ok "Self-test passed / 自我測試通過" }
        elseif ($SelfTest -eq 'failed') {
            Warn "Install finished but the self-test failed, see install.log (Report a problem attaches it); nothing was deleted. 安裝完成但自我測試失敗，詳見 install.log，已安裝的檔案都保留。"
        } else { Info "This version has no self-test yet: skipped / 此版本尚無自我測試，略過" }
    }
}
Write-Host ""
Write-Host "  Installed / 已安裝:" -ForegroundColor White
foreach ($i in $Installed) { Write-Host "    - $i" }
Write-Host "  Translation engine / 翻譯引擎: $Engine" -ForegroundColor White
Write-Host "  Self-test / 自我測試: $SelfTest" -ForegroundColor White
if ($Warnings.Count -gt 0) {
    Write-Host "  Warnings / 注意:" -ForegroundColor Yellow
    foreach ($w in $Warnings) { Write-Host "    - $w" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Launch / 啟動: double-click the desktop shortcut '聽攏有 tinglongWu' (or $VGui)" -ForegroundColor White
Write-Host "  Talk mode / 說話模式: desktop shortcut '聽攏有 說話模式' (Meet microphone = CABLE Output)" -ForegroundColor White
Write-Host "  Uninstall / 移除: delete this folder ($Root; models are in its models\ folder), ~\tts-kokoro and the desktop shortcuts."
Write-Host "                    刪除此資料夾（模型在 models\ 內）與桌面捷徑即可。"
if ($DryRun) { Write-Host ""; Write-Host "  (dry run finished, nothing was changed / 試跑結束，未做任何更動)" -ForegroundColor Yellow }
exit 0
