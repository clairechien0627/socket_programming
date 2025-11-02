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

