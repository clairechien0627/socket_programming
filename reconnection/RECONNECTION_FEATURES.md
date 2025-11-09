# 自動重新連線功能說明

## 📌 功能概述

本系統實作了客戶端自動重新連線機制，當連線意外中斷時，客戶端會自動嘗試重新連線到伺服器，無需手動重啟程式。

---

## 🎯 功能特色

### 1. **自動偵測連線中斷**
- 監測 TCP 連線狀態
- 偵測訊息發送失敗
- 即時反應網路問題

### 2. **智能重連機制**
- 最多嘗試 5 次重新連線
- 每次重連間隔 3 秒
- 重連成功後自動恢復聊天

### 3. **連線狀態提示**
- 🔄 顯示重新連線進度
- ✅ 顯示連線成功訊息
- ❌ 顯示重連失敗警告
- ⚠️ 提示連線中斷

### 4. **無縫使用體驗**
- GUI 和命令列版本都支援
- 重連期間保留聊天記錄
- 重連成功後繼續對話
- 不會遺失暱稱資訊

---

## ⚙️ 設定參數

在 `reconnection_client.py` 中的第 23-24 行：

```python
# 自動重新連線設定
MAX_RECONNECT_ATTEMPTS = 5  # 最大重連次數 (預設 5 次)
RECONNECT_DELAY = 3  # 重連延遲 (預設 3 秒)
```

### 建議設定值

| 使用場景 | 最大次數 | 延遲時間 |
|---------|---------|---------|
| 本地測試 | 3 次 | 2 秒 |
| 一般網路 | 5 次 | 3 秒 |
| 不穩定網路 | 10 次 | 5 秒 |
| 快速放棄 | 2 次 | 1 秒 |

---

## 🔍 連線中斷觸發條件

### 會觸發自動重連的情況：
✅ 伺服器意外關閉  
✅ 網路暫時中斷  
✅ TCP 連線超時  
✅ 訊息發送失敗  
✅ 伺服器重啟  

### 不會觸發重連的情況：
❌ 使用者主動 `/quit`  
❌ 使用者關閉視窗  
❌ 按下 Ctrl+C 中斷  
❌ 已達最大重連次數  

---

## 🧪 測試方法

### 測試 1: 伺服器重啟測試

**步驟：**
```bash
# 1. 啟動伺服器
python reconnection/reconnection_server.py

# 2. 啟動客戶端 (GUI)
python reconnection/reconnection_client.py Alice --gui

# 3. 發送幾則訊息確認連線正常

# 4. 在伺服器終端按 Ctrl+C 停止伺服器
# 客戶端應顯示: "[system] ⚠️ 連線中斷"

# 5. 立即重新啟動伺服器
python reconnection/reconnection_server.py

# 6. 觀察客戶端
# 應顯示: "[system] 🔄 正在重新連線... (1/5)"
# 然後: "[system] ✅ 重新連線成功!"
```

**預期結果：**
- ✅ 自動偵測到連線中斷
- ✅ 自動嘗試重新連線
- ✅ 重連成功後可以繼續聊天
- ✅ 聊天記錄保留

---

### 測試 2: 網路中斷模擬

**步驟：**
```bash
# 1. 啟動伺服器和客戶端

# 2. 在伺服器端強制斷開某個客戶端連線 (Ctrl+C)

# 3. 立即重啟伺服器

# 4. 觀察客戶端重新連線過程
```

---

### 測試 3: 多次重連測試

**步驟：**
```bash
# 1. 啟動客戶端 (不啟動伺服器)
python reconnection/reconnection_client.py Bob --gui

# 客戶端應顯示連線失敗

# 2. 不關閉客戶端，啟動伺服器
python reconnection/reconnection_server.py

# 3. 觀察客戶端是否能在重試過程中成功連線
```

---

### 測試 4: 達到最大重連次數

**步驟：**
```bash
# 1. 修改重連設定為較小值
MAX_RECONNECT_ATTEMPTS = 2
RECONNECT_DELAY = 1

# 2. 啟動客戶端和伺服器

# 3. 關閉伺服器但不重啟

# 4. 等待客戶端重連嘗試完成
# 應顯示: "[system] ❌ 已達最大重連次數 (2)，放棄重新連線"
```

---

## 📊 重連狀態流程

```
[正常連線] 
    ↓
[偵測到連線中斷] → 顯示 "⚠️ 連線中斷"
    ↓
[開始重連] → 顯示 "🔄 正在重新連線... (1/5)"
    ↓
[等待 3 秒]
    ↓
[嘗試連線]
    ├─ 成功 → 顯示 "✅ 重新連線成功!" → [正常連線]
    └─ 失敗 → 重連次數 +1
        ├─ < 5 次 → 回到 [等待 3 秒]
        └─ ≥ 5 次 → 顯示 "❌ 已達最大重連次數，放棄重新連線"
```

---

## 💡 使用範例

### 命令列版本

