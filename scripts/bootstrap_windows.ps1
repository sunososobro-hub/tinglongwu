# tinglongWu Windows bootstrap (published as install.ps1 on each GitHub Release). PowerShell 5.1+.
#
#   irm https://github.com/sunososobro-hub/tinglongwu/releases/latest/download/install.ps1 | iex
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
    $Msgs = @{
        'en' = @{ start = 'Installing tinglongWu version {0} into {1}'; git = 'Using git clone'; zip = 'git not found (or folder is not a git checkout), downloading the zip of {0}'; fail = 'Download failed (network, or unknown version {0}).'; dirty = '{0} exists but is not a tinglongWu folder; rename it or set TLWU_DIR.'; run = 'Running the installer ...' }
        'ru' = @{ start = 'Установка tinglongWu, версия {0}, в {1}'; git = 'Используется git clone'; zip = 'git не найден (или папка не git-репозиторий), скачивается zip версии {0}'; fail = 'Загрузка не удалась (сеть или неизвестная версия {0}).'; dirty = '{0} существует, но это не папка tinglongWu; переименуйте или задайте TLWU_DIR.'; run = 'Запуск установщика ...' }
        'zh-TW' = @{ start = '安裝 tinglongWu 版本 {0} 到 {1}'; git = '使用 git clone'; zip = '找不到 git（或資料夾不是 git 版本），改下載 {0} 的 zip'; fail = '下載失敗（網路，或版本 {0} 不存在）。'; dirty = '{0} 已存在但不是 tinglongWu 資料夾；請改名或設定 TLWU_DIR。'; run = '執行安裝程式 ...' }
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
        Write-Host $M.git
        if ($isGit) {
            & git -C $Dir fetch --quiet --tags origin
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
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1')
}
