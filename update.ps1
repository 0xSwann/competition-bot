# Updates the competition bot to the latest version on GitHub, keeping config.toml and data\. Run via update.bat.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$repo = "https://github.com/0xSwann/competition-bot"

function Get-Hashes($paths) {
    $h = @{}
    foreach ($p in $paths) { Get-ChildItem $p -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object { $h[$_.FullName] = (Get-FileHash $_.FullName).Hash } }
    return ($h.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ";"
}
$extBefore = Get-Hashes @("extension")
$gsBefore = Get-Hashes @("phone\Code.gs")

if ((Test-Path ".git") -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Getting the latest version with git..."
    git fetch origin main
    # Your settings (config.toml) and data\ aren't part of the repository, so this never touches them.
    git checkout -f -B main origin/main
} else {
    Write-Host "Downloading the latest version..."
    $zip = Join-Path $env:TEMP "competition-bot.zip"
    $tmp = Join-Path $env:TEMP "competition-bot-update"
    Invoke-WebRequest "$repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip -DestinationPath $tmp
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
    Copy-Item (Join-Path $src.FullName "*") -Destination $PSScriptRoot -Recurse -Force
    Remove-Item $tmp -Recurse -Force; Remove-Item $zip -Force
}

if (Test-Path ".venv\Scripts\python.exe") {
    Write-Host "Updating packages..."
    & ".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
}

Write-Host "Restarting the background task..."
Stop-ScheduledTask -TaskName "Competition bot" -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName "Competition bot" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Updated." -ForegroundColor Green
if ((Get-Hashes @("extension")) -ne $extBefore) {
    Write-Host "The Chrome extension changed: open chrome://extensions and press the reload arrow on Competition bot." -ForegroundColor Yellow
}
if ((Get-Hashes @("phone\Code.gs")) -ne $gsBefore) {
    Write-Host "The Google script changed: paste phone\Code.gs into script.google.com, save, then" -ForegroundColor Yellow
    Write-Host "Deploy > Manage deployments > edit (pencil) > Version: New version > Deploy." -ForegroundColor Yellow
}
