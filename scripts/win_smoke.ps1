# Packaged-app smoke (#341): run `tlwu.exe --smoke`, then launch the GUI for N seconds and require it to stay alive.
# Usage: powershell -File scripts/win_smoke.ps1 -AppDir <dir with tlwu.exe> [-LogDir <dir>] [-GuiSeconds 10]
# Exit 0 = OK, 1 = smoke failed, 2 = GUI exited early / crashed, 3 = bad arguments. Pure ASCII (PS 5.1 safe).
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [string]$LogDir = "",
    [int]$GuiSeconds = 10,
    [string]$ExeName = "tlwu.exe"
)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $AppDir $ExeName
if (-not (Test-Path -LiteralPath $exe)) { Write-Host "missing $exe"; exit 3 }
if (-not $LogDir) { $LogDir = Join-Path $AppDir 'smoke-logs' }
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Invoke-Logged([string]$Tag, [string[]]$ArgList, [int]$TimeoutSec) {
    $o = Join-Path $LogDir "$Tag.out.txt"; $e = Join-Path $LogDir "$Tag.err.txt"
    $p = Start-Process -FilePath $exe -ArgumentList $ArgList -WorkingDirectory $AppDir -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $o -RedirectStandardError $e
    $null = $p.Handle  # keep the handle so ExitCode is readable after exit
    $done = $p.WaitForExit($TimeoutSec * 1000)
    return @{ Proc = $p; Done = $done; Out = $o; Err = $e }
}

# 1) --smoke
$s = Invoke-Logged 'smoke' @('--smoke') 600
if (-not $s.Done) { $s.Proc.Kill(); Write-Host "FAIL: --smoke timed out"; exit 1 }
Get-Content -LiteralPath $s.Out, $s.Err -ErrorAction SilentlyContinue | Write-Host
if ($s.Proc.ExitCode -ne 0) { Write-Host "FAIL: --smoke exit code $($s.Proc.ExitCode)"; exit 1 }
Write-Host "smoke: exit 0"

# 2) GUI launch: must still be running after N seconds (then we stop it)
$g = Invoke-Logged 'gui' @('--gui') $GuiSeconds
if ($g.Done) {
    Get-Content -LiteralPath $g.Out, $g.Err -ErrorAction SilentlyContinue | Write-Host
    Write-Host "FAIL: GUI exited after less than $GuiSeconds s (exit code $($g.Proc.ExitCode))"
    exit 2
}
Stop-Process -Id $g.Proc.Id -Force -ErrorAction SilentlyContinue
Write-Host "gui: still running after $GuiSeconds s, stopped"
exit 0
