# Shared helpers for the Windows menu (PowerShell 5.1), dot-sourced by windows_menu.ps1.
#   UI language : env TLWU_UI_LANG > config ui_lang > (Get-UICulture).Name > en
#   messages    : scripts\i18n\<lang>.psd1 (en.psd1 is the base; add a language = add a file + a line in ConvertTo-TlwuLang)
#   config file : KEY=VALUE lines (auto_update=0|1, ui_lang=...) in %APPDATA%\tinglongwu\config (the Python side reads the same file)
$script:TlwuI18nDir = Join-Path $PSScriptRoot "i18n"

function Get-TlwuConfigDir {
    if ($env:TLWU_CONFIG_DIR) { return $env:TLWU_CONFIG_DIR }
    $base = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME "AppData\Roaming" }
    return (Join-Path $base "tinglongwu")
}
function Get-TlwuCfg([string]$key) {
    $f = Join-Path (Get-TlwuConfigDir) "config"
    if (-not (Test-Path $f)) { return "" }
    $v = ""
    foreach ($l in [System.IO.File]::ReadAllLines($f)) { if ($l.StartsWith("$key=")) { $v = $l.Substring($key.Length + 1).Trim() } }
    return $v
}
function Set-TlwuCfg([string]$key, [string]$val) {
    try {
        $d = Get-TlwuConfigDir
        New-Item -ItemType Directory -Force -Path $d | Out-Null
        $f = Join-Path $d "config"
        $lines = @()
        if (Test-Path $f) { $lines = @([System.IO.File]::ReadAllLines($f) | Where-Object { -not $_.StartsWith("$key=") }) }
        $lines += "$key=$val"
        [System.IO.File]::WriteAllText($f, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    } catch {}
}

function ConvertTo-TlwuLang([string]$tag) {   # ru-RU, zh-TW, zh-CN, en-US ... -> en | ru | zh-TW
    $t = ("$tag").ToLower().Replace("_", "-")
    if ($t.StartsWith("ru")) { return "ru" }
    if ($t.StartsWith("zh")) { return "zh-TW" }   # zh-CN also gets the zh-TW text (no separate zh-CN file yet)
    return "en"
}
function Get-TlwuUiLang {
    $c = $env:TLWU_UI_LANG
    if ($c -eq "auto") { $c = "" }
    if (-not $c) { $c = Get-TlwuCfg "ui_lang" }
    if ($c -eq "auto") { $c = "" }
    if ($c) { return (ConvertTo-TlwuLang $c) }
    $sys = ""
    try { $sys = (Get-UICulture).Name } catch {}
    return (ConvertTo-TlwuLang $sys)
}
function Import-TlwuMessages {
    $script:TlwuUi = Get-TlwuUiLang
    $script:M = Import-PowerShellDataFile (Join-Path $script:TlwuI18nDir "en.psd1")
    if ($script:TlwuUi -ne "en") {
        $f = Join-Path $script:TlwuI18nDir "$($script:TlwuUi).psd1"
        if (Test-Path $f) { $o = Import-PowerShellDataFile $f; foreach ($k in $o.Keys) { $script:M[$k] = $o[$k] } }
    }
    $env:TLWU_UI_LANG = $script:TlwuUi   # the Python side (guard messages, netreport) follows the menu
}
# T 'KEY' arg0 arg1 ... : message text, .NET format with {0} {1}
function T {
    param([string]$k)
    $f = $script:M[$k]
    if ($null -eq $f) { return $k }
    if ($args.Count -gt 0) { return [string]::Format($f, [object[]]$args) }
    return $f
}

# Live drafts (#60): re-runs Whisper + MT on the growing audio, so it costs CPU. Default no; returns the extra
# tlwu arguments (@() or @("--stream")). Listen-only mode only: the meeting listener runs in its own window.
function Get-TlwuStreamArgs {
    $ans = ""
    try { $ans = Read-Host (T 'ASK_STREAM') } catch { $ans = "" }
    if ($ans -match '^(y|yes)$') { return @("--stream") }
    return @()
}

function Test-TlwuAutoUpdate { return (($env:TLWU_NO_UPDATE -ne "1") -and ((Get-TlwuCfg "auto_update") -eq "1")) }

# First run (no config yet): ask once. No answer possible (stdin closed) = stay off, nothing saved.
function Invoke-TlwuConsent {
    if ($env:TLWU_NO_UPDATE -eq "1") { return }
    if (Get-TlwuCfg "auto_update") { return }
    try { $ans = Read-Host (T 'ASK_AUTOUPD') } catch { return }
    if ($null -eq $ans) { return }
    if ($ans -match '^(y|yes)$') { Set-TlwuCfg "auto_update" "1"; Write-Host (T 'AUTOUPD_SET_ON') }
    else { Set-TlwuCfg "auto_update" "0"; Write-Host (T 'AUTOUPD_SET_OFF') }
}
function Switch-TlwuAutoUpdate {
    if ($env:TLWU_NO_UPDATE -eq "1") { Write-Host (T 'UPD_FORCED_OFF'); return }
    if (Test-TlwuAutoUpdate) { Set-TlwuCfg "auto_update" "0"; Write-Host (T 'AUTOUPD_SET_OFF') }
    else { Set-TlwuCfg "auto_update" "1"; Write-Host (T 'AUTOUPD_SET_ON') }
}
function Select-TlwuUiLang {
    $c = Read-Host (T 'ASK_UILANG')
    if (-not $c) { $c = "auto" }
    if (@("auto", "en", "ru", "zh-TW") -notcontains $c) { $c = ConvertTo-TlwuLang $c }
    Set-TlwuCfg "ui_lang" $c
    $env:TLWU_UI_LANG = ""
    Import-TlwuMessages
}

# Network-guard report (opt-in): count + hosts, ask, y = show the issue text and open a prefilled GitHub issue; either answer marks them handled.
function Invoke-TlwuReportCheck([string]$py) {
    & $py -m tinglongwu.netreport status 2>$null
    if ($LASTEXITCODE -ne 0) { return }
    $ans = Read-Host (T 'ASK_REPORT')
    if ($ans -match '^(y|yes)$') { & $py -m tinglongwu.netreport send } else { & $py -m tinglongwu.netreport dismiss | Out-Null }
}

# Self-update: git fetch (6 s cap) + fast-forward; reinstall deps only if pyproject/requirements changed. The ONLY network use
# besides tlwu-setup/pip. Never blocks or throws. Runs at menu start only if auto-update is on, or on demand with U.
# Returns: updated | uptodate | skipped | forced_off | nogit
function Update-Tlwu([string]$py) {
    if ($env:TLWU_NO_UPDATE -eq "1") { return "forced_off" }
    if (-not (Test-Path ".git")) { return "nogit" }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { return "nogit" }
    $ErrorActionPreference = "Continue"
    try {
        $old = (git rev-parse HEAD 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $old) { return "skipped" }
        $env:GIT_TERMINAL_PROMPT = "0"
        $tmp = [System.IO.Path]::GetTempFileName()
        $p = Start-Process -FilePath "git" -ArgumentList @("-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=5", "fetch", "--quiet", "origin") `
            -NoNewWindow -PassThru -RedirectStandardOutput $tmp -RedirectStandardError "$tmp.err"
        if (-not $p.WaitForExit(6000)) { try { $p.Kill() } catch {}; Write-Host (T 'UPD_SKIP_NET'); return "skipped" }
        Remove-Item $tmp, "$tmp.err" -ErrorAction SilentlyContinue
        if ($p.ExitCode -ne 0) { Write-Host (T 'UPD_SKIP_NET'); return "skipped" }
        $up = (git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $up) { $up = "origin/main" }
        $new = (git rev-parse $up 2>$null)
        if (-not $new -or $new -eq $old) { return "uptodate" }
        git merge-base --is-ancestor $up HEAD 2>$null
        if ($LASTEXITCODE -eq 0) { return "uptodate" }
        git merge --ff-only --quiet $up 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host (T 'UPD_SKIP_DIVERGED'); return "skipped" }
        $changed = (git diff --name-only $old HEAD 2>$null) -join "`n"
        if ($changed -match "(?m)^(pyproject\.toml|requirements[^/]*\.txt)$") {
            Write-Host (T 'UPD_PKG')
            & $py -m pip install -q -e . 2>$null | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Host (T 'UPD_PKG_FAIL') }
        } else { Write-Host (T 'UPD_OK_NEXT') }
        return "updated"
    } catch { Write-Host (T 'UPD_SKIP_ERR' $_.Exception.Message); return "skipped" }
}
# U in the menu: update once now, regardless of the auto-update setting
function Invoke-TlwuManualUpdate([string]$py) {
    $r = Update-Tlwu $py
    $ErrorActionPreference = "Stop"
    switch ($r) {
        "forced_off" { Write-Host (T 'UPD_FORCED_OFF') }
        "nogit" { Write-Host (T 'UPD_NOGIT') }
        "uptodate" { Write-Host (T 'UPD_UPTODATE') }
    }
}
