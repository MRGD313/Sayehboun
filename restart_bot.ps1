# Force stop all Sayeh bot instances, then start one clean instance.
Set-Location $PSScriptRoot
py bot_ctl.py restart @args
