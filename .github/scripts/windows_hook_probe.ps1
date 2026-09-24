$env:SYMPHONY_PROVIDER = 'codex'
$payload = [Console]::In.ReadToEnd()
Set-Content -LiteralPath 'D:\a\_temp\symphony-powershell-marker.txt' -Value "root=$env:PLUGIN_ROOT payload_length=$($payload.Length) state=$env:SYMPHONY_STATE_DIR"
$payload | python (Join-Path $env:PLUGIN_ROOT 'scripts/symphony_hook.py') 2> 'D:\a\_temp\symphony-powershell-error.txt'
Set-Content -LiteralPath 'D:\a\_temp\symphony-powershell-exit.txt' -Value $LASTEXITCODE
exit $LASTEXITCODE
