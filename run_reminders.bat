@echo off
REM ===========================================================================
REM run_reminders.bat — runs the FR7 background reminder-email job.
REM Invoked by Windows Task Scheduler (see README, "Windows (Task Scheduler)").
REM
REM Why this wrapper exists:
REM   * cd to the project folder, so Task Scheduler's working directory
REM     (which schtasks cannot set) never matters;
REM   * uses the project venv's python, so no PATH setup is needed;
REM   * appends a timestamped START/END line to reminders.log around every
REM     run, so you can confirm the schedule actually fired (sanity check
REM     documented in the README). reminders.log matches *.log in .gitignore.
REM ===========================================================================
cd /d "%~dp0"

echo [%date% %time%] run_reminders START >> reminders.log
"%~dp0venv\Scripts\python.exe" manage.py send_reminders >> reminders.log 2>&1
set EXITCODE=%errorlevel%
echo [%date% %time%] run_reminders END exit=%EXITCODE% >> reminders.log
exit /b %EXITCODE%
