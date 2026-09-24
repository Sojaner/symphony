Set-Content -LiteralPath 'D:\a\_temp\symphony-relay-entry.txt' -Value 'entered'
$ErrorActionPreference = 'Stop'
$env:SYMPHONY_PROVIDER = 'codex'

$script = Join-Path $env:PLUGIN_ROOT 'scripts/symphony_hook.py'
$python = (Get-Command python.exe -CommandType Application -ErrorAction Stop).Source
Add-Content -LiteralPath 'D:\a\_temp\symphony-relay-entry.txt' -Value "python=$python"
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $python
$start.Arguments = '"' + $script + '"'
$start.UseShellExecute = $false
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true

try {
    $child = [System.Diagnostics.Process]::Start($start)
    Add-Content -LiteralPath 'D:\a\_temp\symphony-relay-entry.txt' -Value "child=$($child.Id)"
} catch {
    [Console]::Error.WriteLine('Symphony launcher could not start Python')
    exit 1
}

$stdout = $child.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
$stderr = $child.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
[Console]::OpenStandardInput().CopyTo($child.StandardInput.BaseStream)
Add-Content -LiteralPath 'D:\a\_temp\symphony-relay-entry.txt' -Value 'stdin-copied'
$child.StandardInput.BaseStream.Close()
$child.WaitForExit()
[System.Threading.Tasks.Task]::WaitAll(@($stdout, $stderr))
Add-Content -LiteralPath 'D:\a\_temp\symphony-relay-entry.txt' -Value "exit=$($child.ExitCode)"
exit $child.ExitCode
