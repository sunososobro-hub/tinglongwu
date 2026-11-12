# tinglongWu Windows bootstrap (published as install.ps1 on each GitHub Release). PowerShell 5.1+.
#
#   irm https://raw.githubusercontent.com/sunososobro-hub/tinglongwu/main/scripts/bootstrap_windows.ps1 | iex
#
# The release workflow replaces the ref placeholder below with the release tag, so this installs exactly the
# version it was released with. Run straight from the repo (unreleased) it falls back to main.
# It fetches the source to %USERPROFILE%\tinglongwu (git clone if git exists, else the GitHub zip of that tag),
# then runs the repo's own install.ps1 in a child PowerShell. The execution-policy bypass applies to that one child
# process only (same as install.bat); nothing is written to your persistent settings.
# Override with env vars: TLWU_DIR, TLWU_REF, TLWU_REPO (owner/name), TLWU_UI_LANG=en|ru|zh-TW.

& {
    $ErrorActionPreference = 'Stop'
    $RefEmbedded = '__TLWU_REF__'
    if ($RefEmbedded -like '__TLWU_*') { $RefEmbedded = 'main' }   # placeholder not substituted = unreleased
    $Ref  = if ($env:TLWU_REF)  { $env:TLWU_REF }  else { $RefEmbedded }
    $Repo = if ($env:TLWU_REPO) { $env:TLWU_REPO } else { 'sunososobro-hub/tinglongwu' }
    $Dir  = if ($env:TLWU_DIR)  { $env:TLWU_DIR }  else { Join-Path $env:USERPROFILE 'tinglongwu' }

    $lang = $env:TLWU_UI_LANG
    if (-not $lang) { $lang = (Get-UICulture).Name }
    $L = if ($lang -like 'ru*') { 'ru' } elseif ($lang -like 'zh*') { 'zh-TW' } else { 'en' }
    # Non-English text is stored as base64 UTF-8 so this file stays pure ASCII: PS 5.1 reads BOM-less files as ANSI
    # (garbles UTF-8), and a BOM is unsafe through irm | iex. Regenerate: [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes('...'))
    $u = { param($b) [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b)) }
    $Msgs = @{
        'en' = @{ start = 'Installing tinglongWu version {0} into {1}'; git = 'Using git clone'; zip = 'git not found (or folder is not a git checkout), downloading the zip of {0}'; fail = 'Download failed (network, or unknown version {0}).'; dirty = '{0} exists but is not a tinglongWu folder; rename it or set TLWU_DIR.'; run = 'Running the installer ...' }
        'ru' = @{
            start = (& $u '0KPRgdGC0LDQvdC+0LLQutCwIHRpbmdsb25nV3UsINCy0LXRgNGB0LjRjyB7MH0sINCyIHsxfQ==')
            git = (& $u '0JjRgdC/0L7Qu9GM0LfRg9C10YLRgdGPIGdpdCBjbG9uZQ==')
            zip = (& $u 'Z2l0INC90LUg0L3QsNC50LTQtdC9ICjQuNC70Lgg0L/QsNC/0LrQsCDQvdC1IGdpdC3RgNC10L/QvtC30LjRgtC+0YDQuNC5KSwg0YHQutCw0YfQuNCy0LDQtdGC0YHRjyB6aXAg0LLQtdGA0YHQuNC4IHswfQ==')
            fail = (& $u '0JfQsNCz0YDRg9C30LrQsCDQvdC1INGD0LTQsNC70LDRgdGMICjRgdC10YLRjCDQuNC70Lgg0L3QtdC40LfQstC10YHRgtC90LDRjyDQstC10YDRgdC40Y8gezB9KS4=')
            dirty = (& $u 'ezB9INGB0YPRidC10YHRgtCy0YPQtdGCLCDQvdC+INGN0YLQviDQvdC1INC/0LDQv9C60LAgdGluZ2xvbmdXdTsg0L/QtdGA0LXQuNC80LXQvdGD0LnRgtC1INC40LvQuCDQt9Cw0LTQsNC50YLQtSBUTFdVX0RJUi4=')
            run = (& $u '0JfQsNC/0YPRgdC6INGD0YHRgtCw0L3QvtCy0YnQuNC60LAgLi4u')
        }
        'zh-TW' = @{
            start = (& $u '5a6J6KOdIHRpbmdsb25nV3Ug54mI5pysIHswfSDliLAgezF9')
            git = (& $u '5L2/55SoIGdpdCBjbG9uZQ==')
            zip = (& $u '5om+5LiN5YiwIGdpdO+8iOaIluizh+aWmeWkvuS4jeaYryBnaXQg54mI5pys77yJ77yM5pS55LiL6LyJIHswfSDnmoQgemlw')
            fail = (& $u '5LiL6LyJ5aSx5pWX77yI57ay6Lev77yM5oiW54mI5pysIHswfSDkuI3lrZjlnKjvvInjgII=')
            dirty = (& $u 'ezB9IOW3suWtmOWcqOS9huS4jeaYryB0aW5nbG9uZ1d1IOizh+aWmeWkvu+8m+iri+aUueWQjeaIluioreWumiBUTFdVX0RJUuOAgg==')
            run = (& $u '5Z+36KGM5a6J6KOd56iL5byPIC4uLg==')
        }
    }
    $M = $Msgs[$L]

    Write-Host ($M.start -f $Ref, $Dir) -ForegroundColor Cyan
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $git = Get-Command git -ErrorAction SilentlyContinue
    $isGit = Test-Path (Join-Path $Dir '.git')
    $exists = Test-Path $Dir
    if ($exists -and -not $isGit -and -not (Test-Path (Join-Path $Dir 'install.ps1')) -and (Get-ChildItem $Dir -Force | Select-Object -First 1)) {
        Write-Host ($M.dirty -f $Dir) -ForegroundColor Red; return
    }
    if ($git -and ($isGit -or -not $exists)) {
        $ErrorActionPreference = 'Continue'   # git writes progress to stderr
        Write-Host $M.git
        if ($isGit) {
            & git -C $Dir fetch --quiet --force --tags origin
            & git -C $Dir checkout --quiet $Ref
        } else {
            & git clone --quiet --branch $Ref "https://github.com/$Repo.git" $Dir
        }
        if ($LASTEXITCODE -ne 0) { Write-Host ($M.fail -f $Ref) -ForegroundColor Red; return }
    } else {
        Write-Host ($M.zip -f $Ref)
        $tmp = Join-Path ([IO.Path]::GetTempPath()) ('tlwu_' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmp | Out-Null
        try {
            $zip = Join-Path $tmp 'src.zip'
            $kind = if ($Ref -match '^v\d') { 'tags' } else { 'heads' }
            try { Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repo/archive/refs/$kind/$Ref.zip" -OutFile $zip }
            catch { Write-Host ($M.fail -f $Ref) -ForegroundColor Red; return }
            Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force
            $top = Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1
            New-Item -ItemType Directory -Path $Dir -Force | Out-Null
            Copy-Item -Path (Join-Path $top.FullName '*') -Destination $Dir -Recurse -Force   # keeps an existing .venv
        } finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    }
    Write-Host $M.run -ForegroundColor Cyan
    # 'Stop' would turn any stderr line of the child (pip/setuptools warnings) into a terminating NativeCommandError under PS 5.1
    $ErrorActionPreference = 'Continue'
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1')
    if ($LASTEXITCODE -ne 0) { Write-Host ($M.fail -f $Ref) -ForegroundColor Red; return }
}
