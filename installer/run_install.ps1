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
    # #755: open + append + close per line. The old `| Out-File` held install.log open for the whole install, so
    # the wizard's reader (LoadStringsFromFile, share-read) hit a sharing violation on every poll and the page
    # never left "Starting install.ps1 ...". No BOM either, so the first line parses as plain text.
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    # the wizard reads install.log share-read: an append that lands while it is open gets a sharing violation, so
    # retry briefly and finally drop the line; a progress poll must never abort the install
    function Add-LogLine([string]$Text) {
        for ($i = 0; $i -lt 20; $i++) {
            try { [IO.File]::AppendAllText($Log, $Text + "`r`n", $utf8); return } catch [System.IO.IOException] { Start-Sleep -Milliseconds 50 }
        }
    }
    [IO.File]::WriteAllText($Log, '', $utf8)
    & (Join-Path $Root 'install.ps1') @a *>&1 | ForEach-Object { Add-LogLine "$_" }
    $rc = if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE }
} catch {
    Add-LogLine "run_install.ps1: $_"
} finally {
    Set-Content -LiteralPath $Done -Value $rc -Encoding ascii
}
exit $rc
