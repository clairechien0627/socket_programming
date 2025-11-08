# Socket Programming
## What is socket?
就是「程式用來跟網路說話的門口」。作業系統提供它，讓你的程式能把資料送到網路上、或從網路收回資料。

它是什麼？
一個由作業系統管理的通訊介面（抽象化物件）。在網路上，通常由「IP + 埠號（port） + 協定」來辨識。

怎麼用？（常見流程）

Server（伺服器）：建立 → 綁定(bind) → 監聽(listen, 只限 TCP) → 接受(accept, 只限 TCP) → 收發資料

Client（用戶端）：建立 → 連線(connect, TCP) 或 直接 sendto/recvfrom(UDP) → 收發資料

兩種主流型態

TCP socket（SOCK_STREAM）：連線導向、可靠、有順序，像電話通話。

UDP socket（SOCK_DGRAM）：無連線、盡力而為、有封包邊界，像寄明信片。

識別方式

TCP 一條連線可用「五元組」描述：{協定, 本地IP, 本地Port, 遠端IP, 遠端Port}。

UDP 每個封包可來自不同來源，所以常用 recvfrom() 同時取得資料和來源位址。


為什麼需要它？
它把底層網路細節（封包、重傳、序號、快取等）封裝起來，提供一致的 API，讓程式專注於「收資料／送資料」。

