@echo off
rem QA Agent Pro - MCP server entry point (native Windows). Point your MCP
rem client at this file. It runs the launcher (update-check + integrity
rem self-heal + read-only lock) and then serves MCP over stdio.
rem
rem NOTHING may be echoed to stdout: stdout IS the MCP transport, and one
rem stray line makes the client reject the handshake. Hence @echo off, and
rem hence no banner here.
setlocal
cd /d "%~dp0"
set "QA_PY=%~dp0.venv\Scripts\python.exe"
if not exist "%QA_PY%" set "QA_PY=python"
"%QA_PY%" "%~dp0launcher.py" %*
