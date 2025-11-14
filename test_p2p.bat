@echo off
chcp 65001 > nul
echo ========================================
echo    P2P 分散式下載測試
echo ========================================

echo.
echo 啟動 Tracker Server...
start "P2P Tracker Server" python p2p/p2p_server.py

timeout /t 2 /nobreak > nul

echo.
echo 啟動 Peer Alice (Port 7001)...
start "Peer Alice" python p2p/p2p_client.py Alice --gui --p2p-port 7001

timeout /t 1 /nobreak > nul

echo 啟動 Peer Bob (Port 7002)...
start "Peer Bob" python p2p/p2p_client.py Bob --gui --p2p-port 7002

timeout /t 1 /nobreak > nul

echo 啟動 Peer Charlie (Port 7003)...
start "Peer Charlie" python p2p/p2p_client.py Charlie --gui --p2p-port 7003
