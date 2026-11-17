@echo off
rem Run this ONCE (double-click). It creates a Windows scheduled task that runs run.bat every 30 minutes.
schtasks /Create /F /SC MINUTE /MO 30 /TN "TrainerCentralAutomation" /TR "\"%~dp0run.bat\""
echo.
echo To check it:   schtasks /Query /TN "TrainerCentralAutomation"
echo To remove it:  schtasks /Delete /TN "TrainerCentralAutomation" /F
pause