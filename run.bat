@echo off
rem Runs one pass of the automation. Task Scheduler calls this file every 30 minutes.
cd /d "%~dp0"
".venv\Scripts\python.exe" tc_automation.py