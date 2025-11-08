from __future__ import annotations

import argparse
import socket
import threading
import time
import struct
from contextlib import suppress
from datetime import datetime

HOST = "0.0.0.0"
TCP_PORT = 5678
UDP_PORT = 5679
ENCODING = "utf-8"
BUFFER_SIZE = 1024

# 客戶端資料結構
clients = {}  # {nickname: {'tcp_conn': socket, 'udp_addr': (ip, port), 'last_heartbeat': timestamp}}
clients_lock = threading.Lock()


def send_message(sock: socket.socket, message: str) -> None:
    """TCP: 使用長度前綴協議發送完整訊息"""
    data = message.encode(ENCODING)
    length = len(data)
    sock.sendall(struct.pack('>I', length))
    sent = 0
    while sent < length:
        chunk = data[sent:sent + BUFFER_SIZE]
        sock.sendall(chunk)
        sent += len(chunk)


def recv_message(sock: socket.socket) -> str | None:
    """TCP: 使用長度前綴協議接收完整訊息"""
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
            chunk = sock.recv(min(BUFFER_SIZE, length - len(data)))
            if not chunk:
                return None
            data += chunk
        
        return data.decode(ENCODING, errors='ignore')
    except OSError:
        return None


def broadcast_tcp(message: str, sender: str | None = None) -> None:
    """TCP 廣播: 發送聊天訊息給所有客戶端 (除了發送者)"""
    with clients_lock:
        targets = [(nick, info['tcp_conn']) for nick, info in clients.items() 
                   if nick != sender and info['tcp_conn']]
    
    for nick, conn in targets:
        with suppress(OSError):
            send_message(conn, message)


def broadcast_udp(udp_sock: socket.socket, message: str, sender: str | None = None) -> None:
    """UDP 廣播: 發送狀態更新給所有客戶端"""
    data = message.encode(ENCODING)
    with clients_lock:
        targets = [(nick, info['udp_addr']) for nick, info in clients.items() 
                   if nick != sender and info.get('udp_addr')]
    
    for nick, addr in targets:
        with suppress(OSError):
            udp_sock.sendto(data, addr)


def safe_register(nickname: str, tcp_conn: socket.socket) -> str:
    """註冊新客戶端"""
    candidate = nickname or "guest"
    with clients_lock:
        base = candidate
        suffix = 1
        while candidate in clients:
            candidate = f"{base}_{suffix}"
            suffix += 1
        clients[candidate] = {
            'tcp_conn': tcp_conn,
            'udp_addr': None,
            'last_heartbeat': time.time()
        }
    return candidate


def remove_client(nickname: str) -> None:
    """移除客戶端"""
    with clients_lock:
        info = clients.pop(nickname, None)
    if info and info['tcp_conn']:
        with suppress(OSError):
            info['tcp_conn'].close()


def update_udp_address(nickname: str, addr: tuple[str, int]) -> None:
    """更新客戶端的 UDP 位址"""
    with clients_lock:
        if nickname in clients:
            clients[nickname]['udp_addr'] = addr
            clients[nickname]['last_heartbeat'] = time.time()


def update_heartbeat(nickname: str) -> None:
    """更新心跳時間"""
    with clients_lock:
        if nickname in clients:
            clients[nickname]['last_heartbeat'] = time.time()


def get_online_users() -> list[str]:
    """取得在線用戶列表"""
    with clients_lock:
        return list(clients.keys())


def handle_tcp_client(conn: socket.socket, address: tuple[str, int]) -> None:
    """處理 TCP 連線 - 用於聊天訊息"""
    nickname = "unknown"
    registered = False
    
    try:
        send_message(conn, "Enter nickname: ")
        nickname = recv_message(conn)
        if not nickname:
            return
        
        nickname = safe_register(nickname.strip(), conn)
        registered = True
        
        send_message(conn, f"Welcome {nickname}! TCP port for chat, UDP port {UDP_PORT} for status.\n")
        broadcast_tcp(f"[system] {nickname} joined the chat.\n")
        
        print(f"[TCP] {nickname} connected from {address}")
        
        # 發送在線用戶列表
        users = get_online_users()
        send_message(conn, f"[system] Online users ({len(users)}): {', '.join(users)}\n")
        
        while True:
            message = recv_message(conn)
            if not message:
                break
            
            message = message.rstrip("\r\n")
            if message == "/quit":
                with suppress(OSError):
                    send_message(conn, "Goodbye!\n")
                break
            elif message == "/users":
                users = get_online_users()
                send_message(conn, f"[system] Online users ({len(users)}): {', '.join(users)}\n")
            else:
                # 廣播聊天訊息 (TCP)
                timestamp = datetime.now().strftime("%H:%M:%S")
                full_message = f"[{timestamp}] {nickname}: {message}\n"
                # 也回傳給發送者
                send_message(conn, full_message)
                # 廣播給其他人
                broadcast_tcp(full_message, sender=nickname)
                
    except ConnectionResetError:
        pass
    finally:
        if registered:
            remove_client(nickname)
            broadcast_tcp(f"[system] {nickname} left the chat.\n")
            print(f"[TCP] Disconnected: {nickname} {address}")
        else:
            with suppress(OSError):
                conn.close()


