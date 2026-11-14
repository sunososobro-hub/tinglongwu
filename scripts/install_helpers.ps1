# Helpers for install.ps1 (Windows PowerShell 5.1), dot-sourced by it. Kept in their own file so the tests can run
# them (tests/test_install_progress.py). #87: every step prints a time estimate, and native commands (pip, tlwu-setup)
# write straight to the console instead of being captured, so the screen never sits silent for minutes.

# Sizes in GB (packages ~3 GB: measured on the owner's clean Windows install, #87; GPU libs: cuBLAS + cuDNN + cudart
# wheels + the llama-cpp-python CUDA wheel). Speeds: a fast (100 Mbit/s) and a slow (20 Mbit/s) home connection.
$script:TlwuPkgGB    = 3.0
$script:TlwuGpuLibGB = 1.5
# talk mode (#124): Kokoro model 0.12 GB (pinned sizes) + kokoro-onnx/onnxruntime wheels + the zh-en OPUS pair
$script:TlwuTalkGB   = 0.3
$script:TlwuFastMbps = 100
$script:TlwuSlowMbps = 20

# Download minutes for $gb at the fast and the slow speed, plus $fixedMin for unpacking / installing.
function Get-TlwuMinutes([double]$gb, [double]$fixedMin) {
    $mbit = $gb * 1024 * 8
    $lo = [int][math]::Ceiling($mbit / $script:TlwuFastMbps / 60 + $fixedMin)
    $hi = [int][math]::Ceiling($mbit / $script:TlwuSlowMbps / 60 + $fixedMin)
    return ,@([math]::Max(1, $lo), [math]::Max(1, $hi))
}

# Estimate for step $n (1-8) as @(lo, hi) minutes; @(0, 0) = under a minute.
function Get-TlwuStepEstimate([int]$n, [bool]$gpu, [double]$modelGB) {
    if ($n -eq 3) { return (Get-TlwuMinutes $script:TlwuPkgGB 2) }
    if ($n -eq 4 -and $gpu) { return (Get-TlwuMinutes $script:TlwuGpuLibGB 1) }
    if ($n -eq 5 -and $modelGB -gt 0) { return (Get-TlwuMinutes $modelGB 1) }
    if ($n -eq 6 -and $modelGB -gt 0) { return (Get-TlwuMinutes $script:TlwuTalkGB 1) }
    return ,@(0, 0)
}

# Whole install (steps 3-5 plus a minute for the quick ones) for the chosen tier.
function Get-TlwuTotalEstimate([bool]$gpu, [double]$modelGB) {
    $lo = 1; $hi = 1
    foreach ($n in 3, 4, 5, 6) { $r = Get-TlwuStepEstimate $n $gpu $modelGB; $lo += $r[0]; $hi += $r[1] }
    return ,@($lo, $hi)
}

function Format-TlwuEstimate($r) {
    if ($r[1] -le 0) { return '預估不到 1 分鐘 / under 1 minute' }
    return ('預估約 {0}-{1} 分鐘（視網路速度） / about {0}-{1} min, depending on your connection' -f $r[0], $r[1])
}