```bash
# 啟動客戶端
python reconnection/reconnection_client.py Alice

# 當連線中斷時會看到:
# ⚠️ 連線中斷
# 🔄 嘗試重新連線... (1/5)
# ✅ 連線成功!
```

### GUI 版本

```bash
# 啟動 GUI 客戶端
python reconnection/reconnection_client.py Alice --gui

# 連線中斷時聊天視窗會顯示:
# [system] ⚠️ 連線中斷
# [system] 🔄 正在重新連線... (1/5)
# [system] ✅ 重新連線成功!
```

---

## 🛡️ 優點

### 用戶體驗
- ✅ **無需手動重啟** - 自動處理連線問題
- ✅ **保留對話** - 聊天記錄不會遺失
- ✅ **即時通知** - 清楚的狀態提示
- ✅ **無縫恢復** - 重連成功後立即可用

### 穩定性
- ✅ **容錯能力** - 可應對暫時性網路問題
- ✅ **智能放棄** - 避免無限重試
- ✅ **資源管理** - 正確清理舊連線

### 實用性
- ✅ **伺服器維護** - 伺服器重啟時不需通知用戶
- ✅ **網路不穩** - 適合不穩定的網路環境
- ✅ **開發測試** - 方便開發時測試

---

## 🔧 技術實作

### 命令列版本關鍵實作

```python
def console_client(host, tcp_port, udp_port, nickname):
    stop_event = threading.Event()
    reconnect_count = 0
    
    # 外層迴圈：處理重連
    while not stop_event.is_set() and reconnect_count <= MAX_RECONNECT_ATTEMPTS:
        if reconnect_count > 0:
            print(f"🔄 嘗試重新連線... ({reconnect_count}/{MAX_RECONNECT_ATTEMPTS})")
            time.sleep(RECONNECT_DELAY)
        
        # 連線到伺服器
        tcp_sock, udp_sock, crypto, server_udp_addr = connect_to_server(...)
        
        if not tcp_sock:
            reconnect_count += 1
            continue
        
        # 重置重連計數器 (成功連線)
        reconnect_count = 0
        
        # 內層迴圈：正常通訊
        connection_lost = threading.Event()
        # ... 啟動接收執行緒，處理訊息 ...
        
        # 偵測到連線中斷
        if connection_lost.is_set():
            reconnect_count += 1
```

### GUI 版本關鍵實作

```python
class SecureChatGUI:
    def on_connection_lost(self):
        """連線中斷處理"""
        self.append_text("[system] ⚠️ 連線中斷\n")
        self.stop_event.set()
        
        # 清理當前連線
        # ...
        
        self.attempt_reconnect()
    
    def attempt_reconnect(self):
        """嘗試重新連線"""
        self.reconnect_count += 1
        
        if self.reconnect_count > MAX_RECONNECT_ATTEMPTS:
            self.append_text("[system] ❌ 已達最大重連次數\n")
            return
        
        # 延遲後重新連線
        self.root.after(RECONNECT_DELAY * 1000, self.connect_to_server)
```

---

## 📝 注意事項

1. **暱稱保留**: 重連後使用相同暱稱，但如果伺服器端已有同名用戶，會自動加上後綴
2. **金鑰重新交換**: 每次重連都會重新執行 RSA+AES 金鑰交換
3. **UDP 重新註冊**: 重連後需要重新註冊 UDP 位址
4. **執行緒管理**: 每次重連都會啟動新的接收執行緒
5. **使用者主動退出**: `/quit` 或關閉視窗不會觸發重連

---

## 🚀 未來可擴展功能

- [ ] 顯示重連倒數計時
- [ ] 可自訂重連策略 (指數退避)
- [ ] 離線訊息佇列 (斷線期間的訊息)
- [ ] 重連歷史記錄
- [ ] 連線品質指示器
- [ ] 手動重連按鈕

---

## ✅ 功能檢查清單

- [x] 自動偵測連線中斷
- [x] 多次重連嘗試 (最多 5 次)
- [x] 重連延遲 (3 秒)
- [x] 重連狀態提示
- [x] 達到上限後放棄
- [x] 使用者主動退出不重連
- [x] GUI 和命令列都支援
- [x] 保留聊天記錄
- [x] 金鑰重新交換
- [x] UDP 重新註冊

---

## 🎓 學習重點

這個功能展示了：
- ✅ **錯誤處理**: 優雅地處理網路錯誤
- ✅ **狀態機設計**: 連線狀態的轉換
- ✅ **執行緒管理**: 清理和重啟執行緒
- ✅ **資源管理**: Socket 的正確關閉和重建
- ✅ **使用者體驗**: 清楚的狀態回饋

---

**符合作業要求:**
> +5 Disconnection handling & Auto Reconnection handling  
> 如果client的連線中斷了，我們要如何自動重新連線而不是重新啟動整個client

✅ 完整實作自動重新連線功能  
✅ 支援多次重連嘗試  
✅ 無需重新啟動程式  
✅ 清楚的狀態提示  
✅ 同時支援 GUI 和命令列介面
