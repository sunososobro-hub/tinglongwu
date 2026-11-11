# Helpers for install.ps1 (Windows PowerShell 5.1), dot-sourced by it. Kept in their own file so the tests can run
# them (tests/test_install_progress.py). #87: every step prints a time estimate, and native commands (pip, tlwu-setup)
# write straight to the console instead of being captured, so the screen never sits silent for minutes.

# Sizes in GB (packages ~3 GB: measured on the owner's clean Windows install, #87; GPU libs: cuBLAS + cuDNN + cudart
# wheels + the llama-cpp-python CUDA wheel). Speeds: a fast (100 Mbit/s) and a slow (20 Mbit/s) home connection.
$script:TlwuPkgGB    = 3.0
$script:TlwuGpuLibGB = 1.5
$script:TlwuFastMbps = 100
$script:TlwuSlowMbps = 20

# Download minutes for $gb at the fast and the slow speed, plus $fixedMin for unpacking / installing.
function Get-TlwuMinutes([double]$gb, [double]$fixedMin) {
    $mbit = $gb * 1024 * 8
    $lo = [int][math]::Ceiling($mbit / $script:TlwuFastMbps / 60 + $fixedMin)
    $hi = [int][math]::Ceiling($mbit / $script:TlwuSlowMbps / 60 + $fixedMin)
    return ,@([math]::Max(1, $lo), [math]::Max(1, $hi))
}

# Estimate for step $n (1-7) as @(lo, hi) minutes; @(0, 0) = under a minute.
function Get-TlwuStepEstimate([int]$n, [bool]$gpu, [double]$modelGB) {
    if ($n -eq 3) { return (Get-TlwuMinutes $script:TlwuPkgGB 2) }
    if ($n -eq 4 -and $gpu) { return (Get-TlwuMinutes $script:TlwuGpuLibGB 1) }
    if ($n -eq 5 -and $modelGB -gt 0) { return (Get-TlwuMinutes $modelGB 1) }
    return ,@(0, 0)
}

# Whole install (steps 3-5 plus a minute for the quick ones) for the chosen tier.
function Get-TlwuTotalEstimate([bool]$gpu, [double]$modelGB) {
    $lo = 1; $hi = 1
    foreach ($n in 3, 4, 5) { $r = Get-TlwuStepEstimate $n $gpu $modelGB; $lo += $r[0]; $hi += $r[1] }
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

# #99: last installer step. Runs `tlwu-setup --selftest <args>` with its output on the console and returns
# 'ok' (exit 0), 'skipped' (exit 3: this tlwu has no --selftest yet) or 'failed' (anything else; nothing is deleted).
function Invoke-TlwuSelftest([string]$setupExe, [string[]]$a) {
    $rc = Invoke-TlwuNative $setupExe (@('--selftest') + @($a))
    if ($rc -eq 0) { return 'ok' }
    if ($rc -eq 3) { return 'skipped' }
    return 'failed'
}
