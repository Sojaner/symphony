$ErrorActionPreference = 'Stop'
$share = '\\host.lan\Data'
$receipt = Join-Path $share 'receipt.txt'
$log = Join-Path $share 'guest.log'
$stdout = Join-Path $share 'guest.stdout.log'
$status = 'FAIL'

try {
    for ($attempt = 0; $attempt -lt 60 -and -not (Test-Path $share); $attempt++) {
        Start-Sleep -Seconds 5
    }
    if (-not (Test-Path $share)) { throw 'Dockur shared folder unavailable' }

    $python = 'C:\OEM\Python312\python.exe'
    if (-not (Test-Path $python)) {
        $installer = Start-Process -FilePath 'C:\OEM\python-3.12.10-amd64.exe' `
            -ArgumentList '/quiet InstallAllUsers=0 TargetDir=C:\OEM\Python312 Include_launcher=0 PrependPath=0' `
            -Wait -PassThru
        if ($installer.ExitCode -ne 0) { throw "Python installer exited $($installer.ExitCode)" }
    }
    if (-not (Test-Path $python)) { throw 'Python executable missing after install' }
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = $share
    # VM process startup is slow; the native Windows CI job retains the 3-second guard.
    $env:SYMPHONY_WINDOWS_INTERRUPT_LIMIT = '120'
    $test = Start-Process -FilePath $python -WorkingDirectory 'C:\Windows\Temp' -Wait -PassThru `
        -ArgumentList '-m unittest plugins.symphony.tests.test_package.PackageContractTests.test_codex_windows_hooks_run_without_a_working_py_launcher -v' `
        -RedirectStandardOutput $stdout -RedirectStandardError $log
    if ($test.ExitCode -ne 0) { throw "Windows hook test exited $($test.ExitCode)" }
    $startup = [Environment]::GetFolderPath('Startup')
    if (-not (Test-Path $startup)) { New-Item -ItemType Directory -Path $startup -Force | Out-Null }
    Copy-Item 'C:\OEM\install.bat' (Join-Path $startup 'symphony-dockur-smoke.bat') -Force
    $status = 'PASS'
} catch {
    if (Test-Path $share) { $_ | Out-File -FilePath $log -Append -Encoding utf8 }
} finally {
    if (Test-Path $share) { Set-Content -Path $receipt -Value $status -Encoding Ascii }
}
