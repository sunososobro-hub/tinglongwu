# Called by the installer wizard (installer\tinglongwu.iss): runs install.ps1 hidden, logs everything to
# install.log (the wizard reads the "[n/7]" step lines from it for its progress bar) and always writes the exit
# code to install.done, which tells the wizard that install.ps1 has finished.
param([switch]$Cpu, [string]$Packs = '')
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Log  = Join-Path $Root 'install.log'
$Done = Join-Path $Root 'install.done'
Remove-Item -LiteralPath $Done -ErrorAction SilentlyContinue
$rc = 1
try {
    if ($Packs -match '^[a-z]+(,[a-z]+)*$') { $env:TLWU_PACKS = $Packs }   # #672: install.ps1 passes it to tlwu-setup --packs
    $a = @{ SkipShortcut = $true; FromSetup = $true }   # #756: summary says "uninstall in Settings > Apps"   # the wizard makes the shortcuts itself (with the app icon)
    if ($Cpu) { $a.Cpu = $true }
    & (Join-Path $Root 'install.ps1') @a *>&1 | ForEach-Object { "$_" } | Out-File -LiteralPath $Log -Encoding utf8 -Width 400
    $rc = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
} catch {
    "run_install.ps1: $_" | Out-File -LiteralPath $Log -Encoding utf8 -Append
} finally {
    Set-Content -LiteralPath $Done -Value $rc -Encoding ascii
}
exit $rc
