# 加密聊天室測試指南

## 🎯 測試目標

驗證以下功能:
1. ✅ RSA-2048 金鑰交換
2. ✅ AES-256-CBC 訊息加密
3. ✅ HMAC-SHA256 完整性驗證
4. ✅ 多客戶端加密通訊
5. ✅ GUI 模式加密通訊

---

## 📝 測試步驟

### 測試 1: 基本加密通訊

**步驟:**
```bash
# 終端 1: 啟動伺服器
cd secure
python secure_server.py

# 終端 2: 啟動客戶端 Alice
python secure_client.py Alice

# 終端 3: 啟動客戶端 Bob
python secure_client.py Bob
```

**預期結果:**
```
[伺服器輸出]
✅ RSA 金鑰對生成完成
📡 監聽端口: TCP 6678, UDP 6679
🔑 開始與 ('127.0.0.1', xxxx) 進行金鑰交換...
✅ 與 ('127.0.0.1', xxxx) 的金鑰交換成功
🔒 [TCP] Alice 已建立加密連線
🔒 [TCP] Bob 已建立加密連線

[客戶端輸出 - Alice]
🔑 開始金鑰交換...
✅ 成功匯入伺服器公鑰
🔐 AES-256 會話金鑰已生成
🔒 AES 金鑰已加密 (256 bytes)
📤 已發送加密的 AES 金鑰
✅ 金鑰交換成功
🔒 歡迎 Alice! 連線已加密 (AES-256 + HMAC-SHA256)
[system] 🔐 Alice 已加入聊天室 (加密連線)
[system] 🔐 Bob 已加入聊天室 (加密連線)

Alice > Hello Bob!

[客戶端輸出 - Bob]
[同上]
[Alice] Hello Bob!

Bob > Hi Alice!
```

✅ **成功標準**: 
- 金鑰交換成功完成
- 訊息成功加密並傳送
- 兩個客戶端都能看到對方的訊息

---

### 測試 2: GUI 模式加密通訊

**步驟:**
```bash
# 終端 1: 伺服器已運行

# 終端 2: 啟動 GUI 客戶端
python secure_client.py Charlie --gui
```

**預期結果:**
- GUI 視窗顯示「🔒 安全連線已建立」
- 可以在文字框輸入訊息並發送
- 能看到其他客戶端的加密訊息
- 發送按鈕和 Enter 鍵都可以發送訊息

✅ **成功標準**: 
- GUI 正常顯示
- 可以發送和接收加密訊息
- 介面流暢無卡頓

---

### 測試 3: 多客戶端同時通訊

**步驟:**
```bash
# 同時運行 5 個客戶端
python secure_client.py Alice
python secure_client.py Bob
python secure_client.py Charlie --gui
python secure_client.py David
python secure_client.py Eve --gui
```

**測試內容:**
1. Alice 發送: "Hello everyone!"
2. Bob 發送: "Hi Alice!"
3. Charlie (GUI) 發送: "Testing from GUI"
4. David 發送: "Multiple clients working!"
5. Eve (GUI) 發送: "Encryption is awesome!"

**預期結果:**
- 所有客戶端都收到所有訊息
- 每個客戶端使用獨立的 AES 會話金鑰
- 伺服器日誌顯示 5 個獨立的金鑰交換

✅ **成功標準**: 
- 5 個客戶端同時在線
- 訊息廣播正常
- 沒有訊息丟失

---

### 測試 4: 指令測試

**步驟:**
在客戶端輸入以下指令:

```
/users     # 查看在線用戶
/quit      # 離開聊天室
```

**預期結果:**
```
/users
[system] 📋 當前在線用戶 (3):
  • Alice
  • Bob
  • Charlie

/quit
👋 [system] Alice 已離開聊天室
```

✅ **成功標準**: 
- 指令正常執行
- 系統訊息正常顯示

---

## 🔐 安全性驗證

### 驗證 1: 加密強度
查看伺服器啟動訊息,確認:
- ✅ RSA-2048 (2048位元)
- ✅ AES-256-CBC (256位元)
- ✅ HMAC-SHA256 (256位元)

### 驗證 2: 獨立會話金鑰
查看伺服器日誌,確認每個客戶端都有:
```
✅ AES 會話金鑰已設定
✅ 與 ('127.0.0.1', xxxx) 的金鑰交換成功
```

### 驗證 3: 訊息格式
訊息在網路上傳輸時為加密格式:
```json
{
  "type": "encrypted",
  "data": {
    "ciphertext": "base64_encoded...",
    "mac": "base64_encoded...",
    "iv": "base64_encoded..."
  }
}
```

---

## 🐛 常見問題

### Q1: 金鑰交換失敗
**症狀**: `❌ 金鑰交換失敗`

**解決方案**:
1. 確認伺服器已啟動
2. 確認端口 6678 未被占用
3. 檢查防火牆設定

### Q2: 連線中斷
**症狀**: `Connection reset by peer`

**解決方案**:
1. 重啟伺服器
2. 確認網路連線正常
3. 檢查是否有其他程式占用端口

### Q3: 訊息未加密
**症狀**: 看到明文訊息

**解決方案**:
1. 確認使用 `secure_server.py` 和 `secure_client.py`
2. 確認金鑰交換成功完成
3. 檢查加密模組是否正確安裝

---

## 📊 測試檢查清單

- [ ] 伺服器成功啟動並生成 RSA 金鑰
- [ ] 客戶端成功連線並完成金鑰交換
- [ ] 訊息成功加密並傳送
- [ ] 多客戶端同時在線
- [ ] GUI 模式正常運作
- [ ] `/users` 指令正常
- [ ] `/quit` 指令正常
- [ ] 無錯誤訊息或異常

---

## ✅ 測試完成

當以上所有測試都通過時,代表加密聊天室功能完整且正常運作!

**恭喜!** 🎉 您已成功實作並測試了端到端加密聊天系統!