def handle_udp_messages(udp_sock: socket.socket) -> None:
    """處理 UDP 訊息 - 用於心跳和狀態更新"""
    print(f"[UDP] Listening on port {UDP_PORT} for status updates...")
    
    while True:
        try:
            data, addr = udp_sock.recvfrom(BUFFER_SIZE)
            message = data.decode(ENCODING, errors='ignore').strip()
            
            if not message:
                continue
            
            # 解析 UDP 訊息格式: "COMMAND|nickname|data"
            parts = message.split('|', 2)
            if len(parts) < 2:
                continue
            
            command = parts[0]
            nickname = parts[1]
            
            if command == "HEARTBEAT":
                # 心跳包
                update_udp_address(nickname, addr)
                # 回應心跳
                udp_sock.sendto(b"ACK", addr)
                
            elif command == "TYPING":
                # 正在輸入狀態
                update_heartbeat(nickname)
                broadcast_udp(udp_sock, f"TYPING|{nickname}", sender=nickname)
                print(f"[UDP] {nickname} is typing...")
                
            elif command == "REGISTER":
                # 註冊 UDP 位址
                update_udp_address(nickname, addr)
                udp_sock.sendto(b"REGISTERED", addr)
                print(f"[UDP] Registered {nickname} at {addr}")
                
            elif command == "PING":
                # 延遲測試
                udp_sock.sendto(f"PONG|{time.time()}".encode(ENCODING), addr)
                
        except OSError as e:
            print(f"[UDP] Error: {e}")


def check_heartbeats() -> None:
    """定期檢查心跳,移除超時客戶端"""
    TIMEOUT = 30  # 30 秒無心跳視為斷線
    
    while True:
        time.sleep(10)
        current_time = time.time()
        
        with clients_lock:
            timeout_clients = [
                nick for nick, info in clients.items()
                if current_time - info['last_heartbeat'] > TIMEOUT
            ]
        
        for nick in timeout_clients:
            print(f"[system] {nick} timed out (no heartbeat)")
            remove_client(nick)
            broadcast_tcp(f"[system] {nick} disconnected (timeout).\n")


def serve_forever(host: str, tcp_port: int, udp_port: int) -> None:
    """啟動混合 TCP/UDP 伺服器"""
    
    # 建立 TCP socket (用於聊天訊息)
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((host, tcp_port))
    tcp_server.listen()
    
    # 建立 UDP socket (用於狀態更新)
    udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_server.bind((host, udp_port))
    
    print("=" * 60)
    print("🚀 混合協議聊天室伺服器")
    print("=" * 60)
    print(f"📡 TCP (聊天訊息): {host}:{tcp_port}")
    print(f"📡 UDP (狀態更新): {host}:{udp_port}")
    print("=" * 60)
    print("\n為什麼使用混合協議?")
    print("  [TCP] 可靠傳輸 - 確保聊天訊息完整送達")
    print("  [UDP] 即時性優先 - 心跳/狀態更新允許丟包")
    print("=" * 60)
    
    # 啟動 UDP 處理執行緒
    udp_thread = threading.Thread(target=handle_udp_messages, args=(udp_server,), daemon=True)
    udp_thread.start()
    
    # 啟動心跳檢查執行緒
    heartbeat_thread = threading.Thread(target=check_heartbeats, daemon=True)
    heartbeat_thread.start()
    
    try:
        # TCP 主迴圈
        while True:
            conn, address = tcp_server.accept()
            thread = threading.Thread(target=handle_tcp_client, args=(conn, address), daemon=True)
            thread.start()
            
    except KeyboardInterrupt:
        print("\n\nStopping server...")
    finally:
        with clients_lock:
            active = list(clients.keys())
        for nickname in active:
            remove_client(nickname)
        tcp_server.close()
        udp_server.close()
        print("Server stopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid TCP/UDP chat server")
    parser.add_argument("--host", default=HOST, help="Host/IP to bind")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP port for chat")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP port for status")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    serve_forever(args.host, args.tcp_port, args.udp_port)