# Windows command-line quoting (MS C runtime / CommandLineToArgvW rules) for ProcessStartInfo.Arguments:
# PowerShell 5.1 runs on .NET Framework, which has no ArgumentList.
function ConvertTo-TlwuArgLine([string[]]$a) {
    $parts = foreach ($s in $a) {
        if ($s -ne '' -and $s -notmatch '[\s"]') { $s; continue }
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"'); $bs = 0
        foreach ($c in $s.ToCharArray()) {
            if ($c -eq [char]'\') { $bs++; continue }
            if ($c -eq [char]'"') { [void]$sb.Append(('\' * ($bs * 2 + 1)) + '"') }
            else { [void]$sb.Append(('\' * $bs) + $c) }
            $bs = 0
        }
        [void]$sb.Append(('\' * ($bs * 2)) + '"')
        $sb.ToString()
    }
    return ($parts -join ' ')
}

# Run a native command with its stdout/stderr going straight to the console (not into the pipeline), so pip keeps
# its own progress bar and nothing is swallowed. Returns the exit code (-1 if it could not start).
function Invoke-TlwuNative([string]$exe, [string[]]$a) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = ConvertTo-TlwuArgLine $a
    $psi.UseShellExecute = $false
    $psi.WorkingDirectory = (Get-Location).ProviderPath
    try { $p = [System.Diagnostics.Process]::Start($psi) }
    catch { Write-Host "  !!  cannot start $exe ($($_.Exception.Message))" -ForegroundColor Yellow; return -1 }
    $p.WaitForExit()
    return [int]$p.ExitCode
}

# #126: Japanese on the CPU tier. OPUS has no usable Japanese pairs, so a CPU PC gets Hy-MT2-1.8B (Q4_K_M GGUF,
# ~1.1 GB on disk, ~1.5 GB RAM loaded) through a CPU llama-cpp-python wheel. Below a nominal 4 GB of RAM it does not
# fit next to Whisper small and Windows, so Japanese is reported as unsupported instead. A "4 GB" PC reports about
# 3.7-3.9 GB total (firmware / integrated-GPU reserve), hence 3584 MB. Unknown RAM: try it (tlwu falls back to OPUS).
$script:TlwuJaMinRamMB = 3584

# Physical RAM in MB, $null when it cannot be read. CI-only: TLWU_TEST_RAM_MB pretends a size (tests of the low-RAM path).
function Get-TlwuRamMB {
    if ($env:TLWU_TEST_RAM_MB) { return [int]$env:TLWU_TEST_RAM_MB }
    try { return [int]((Get-CimInstance Win32_ComputerSystem -ErrorAction Stop).TotalPhysicalMemory / 1MB) }
    catch { return $null }
}

# Enough RAM (nominal 4 GB) for Hy-MT2 on the CPU? Below it the installer still installs it, with a warning (#301). $ramMB may be $null (unknown).
function Test-TlwuCpuJapanese($ramMB) {
    if ($null -eq $ramMB) { return $true }
    return ([double]$ramMB -ge $script:TlwuJaMinRamMB)
}

# #99: last installer step. Runs `tlwu-setup --selftest <args>` with its output on the console and returns
# 'ok' (exit 0), 'skipped' (exit 3: this tlwu has no --selftest yet) or 'failed' (anything else; nothing is deleted).
function Invoke-TlwuSelftest([string]$setupExe, [string[]]$a) {
    $rc = Invoke-TlwuNative $setupExe (@('--selftest') + @($a))
    if ($rc -eq 0) { return 'ok' }
    if ($rc -eq 3) { return 'skipped' }
    return 'failed'
}

# #95: a GPU whose driver predates CUDA 12 is not a GPU for us (CTranslate2 4.x / cuBLAS 12 cannot start on it), so
# the installer must pick the CPU tier up front instead of the user finding out at run time. Same threshold as
# tinglongwu/device.py MIN_DRIVER['win32'] (NVIDIA CUDA 12.x release notes: Windows driver >= 528.33).
$script:TlwuMinDriver = '528.33'
$script:TlwuDriverUrl = 'https://www.nvidia.com/Download/index.aspx'

# First line of `nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv,noheader,nounits`
# -> @{Name; TotalMB; FreeMB; Driver; DriverOld}, or $null when it cannot be parsed (no GPU).
function Get-TlwuGpu([string]$smi) {
    $line = (($smi -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -First 1)
    if (-not $line) { return $null }
    $f = @($line -split ',\s*' | ForEach-Object { $_.Trim() })
    if ($f.Count -lt 3) { return $null }
    $t = 0; $fr = 0
    if (-not ([int]::TryParse($f[1], [ref]$t) -and [int]::TryParse($f[2], [ref]$fr))) { return $null }
    $drv = ''; $old = $false
    if ($f.Count -ge 4 -and $f[3] -match '^\d+(\.\d+){1,3}$') { $drv = $f[3]; $old = ([version]$drv) -lt ([version]$script:TlwuMinDriver) }
    return [pscustomobject]@{ Name = $f[0]; TotalMB = $t; FreeMB = $fr; Driver = $drv; DriverOld = $old }
}

# #756: the two "Uninstall" summary lines. A setup install (the wizard passes -FromSetup, or unins*.exe sits in the
# install folder) has a real uninstaller; a script / git install has nothing to run, so the folder is deleted by hand.
function Get-TlwuUninstallHint([string]$Root, [bool]$FromSetup) {
    $setup = $FromSetup -or [bool](Get-ChildItem -LiteralPath $Root -Filter 'unins*.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($setup) {
        return @('  Uninstall / 移除: Windows Settings > Apps > 聽攏有 (Add or remove programs).',
                 '                    Windows 設定 > 應用程式 > 聽攏有 > 解除安裝。')
    }
    return @("  Uninstall / 移除: delete this folder ($Root; models are in its models\ folder), ~\tts-kokoro and the desktop shortcuts.",
             '                    刪除此資料夾（模型在 models\ 內）與桌面捷徑即可。')
}

# #705: llama.dll (and ctranslate2 / onnxruntime) load only when the Microsoft Visual C++ 2015-2022 runtime is on the
# PC, and a fresh Windows 11 often has none ("Could not find module ... llama.dll"). Step 4 installs it first.
$script:TlwuVcDlls = @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'vcomp140.dll')
$script:TlwuVcRedistUrl = 'https://aka.ms/vs/17/release/vc_redist.x64.exe'
$script:TlwuVcSigner = 'O=Microsoft Corporation'   # an exact RDN of the signer certificate subject
$script:TlwuVcOkExitCodes = @(0, 1638, 3010)       # done / a newer runtime is already installed / done, reboot pending

# Names of the runtime DLLs that are not in $SysDir (default: System32). Always an array (empty when all are there).
function Get-TlwuMissingVcDlls([string]$SysDir = (Join-Path $env:windir 'System32')) {
    return @($script:TlwuVcDlls | Where-Object { -not (Test-Path -LiteralPath (Join-Path $SysDir $_)) })
}

# $true only for a valid Authenticode signature (Get-AuthenticodeSignature result) whose signer certificate has
# O=Microsoft Corporation. Anything else (unsigned, hash mismatch, other publisher, no certificate) is $false.
function Test-TlwuMicrosoftSignature($Sig) {
    if ($null -eq $Sig -or $null -eq $Sig.SignerCertificate) { return $false }
    if ([string]$Sig.Status -ne 'Valid') { return $false }
    return (@(([string]$Sig.SignerCertificate.Subject) -split ',\s*') -contains $script:TlwuVcSigner)
}

# Downloads the VC++ redistributable, requires a valid Microsoft signature, then runs it `/install /quiet /norestart`
# (the exe asks Windows for permission once). $true when it finished (see TlwuVcOkExitCodes). The three script blocks
# are the download, the signature check and the run; the tests replace them.
function Install-TlwuVcRuntime(
    [string]$Url = $script:TlwuVcRedistUrl,
    [scriptblock]$Fetch = { param($u, $f)
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $u -OutFile $f -UseBasicParsing },
    [scriptblock]$Sign = { param($f) Get-AuthenticodeSignature -FilePath $f },
    [scriptblock]$Exec = { param($f, $a) (Start-Process -FilePath $f -ArgumentList $a -Wait -PassThru).ExitCode }) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) 'tlwu_vc_redist.x64.exe'
    try {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        & $Fetch $Url $tmp
        if (-not (Test-Path -LiteralPath $tmp)) { Write-Host "  !!  VC++ runtime download failed / 下載失敗" -ForegroundColor Yellow; return $false }
        if (-not (Test-TlwuMicrosoftSignature (& $Sign $tmp))) {
            Write-Host "  !!  the downloaded file is not validly signed by Microsoft Corporation: not run / 檔案不是 Microsoft 的有效簽章，不執行" -ForegroundColor Yellow
            return $false
        }
        $rc = [int](& $Exec $tmp @('/install', '/quiet', '/norestart'))
        if ($script:TlwuVcOkExitCodes -notcontains $rc) { Write-Host "  !!  VC++ runtime installer exit code $rc / 安裝程式回傳 $rc" -ForegroundColor Yellow; return $false }
        return $true
    } catch {
        Write-Host "  !!  VC++ runtime install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}
