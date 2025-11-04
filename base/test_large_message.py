"""測試大訊息傳輸功能"""
import socket
import struct
import time

HOST = "127.0.0.1"
PORT = 5678
ENCODING = "utf-8"
BUFFER_SIZE = 1024

def send_message(sock: socket.socket, message: str) -> None:
    """使用長度前綴協議發送完整訊息"""
    data = message.encode(ENCODING)
    length = len(data)
    sock.sendall(struct.pack('>I', length))
    sent = 0
    while sent < length:
        chunk = data[sent:sent + BUFFER_SIZE]
        sock.sendall(chunk)
        sent += len(chunk)

def recv_message(sock: socket.socket) -> str | None:
    """使用長度前綴協議接收完整訊息"""
    try:
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        length = struct.unpack('>I', length_data)[0]
        
        data = b''
        while len(data) < length:
            remaining = length - len(data)
            chunk_size = min(BUFFER_SIZE, remaining)
            chunk = sock.recv(chunk_size)
            if not chunk:
                return None
            data += chunk
        
        return data.decode(ENCODING, errors='ignore')
    except OSError:
        return None

def test_large_message():
    """測試不同大小的訊息"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))
    
    # 接收歡迎訊息
    greeting = recv_message(sock)
    print(f"收到: {greeting.strip()}")
    
    # 發送暱稱
    nickname = "Tester"
    send_message(sock, f"{nickname}\n")
    
    # 接收歡迎訊息
    welcome = recv_message(sock)
    print(f"收到: {welcome.strip()}")
    
    # 測試不同大小的訊息
    test_cases = [
        ("小訊息", "Hello! 這是一個小訊息"),
        ("中訊息", "X" * 500 + " - 500 字元的訊息"),
        ("大訊息", "Y" * 2000 + " - 2000 字元的訊息(超過兩個 buffer)"),
        ("超大訊息", "Z" * 5000 + " - 5000 字元的超大訊息"),
        ("多行訊息", "第一行\n第二行\n第三行\n包含換行符號的訊息"),
    ]
    
    for name, message in test_cases:
        print(f"\n測試 {name} (長度: {len(message)} bytes)")
        send_message(sock, f"{message}\n")
        print(f"✓ 已發送 {len(message)} bytes")
        time.sleep(0.5)  # 給伺服器一點時間處理
    
    # 離開
    send_message(sock, "/quit\n")
    goodbye = recv_message(sock)
    if goodbye:
        print(f"\n收到: {goodbye.strip()}")
    
    sock.close()
    print("\n測試完成!")

if __name__ == "__main__":
    print("=== 大訊息傳輸測試 ===")
    print(f"Buffer Size: {BUFFER_SIZE} bytes")
    print(f"連接到 {HOST}:{PORT}\n")
    try:
        test_large_message()
    except ConnectionRefusedError:
        print("錯誤: 無法連接到伺服器。請先啟動 server.py")
    except Exception as e:
        print(f"錯誤: {e}")
