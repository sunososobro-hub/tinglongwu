# Provision Python for install.ps1 when the PC has none (#67). Dot-sourced by install.ps1. PowerShell 5.1+, ASCII only.
# No admin needed: the pinned python.org installer runs silently for the current user only. The download is
# SHA256-checked before it is run; if it cannot be downloaded or verified, winget is tried (when present).
# Pin source: python.org release 3.12.10 (last 3.12 with a Windows installer); SHA256 as published in the
# microsoft/winget-pkgs manifest Python.Python.3.12 3.12.10 (x64, InstallerSha256).
$TlwuPyUrl    = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
$TlwuPySha256 = '67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB'
$TlwuPyInstallArgs = @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=0', 'Include_test=0')
$TlwuPyWingetArgs  = @('install', '-e', '--id', 'Python.Python.3.12', '--scope', 'user', '--silent',
                       '--accept-package-agreements', '--accept-source-agreements')

function Get-TlwuPythonTarget {   # where the per-user python.org / winget install puts python.exe
    $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME 'AppData\Local' }
    return (Join-Path $base 'Programs\Python\Python312\python.exe')
}

function Test-TlwuSha256([string]$Path, [string]$Expected) {
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    return ($h -and ($h.ToUpper() -eq "$Expected".Trim().ToUpper()))
}

# Returns the path of the installed python.exe, or '' when it could not be installed.
# -Download / -RunInstaller / -Winget replace the real actions (tests); defaults do the real thing.
function Install-TlwuPython {
    param(
        [switch]$DryRun,
        [string]$Url,
        [string]$Sha256,
        [string]$Target,
        [scriptblock]$Download,
        [scriptblock]$RunInstaller,
        [scriptblock]$Winget
    )
    if (-not $Url)    { $Url = $TlwuPyUrl }
    if (-not $Sha256) { $Sha256 = $TlwuPySha256 }
    if (-not $Target) { $Target = Get-TlwuPythonTarget }
    if (-not $Download) {
        $Download = {
            param($u, $o)
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            $ProgressPreference = 'SilentlyContinue'   # the PS 5.1 progress bar makes the download many times slower
            Invoke-WebRequest -UseBasicParsing -Uri $u -OutFile $o
        }
    }
    if (-not $RunInstaller) {
        $RunInstaller = { param($exe, $a) $p = Start-Process -FilePath $exe -ArgumentList $a -Wait -PassThru; $p.ExitCode }
    }
    if (-not $Winget) {
        $Winget = {
            param($a)
            if (Get-Command winget -ErrorAction SilentlyContinue) { & winget @a | Out-Host }
            else { Write-Host "      winget not available" }
        }
    }
    $name = $Url.Substring($Url.LastIndexOf('/') + 1)
    if ($DryRun) {
        Write-Host "  [dry-run] download $Url (SHA256 $Sha256)" -ForegroundColor DarkGray
        Write-Host "  [dry-run] $name $($TlwuPyInstallArgs -join ' ')" -ForegroundColor DarkGray
        Write-Host "  [dry-run] fallback: winget $($TlwuPyWingetArgs -join ' ')" -ForegroundColor DarkGray
        return $Target
    }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ('tlwu_py_' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $exe = Join-Path $tmp $name
    try {
        Write-Host "      Downloading $Url (about 26 MB) ..."
        try { & $Download $Url $exe | Out-Null }
        catch { Write-Host "  !!  Python download failed: $_" -ForegroundColor Yellow }
        if (Test-Path -LiteralPath $exe) {
            if (Test-TlwuSha256 $exe $Sha256) {
                Write-Host "      SHA256 OK. Installing Python for this user (no admin, about 1 minute) ..."
                $code = @(& $RunInstaller $exe $TlwuPyInstallArgs)[-1]
                if ("$code" -ne '0' -and "$code" -ne '3010') {
                    Write-Host "  !!  Python installer exit code: $code" -ForegroundColor Yellow
                }
            } else {
                Write-Host "  !!  SHA256 mismatch for $name (expected $Sha256): NOT running it." -ForegroundColor Yellow
            }
        }
    } finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    if (-not (Test-Path -LiteralPath $Target)) {
        Write-Host "      Trying winget instead ..."
        try { & $Winget $TlwuPyWingetArgs | Out-Null }
        catch { Write-Host "  !!  winget failed: $_" -ForegroundColor Yellow }
    }
    if (Test-Path -LiteralPath $Target) { return $Target }
    return ''
}
