@echo off
chcp 65001 > nul
echo ========================================
echo   安全聊天室測試腳本 (加密版)
echo ========================================
echo.

echo 正在啟動伺服器...
start "Secure Server" python secure\secure_server.py

timeout /t 3 /nobreak >nul

echo 正在啟動客戶端 Alice (GUI)...
start "Alice" python secure\secure_client.py Alice --gui

timeout /t 2 /nobreak >nul

echo 正在啟動客戶端 Bob (GUI)...
start "Bob" python secure\secure_client.py Bob --gui

echo.
echo ========================================
echo 測試已啟動!
echo.
echo 伺服器端口:
echo   - TCP: 6678 (加密聊天)
echo   - UDP: 6679 (狀態更新)
echo.
echo 加密方案:
echo   - RSA-2048 (金鑰交換)
echo   - AES-256-CBC (訊息加密)
echo   - HMAC-SHA256 (完整性驗證)
echo.
echo 關閉各視窗以結束測試
echo ========================================
pause
