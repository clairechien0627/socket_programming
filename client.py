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

