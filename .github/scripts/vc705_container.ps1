# #705: runs INSIDE a mcr.microsoft.com/windows/servercore container (no Microsoft VC++ runtime, no Python).
# Copies the repo to C:\tlwu, runs install.bat as a double-click would (models and shortcuts skipped), and writes
# C:\out\install.log + C:\out\result.txt for scripts/vc_runtime_check.py (run on the host afterwards).
#   -Mode installs   aka.ms reachable: the installer must fetch and install the runtime itself
#   -Mode blocked    aka.ms resolves to 0.0.0.0 (forced failure): the install must stop at step 4
param([ValidateSet('installs', 'blocked')][string]$Mode = 'installs')
$ErrorActionPreference = 'Continue'
New-Item -ItemType Directory -Force -Path C:\out | Out-Null
$dlls = @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'vcomp140.dll')
function Missing { @($dlls | Where-Object { -not (Test-Path (Join-Path "$env:windir\System32" $_)) }) -join ',' }
$start = Missing
Write-Host "VC++ runtime DLLs missing before install: '$start'"

if ($Mode -eq 'blocked') {
    Add-Content -Path "$env:windir\System32\drivers\etc\hosts" -Value "`r`n0.0.0.0 aka.ms" -Encoding ASCII
    Write-Host "forced failure: aka.ms -> 0.0.0.0"
}

robocopy C:\src C:\tlwu /E /XD .git .venv /NFL /NDL /NJH /NJS /NP | Out-Null
Set-Location C:\tlwu
$env:TLWU_SKIP_MODELS = '1'; $env:TLWU_SKIP_SHORTCUT = '1'; $env:TLWU_NO_UPDATE = '1'; $env:TLWU_UI_LANG = 'en'
cmd /c "install.bat < NUL > C:\out\install.log 2>&1"
$rc = $LASTEXITCODE
Get-Content C:\out\install.log -Tail 60

$llama = ''
if ($Mode -eq 'installs' -and (Test-Path .venv\Scripts\tlwu-setup.exe)) {
    & .venv\Scripts\tlwu-setup.exe --check-llama
    $llama = "$LASTEXITCODE"
}
@("vc_missing_start=$start", "install_rc=$rc", "check_llama_rc=$llama", "vc_missing_end=$(Missing)") |
    Set-Content -Path C:\out\result.txt -Encoding ASCII
Get-Content C:\out\result.txt
exit 0   # the host judges the run (scripts/vc_runtime_check.py)
