# #1712: install.ps1 lives in scripts\install.ps1. This stub keeps the root path working for callers that
# still use it (release bootstrap install.ps1 of older tags, the wizard's run_install.ps1, docs). PowerShell 5.1+.
& (Join-Path (Join-Path $PSScriptRoot 'scripts') 'install.ps1') @args
exit $LASTEXITCODE
