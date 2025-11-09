@echo off
chcp 65001 >nul
echo ========================================
echo 多埠口聊天室測試腳本
echo ========================================
echo.
echo 正在啟動伺服器和兩個客戶端...
echo.

REM 啟動伺服器
start "Multi-Port Server" python multi_port\multi_port_server.py

REM 等待伺服器啟動
timeout /t 2 /nobreak >nul

REM 啟動 Alice 客戶端 (GUI)
start "Client - Alice" python multi_port\multi_port_client.py Alice --gui

REM 等待第一個客戶端連線
timeout /t 1 /nobreak >nul

REM 啟動 Bob 客戶端 (GUI)
start "Client - Bob" python multi_port\multi_port_client.py Bob --gui

echo.
echo ========================================
echo 測試步驟:
echo 1. 兩個客戶端視窗已開啟
echo 2. 點擊 [📎 傳檔] 按鈕選擇檔案
echo 3. 觀察檔案傳輸進度
echo 4. 對方會收到檔案通知
echo 5. 同時測試聊天訊息是否不受影響
echo ========================================
echo.
echo 按任意鍵關閉此視窗...
pause >nul
