@echo off
echo Starting chat server...
start "Server" python base\server.py

timeout /t 2 /nobreak >nul

echo Starting client Alice with GUI...
start "Alice" python base\client.py Alice --gui

timeout /t 1 /nobreak >nul

echo Starting client Bob with GUI...
start "Bob" python base\client.py Bob --gui

echo.
echo All processes started!
echo Close each window when you're done testing.
