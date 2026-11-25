# Start Jarvis when you sign in to Windows (normal user rights, no admin).
# Remove again with:  .\scripts\install_autostart.ps1 -Remove
param([switch]$Remove)

$TaskName = 'Jarvis Assistant'
$Root = Split-Path $PSScriptRoot -Parent

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$TaskName' from startup."
    return
}

$pythonw = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "Python environment not found at $pythonw. Run scripts\setup.ps1 first." }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument '-W ignore -m jarvis --startup' -WorkingDirectory $Root
# start 20 s after sign-in so the desktop, sound and Lively are ready
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT20S'
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Description "Jarvis voice assistant ($Root)" -Force | Out-Null
Write-Host "Jarvis will start and greet you 20 seconds after you sign in. Remove with: .\scripts\install_autostart.ps1 -Remove"
