@echo off
chcp 65001 > nul
echo ========================================
echo    Nonblocking 聊天室測試
echo ========================================
echo.
echo 測試項目:
echo   1. 多用戶同時連線 (非阻塞)
echo   2. 檔案傳輸不影響聊天 (多埠口)
echo   3. GUI 保持流暢 (非同步)
echo.
echo 啟動伺服器...
echo ========================================
start "Nonblocking Server" python nonblocking/nonblocking_server.py

timeout /t 2 /nobreak > nul

echo.
echo 啟動客戶端 Alice...
start "Client Alice" python nonblocking/nonblocking_client.py Alice --gui

timeout /t 1 /nobreak > nul

echo 啟動客戶端 Bob...
start "Client Bob" python nonblocking/nonblocking_client.py Bob --gui


echo.
echo ========================================
echo 測試說明:
echo ========================================
echo.
echo [測試 1] 多用戶非阻塞
echo   → 三個客戶端同時發訊息
echo   → 觀察是否全部立即送達
echo.
echo [測試 2] 檔案傳輸非阻塞
echo   → Alice 點擊 [📎 傳檔] 傳送檔案
echo   → Bob 同時發送訊息
echo   → 觀察聊天是否受影響
echo.
echo [測試 3] GUI 非阻塞
echo   → 接收大量訊息時
echo   → 觀察輸入框是否能正常輸入
echo.
echo ✅ 如果以上都正常 = Nonblocking 成功！
echo ========================================
pause
