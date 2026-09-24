$env:SYMPHONY_PROVIDER = 'codex'
$script = Join-Path $env:PLUGIN_ROOT 'scripts/symphony_hook.py'
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = 'python'
$start.Arguments = '"' + $script + '"'
$start.UseShellExecute = $false
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true
$child = [System.Diagnostics.Process]::Start($start)
Set-Content -LiteralPath 'D:\a\_temp\symphony-powershell-marker.txt' -Value "root=$env:PLUGIN_ROOT state=$env:SYMPHONY_STATE_DIR child=$($child.Id)"
$outTask = $child.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
$errTask = $child.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
$source = [Console]::OpenStandardInput()
$capture = [System.IO.File]::Create('D:\a\_temp\symphony-hook-input.bin')
$buffer = New-Object byte[] 8192
$bytes = 0
while (($count = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
    $child.StandardInput.BaseStream.Write($buffer, 0, $count)
    $capture.Write($buffer, 0, $count)
    $bytes += $count
}
$capture.Close()
Add-Content -LiteralPath 'D:\a\_temp\symphony-powershell-marker.txt' -Value "bytes=$bytes"
$child.StandardInput.BaseStream.Close()
$child.WaitForExit()
[System.Threading.Tasks.Task]::WaitAll(@($outTask, $errTask))
Set-Content -LiteralPath 'D:\a\_temp\symphony-powershell-exit.txt' -Value $child.ExitCode
exit $child.ExitCode
