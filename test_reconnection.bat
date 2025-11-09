@echo off
chcp 65001 >nul
title 測試自動重新連線功能

echo ============================================================
echo 🔄 自動重新連線測試
echo ============================================================
echo.
echo 測試步驟:
echo   1. 等待所有視窗啟動完成
echo   2. 在伺服器視窗按 Ctrl+C 停止伺服器
echo   3. 觀察客戶端顯示 "⚠️ 連線中斷"
echo   4. 立即重新啟動伺服器 (按向上鍵 + Enter)
echo   5. 觀察客戶端自動重新連線
echo.
echo 重新連線設定:
echo   • 最大重連次數: 5 次
echo   • 重連延遲: 3 秒
echo.
echo ============================================================
echo.

echo 🚀 啟動伺服器...
start "Reconnection Server" python reconnection\reconnection_server.py

timeout /t 2 >nul

echo 🚀 啟動 Alice...
start "Alice" python reconnection\reconnection_client.py Alice --gui

timeout /t 2 >nul

echo 🚀 啟動 Bob (命令列)...
start "Bob" python reconnection\reconnection_client.py Bob --gui

echo.
echo ✅ 所有程式已啟動!
echo.
echo 💡 測試提示:
echo   - 在伺服器視窗按 Ctrl+C 來模擬連線中斷
echo   - 立即重啟伺服器來測試自動重新連線
echo   - 觀察客戶端的重連訊息
echo.
pause
