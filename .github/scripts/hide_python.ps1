# Dot-source in a CI step: make this windows-latest runner look like a PC that never had Python (#67 / #69).
# Takes every Python / tool-cache / WindowsApps (Store python.exe stub) entry off PATH for this step and its child
# processes, moves the py launcher away (runner is admin), and fails if `python` or `py` still resolves.
$env:Path = (($env:Path -split ';') | Where-Object { $_ -and $_ -notmatch '(?i)python|hostedtoolcache|WindowsApps|pipx' }) -join ';'
foreach ($c in @(Get-Command py, pyw -All -ErrorAction SilentlyContinue)) {
    Move-Item -LiteralPath $c.Source -Destination "$($c.Source).hidden" -Force
}
[Environment]::SetEnvironmentVariable('PYTHONHOME', $null, 'Process')
[Environment]::SetEnvironmentVariable('PYTHONPATH', $null, 'Process')
$left = @(Get-Command python, python3, py -All -ErrorAction SilentlyContinue)
if ($left) { throw "Python still reachable: $($left.Source -join ', ')" }
Write-Host "no python / py on PATH: this runner now looks like a fresh Windows"
