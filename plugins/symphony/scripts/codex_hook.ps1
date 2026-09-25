# Encoded into hooks/codex.json; PowerShell does not load this file.
$ErrorActionPreference = 'Stop'
$env:SYMPHONY_PROVIDER = 'codex'

$script = Join-Path $env:PLUGIN_ROOT 'scripts/symphony_hook.py'
$python = (Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = $python
$start.Arguments = '"' + $script + '"'
$start.UseShellExecute = $false
$start.RedirectStandardInput = $true
$start.RedirectStandardOutput = $true
$start.RedirectStandardError = $true

try {
    $child = [System.Diagnostics.Process]::Start($start)
} catch {
    [Console]::Error.WriteLine('Symphony launcher could not start Python')
    exit 1
}

$stdout = $child.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
$stderr = $child.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
[Console]::OpenStandardInput().CopyTo($child.StandardInput.BaseStream)
$child.StandardInput.BaseStream.Close()
$child.WaitForExit()
[System.Threading.Tasks.Task]::WaitAll(@($stdout, $stderr))
exit $child.ExitCode
