@echo off
powershell -NoProfile -Command "Stop-ScheduledTask -TaskName 'Competition bot' -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName 'Competition bot' -Confirm:$false -ErrorAction SilentlyContinue"
del "%USERPROFILE%\Desktop\Enter comps.lnk" 2>nul
del "%USERPROFILE%\Desktop\Comp stats.lnk" 2>nul
echo Background task and shortcuts removed. Delete this folder to remove everything else.
pause
