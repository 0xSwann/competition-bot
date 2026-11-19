# One-time setup for the competition bot on Windows 11. Run via setup.bat.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "Checking for Python 3.11 or newer..."
$py = $null; $pyArgs = @(); $ok = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $py = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $py = "python" }
if ($py) {
    try { $ok = & $py @pyArgs -c "import sys; print(sys.version_info >= (3, 11))" 2>$null } catch {}
}
if ($ok -ne "True") {
    Write-Host "Python 3.11+ not found. Install it free with:  winget install Python.Python.3.12" -ForegroundColor Yellow
    Write-Host "Then close this window and run setup.bat again."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $py @pyArgs -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$venvPyw = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"

Write-Host "Installing packages..."
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt --quiet

# Edge ships with Windows 11, so Playwright can use it without downloading a browser.
# Download Playwright's own Chromium too as a fallback (free, ~150 MB).
Write-Host "Installing fallback browser..."
& $venvPy -m playwright install chromium

if (-not (Test-Path "config.toml")) {
    Copy-Item "config.example.toml" "config.toml"
    Write-Host ""
    Write-Host "Created config.toml. Fill it in (Discord webhook, the relay_url line from the Google script, your details)," -ForegroundColor Yellow
    Write-Host "then run setup.bat again to finish." -ForegroundColor Yellow
    Start-Process notepad.exe "config.toml"
    exit 0
}

Write-Host "Testing Discord..."
& $venvPy -m compbot test-discord

Write-Host "Registering the background task (starts when you log in)..."
$action = New-ScheduledTaskAction -Execute $venvPyw -Argument "-m compbot run" -WorkingDirectory $PSScriptRoot
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
$principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5) -StartWhenAvailable
Stop-ScheduledTask -TaskName "Competition bot" -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "Competition bot" -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Watches OzBargain comps and collects win emails, alerts Discord" -Force | Out-Null
Start-ScheduledTask -TaskName "Competition bot"

Write-Host "Creating 'Enter comps' and 'Comp stats' shortcuts on your desktop..."
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Enter comps.lnk"))
$lnk.TargetPath = Join-Path $PSScriptRoot "enter.bat"
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.Save()
$lnk = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Comp stats.lnk"))
$lnk.TargetPath = Join-Path $PSScriptRoot "stats.bat"
$lnk.WorkingDirectory = $PSScriptRoot
$lnk.Save()

Write-Host ""
Write-Host "All set. The bot is running in the background and will post to Discord." -ForegroundColor Green
Write-Host "Double-click 'Enter comps' on your desktop whenever you want to work through new comps."
Write-Host "Double-click 'Comp stats' to see entries, time spent and wins, and to record a win."
Write-Host "Last step: add the Chrome extension. See 'Chrome extension' in README.md (chrome://extensions, Load unpacked, the extension folder)."
Write-Host "To update later, double-click update.bat."
