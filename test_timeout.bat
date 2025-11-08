@echo off
chcp 65001 > nul
echo ========================================
echo   閒置超時測試腳本
echo ========================================
echo.

echo 正在啟動伺服器 (帶閒置超時功能)...
start "Timeout Server" python timeout\timeout_server.py

timeout /t 3 /nobreak >nul

echo 正在啟動客戶端 Alice (GUI)...
start "Alice" python timeout\timeout_client.py Alice --gui

timeout /t 2 /nobreak >nul

echo 正在啟動客戶端 Bob (GUI)...
start "Bob" python timeout\timeout_client.py Bob --gui

echo.
echo ========================================
echo 測試已啟動!
echo.
echo 伺服器端口:
echo   - TCP: 6678 (加密聊天)
echo   - UDP: 6679 (狀態更新)
echo.
echo ⏰ 超時設定:
echo   - 閒置警告: 5 分鐘
echo   - 閒置斷線: 10 分鐘
echo   - 心跳超時: 30 秒
echo.
echo 💡 測試方法:
echo   1. 連線後不發送任何訊息
echo   2. 等待 5 分鐘應收到警告
echo   3. 等待 10 分鐘應被自動斷線
echo.
echo 關閉各視窗以結束測試
echo ========================================
pause