![螢幕擷取畫面 2025-10-20 144626](https://hackmd.io/_uploads/BJFR9UXCxx.png)


## TCP

### Workflow
![image](https://hackmd.io/_uploads/rkcrpCwCgx.png)


### Server
```python=
import socket  # 匯入標準庫 socket，提供網路通訊

HOST = "127.0.0.1"  # 伺服器要綁定的 IP（本機）
PORT = 5678         # 伺服器監聽的 TCP 埠號（需與 client 一致）
BUFFER_SIZE = 256   # 每次接收的最大位元組數（對應 C 程式的 buf 大小）
BACKLOG = 5         # listen 等待佇列大小（對應 C: listen(sock, 5)）

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 建立 IPv4/TCP socket
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 允許位址重用，重啟時較不易 bind 失敗
srv.bind((HOST, PORT))  # 綁定位址與埠號
srv.listen(BACKLOG)  # 進入監聽狀態，等待連線
print(f"[server] listening on {HOST}:{PORT} ...")  # 顯示監聽資訊

conn, addr = srv.accept()  # 阻塞等待一個連線；取得通訊 socket 與對端位址
print(f"[server] connected by {addr}")  # 印出客戶端位址

try:  # 進入通訊迴圈（單一客戶端範例）
    while True:  # 持續接收資料直到對端關閉
        data = conn.recv(BUFFER_SIZE)  # 從客戶端讀取最多 BUFFER_SIZE bytes
        if not data:  # 若收到空 bytes，代表對端關閉連線
            print("[server] client closed the connection.")  # 提示客戶端已斷線
            break  # 跳出迴圈
        text = data.decode(errors="replace")  # 嘗試把 bytes 轉成字串以利列印（錯誤以替代符號）
        print(f"Read Message: {text}", end="")  # 模擬 C 的 printf(不自動加換行)；原資料若含 \n 會直接顯示
        print(f"Send Message: {text.strip()}")  # 顯示即將送回的內容（去尾端換行以好看）
        conn.sendall(data)  # Echo：原封不動把收到的 bytes 回傳
finally:
    conn.close()  # 關閉與客戶端的通訊 socket
    srv.close()  # 關閉監聽 socket
    print("[server] socket closed.")  # 收尾訊息

```


### Client
```python
import socket  # 匯入 socket 以建立 TCP 連線
import sys     # 匯入 sys 以讀取標準輸入與結束程式

HOST = "127.0.0.1"       # 伺服器 IP（需與伺服器一致）
PORT = 5678              # 伺服器 TCP 埠號（需與伺服器一致）
BUFFER_SIZE = 256        # 每次接收的最大位元組數（對應 C 程式的 buf 大小）
INITIAL = b"TCP TEST\n"  # 連線成功後先送出的訊息（等同 C 範例的預設內容）

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # 建立 IPv4/TCP socket
sock.connect((HOST, PORT))  # 連線到伺服器
sock.sendall(INITIAL)  # 送出初始訊息
print(f"Send Message: {INITIAL.decode(errors='replace')}", end="")  # 顯示送出的資料（模擬 C 的 printf）
data = sock.recv(BUFFER_SIZE)  # 接收伺服器回覆
print(f"Read Message: {data.decode(errors='replace').strip()}")  # 顯示收到的資料（美化去尾端換行）

try:  # 之後把使用者每一行輸入送給伺服器並印回覆
    for line in sys.stdin:  # 從標準輸入逐行讀入（Ctrl+D/Linux、Ctrl+Z+Enter/Windows 結束）
        if not line:  # 安全檢查：若為空字串則結束
            break  # 跳出輸入迴圈
        sock.sendall(line.encode())  # 將該行轉成 bytes 後送出
        print(f"Send Message: {line}", end="")  # 顯示送出的資料（保留原本的換行行為）
        data = sock.recv(BUFFER_SIZE)  # 等待伺服器回覆
        print(f"Read Message: {data.decode(errors='replace').strip()}")  # 顯示伺服器回覆
except KeyboardInterrupt:  # 若使用者按下 Ctrl+C
    pass  # 忽略中斷，直接收尾
finally:
    print("Close connection!")  # 印出關閉提示（對應 C 範例）
    sock.close()  # 關閉 socket
    
```

## UDP

### Workflow
![image alt](https://hackmd.io/_uploads/Bkof0X7Myx.png)

### Server
```python=
import socket  # 匯入標準庫 socket

HOST = "127.0.0.1"  # 伺服器綁定的 IP（本機）
PORT = 5678         # 伺服器綁定的 UDP 埠號
BUFFER_SIZE = 256   # 每次接收的最大位元組數（對應原 C 的 buf 大小）

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # 建立 IPv4/UDP socket（SOCK_DGRAM）
sock.bind((HOST, PORT))                                   # 綁定位址與埠（UDP 無需 listen/accept）
print(f"[udp server] listening on {HOST}:{PORT} ...")     # 顯示監聽資訊

try:                                                      # 進入服務迴圈，直到鍵盤中斷
    while True:                                           # 持續處理收到的每個資料報
        data, addr = sock.recvfrom(BUFFER_SIZE)           # 接收一個 UDP 資料報，取得資料與來源位址
        if data == b"":                                   # 處理零長度資料報（合法但無內容）
            print(f"Read Message: <empty> from {addr}")   # 印出收到空訊息
            sock.sendto(data, addr)                       # 回送空封包（維持 echo 行為）
            continue                                      # 繼續等待下一個封包
        text = data.decode(errors="replace").rstrip("\n") # 嘗試把 bytes 轉字串以便列印（錯誤以替代符號）
        print(f"Read Message: {text} from {addr}")        # 顯示收到的內容與來源
        sock.sendto(data, addr)                           # 將同一批 bytes 回送給來源（UDP echo）
        print(f"Send Message: {text} to {addr}")          # 顯示已回送的內容與目標
except KeyboardInterrupt:                                 # 捕捉 Ctrl+C 中斷
    pass                                                  # 忽略並進入收尾
finally:                                                  # 確保離開前關閉 socket
    sock.close()                                          # 關閉 UDP socket
    print("[udp server] socket closed.")                  # 顯示關閉訊息

```

### Client
```python=
import socket  # 匯入 socket 以建立 UDP 通訊
import sys     # 匯入 sys 以讀取標準輸入

HOST = "127.0.0.1"       # 伺服器 IP（需與伺服器端一致）
PORT = 5678              # 伺服器 UDP 埠號（需與伺服器端一致）
BUFFER_SIZE = 256        # 每次接收的最大位元組數
INITIAL = b"UDP TEST\n"  # 啟動後先送出的測試訊息（bytes）

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)   # 建立 IPv4/UDP socket（不需 connect）
target = (HOST, PORT)                                     # 伺服器的目標位址 tuple

sock.sendto(INITIAL, target)                              # 傳送初始資料報到伺服器
print(f"Send Message: {INITIAL.decode(errors='replace')}", end="")  # 顯示送出的資料（保留換行）
data, addr = sock.recvfrom(BUFFER_SIZE)                   # 接收伺服器回覆（來源可能是任意位址，正常應回同一台）
print(f"Read Message: {data.decode(errors='replace').strip()}")     # 顯示收到的資料（去尾端換行）

try:                                                      # 接著把使用者每一行輸入送給伺服器，並印回覆
    for line in sys.stdin:                                # 從標準輸入逐行讀入（Ctrl+D/Linux、Ctrl+Z+Enter/Win 結束）
        if not line:                                      # 安全檢查：空字串表示 EOF
            break                                         # 結束輸入迴圈
        sock.sendto(line.encode(), target)                # 將該行編碼為 bytes 後以 UDP 傳送
        print(f"Send Message: {line}", end="")            # 顯示送出的資料（保留使用者輸入的換行）
        data, addr = sock.recvfrom(BUFFER_SIZE)           # 等待伺服器的 echo 回覆
        print(f"Read Message: {data.decode(errors='replace').strip()}")  # 顯示伺服器回覆
except KeyboardInterrupt:                                 # 使用者 Ctrl+C 中斷
    pass                                                  # 忽略例外，往下收尾
finally:                                                  # 收尾動作
    print("Close connection!")                            # 顯示關閉訊息（UDP 無真正連線，僅代表結束互動）
    sock.close()                                          # 關閉 UDP socket

```
## TCP與UDP程式碼差異
![螢幕擷取畫面 2025-10-20 145204](https://hackmd.io/_uploads/B1dEhLmRge.png)

# Assignment
## Task
請使用 **socket programming** 寫作一個應用（eg: 聊天室）


## Grading Policy
實作（90%）

:::success
**Baseline（80分）**：Basic TCP or UDP application like sample codes  
（Single client connection with **blocking mode**）
:::

```
加分項目

+10 Multi-client connections

+10 Multi-process or multi-thread

+10 GUI

+5 Message split  
假設你的buffer size有限，你要如何完整的傳送超過buffer size的message?

+5 Use both UDP & TCP and Explain why?  
在應用中同時使用到TCP&UDP的連線模式，並解釋在你的應用中為何要這樣設計(有必要性嗎)、優點是什麼

+5 Message encryption & decryption  
實現訊息加密與解密，socket僅會傳輸訊息，我們如何確保訊息安全? 請解釋你的設計理念與加解密方式

+5 Time out handling  
如果有多個用戶未使用close或是閒置在連線中，我們該如何避免資源被耗盡，請設計一個能夠處理超時連線的功能

+5 Disconnection handling & Auto Reconnection handling  
如果client的連線中斷了，我們要如何自動重新連線而不是重新啟動整個client

+5 Multi Port Listing（Different Port for Different Method）  
對於不同的連線需求(比方說傳送訊息、廣播、影音串流等)我們能否提供不同的port來處理不同的功能或是連線需求?

+5 Nonblocking and explain why  
你的系統是否能做到Nonblocking，在處理多用戶或龐大的message時，其他功能會不會被卡住?

+20 P2P  
挑戰實現基於P2P的分散式下載
```
報告（10%）

請使用 LLM 來提升你寫的 code 的品質（例如：可讀性、結構優化、去耦合、可維護性、錯誤處理…），並撰寫成一份簡易的報告，報告內容至少需包含以下議題：

- 說明 prompt 的設計與使用的大型語言模型
- 優化後的程式碼與原本程式碼的比較（可針對不同優化方向探討）
- 評估 LLM 的有效性與局限性
- 探討除了提升程式碼品質外，在這份作業中還可以如何應用 LLM

## 繳交及Demo注意事項

- 期限：11/14 23:59前
- 地點：B324-1 (行動寬頻網路實驗室)
- Demo時間：10分鐘
- 如果無法順利執行程式，會扣實作分數5分
- 如果時間無法配合請主動聯繫TA(三位都寄)，約其他時間Demo
- 繳交方式：請將你自己寫的程式碼、LLM 優化後的程式碼，以及撰寫的報告（pdf）打包成一個 .zip 檔上傳至 eeclass