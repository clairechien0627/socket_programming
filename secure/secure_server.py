"""
安全聊天室伺服器 (加密版)
使用混合加密 (RSA + AES) 保護訊息傳輸
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import struct
import json
import base64
from contextlib import suppress
from datetime import datetime
from pathlib import Path
import sys

# 添加父目錄到路徑
sys.path.append(str(Path(__file__).parent.parent))

from secure.crypto_utils import CryptoManager

HOST = "0.0.0.0"
TCP_PORT = 6678  # 使用不同的埠號
UDP_PORT = 6679
ENCODING = "utf-8"
BUFFER_SIZE = 4096  # 加密後訊息會變大

# 客戶端資料
clients = {}  # {nickname: {'tcp_conn': socket, 'crypto': CryptoManager, ...}}
clients_lock = threading.Lock()

# 伺服器加密管理器
server_crypto = CryptoManager()


def send_message(sock: socket.socket, message: str) -> None:
    """TCP: 使用長度前綴協議發送訊息"""
    data = message.encode(ENCODING)
    length = len(data)
    sock.sendall(struct.pack('>I', length))
    sock.sendall(data)


def recv_message(sock: socket.socket) -> str | None:
    """TCP: 使用長度前綴協議接收訊息"""
    try:
        length_data = b''
        while len(length_data) < 4:
            chunk = sock.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        length = struct.unpack('>I', length_data)[0]
        
        # 防止過大的訊息
        if length > 10 * 1024 * 1024:  # 10MB 上限
            raise ValueError("訊息過大")
        
        data = b''
        while len(data) < length:
            chunk = sock.recv(min(BUFFER_SIZE, length - len(data)))
            if not chunk:
                return None
            data += chunk
        
        return data.decode(ENCODING, errors='ignore')
    except OSError:
        return None


def send_encrypted(sock: socket.socket, plaintext: str, crypto: CryptoManager) -> None:
    """發送加密訊息"""
    encrypted = crypto.encrypt_message(plaintext)
    packet = {
        'type': 'encrypted',
        'data': encrypted
    }
    send_message(sock, json.dumps(packet))


def recv_encrypted(sock: socket.socket, crypto: CryptoManager) -> str | None:
    """接收並解密訊息"""
    packet_json = recv_message(sock)
    if not packet_json:
        return None
    
    try:
        packet = json.loads(packet_json)
        if packet['type'] == 'encrypted':
            return crypto.decrypt_message(packet['data'])
        elif packet['type'] == 'plain':
            # 握手階段的明文訊息
            return packet['data']
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"解密錯誤: {e}")
        return None


def broadcast_encrypted(message: str, sender: str | None = None) -> None:
    """加密廣播訊息給所有客戶端"""
    with clients_lock:
        targets = [(nick, info['tcp_conn'], info['crypto']) 
                   for nick, info in clients.items() 
                   if nick != sender and info.get('crypto')]
    
    for nick, conn, crypto in targets:
        with suppress(OSError, ValueError):
            send_encrypted(conn, message, crypto)


def broadcast_udp(udp_sock: socket.socket, message: str, sender: str | None = None) -> None:
    """UDP 廣播訊息給所有客戶端 (除了發送者)"""
    with clients_lock:
        targets = [(info['udp_addr']) 
                   for nick, info in clients.items() 
                   if nick != sender and info.get('udp_addr')]
    
    data = message.encode(ENCODING)
    for addr in targets:
        with suppress(OSError):
            udp_sock.sendto(data, addr)


def safe_register(nickname: str, conn: socket.socket, crypto: CryptoManager) -> str:
    """註冊客戶端"""
    candidate = nickname or "guest"
    with clients_lock:
        base = candidate
        suffix = 1
        while candidate in clients:
            candidate = f"{base}_{suffix}"
            suffix += 1
        clients[candidate] = {
            'tcp_conn': conn,
            'crypto': crypto,
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


def perform_key_exchange(conn: socket.socket, address: tuple[str, int]) -> CryptoManager | None:
    """
    執行金鑰交換握手
    
    流程:
    1. 發送伺服器 RSA 公鑰
    2. 接收客戶端用 RSA 加密的 AES 金鑰
    3. 用 RSA 私鑰解密獲得 AES 金鑰
    """
    try:
        print(f"🔑 開始與 {address} 進行金鑰交換...")
        
        # 1. 發送伺服器公鑰
        public_key_pem = server_crypto.export_public_key()
        packet = {
            'type': 'plain',
            'data': json.dumps({
                'action': 'public_key',
                'key': public_key_pem
            })
        }
        send_message(conn, json.dumps(packet))
        
        # 2. 接收客戶端加密的 AES 金鑰
        response = recv_message(conn)
        if not response:
            print(f"❌ 未收到客戶端回應")
            return None
        
        # 解析外層封包
        packet = json.loads(response)
        if packet.get('type') != 'plain':
            print(f"❌ 無效的封包類型: {packet.get('type')}")
            return None
        
        # 解析內層資料
        data = json.loads(packet['data'])
        if data.get('action') != 'aes_key':
            print(f"❌ 無效的回應類型: {data.get('action')}")
            return None
        
        encrypted_aes_key = base64.b64decode(data['encrypted_key'])
        
        # 3. 解密 AES 金鑰
        aes_key_bundle = server_crypto.decrypt_with_rsa(encrypted_aes_key)
        
        # 4. 為此客戶端創建加密管理器
        client_crypto = CryptoManager()
        client_crypto.set_aes_key_bundle(aes_key_bundle)
        
        # 5. 確認金鑰交換成功
        packet = {
            'type': 'plain',
            'data': json.dumps({'action': 'key_exchange_ok'})
        }
        send_message(conn, json.dumps(packet))
        
        print(f"✅ 與 {address} 的金鑰交換成功")
        return client_crypto
        
    except Exception as e:
        print(f"❌ 金鑰交換失敗: {e}")
        import traceback
        traceback.print_exc()
        return None


def handle_tcp_client(conn: socket.socket, address: tuple[str, int]) -> None:
    """處理 TCP 客戶端連線"""
    nickname = "unknown"
    registered = False
    
    try:
        # 1. 執行金鑰交換
        client_crypto = perform_key_exchange(conn, address)
        if not client_crypto:
            print(f"❌ {address} 金鑰交換失敗,斷開連線")
            return
        
        # 2. 接收暱稱 (加密)
        nickname_msg = recv_encrypted(conn, client_crypto)
        if not nickname_msg:
            return
        
        nickname = nickname_msg.strip()
        nickname = safe_register(nickname, conn, client_crypto)
        registered = True
        
        # 3. 發送歡迎訊息 (加密)
        welcome = f"🔒 歡迎 {nickname}! 連線已加密 (AES-256 + HMAC-SHA256)"
        send_encrypted(conn, welcome, client_crypto)
        
        # 4. 廣播加入訊息
        broadcast_encrypted(f"[system] 🔐 {nickname} 已加入聊天室 (加密連線)")
        
        print(f"🔒 [TCP] {nickname} 已建立加密連線 from {address}")
        
        # 5. 主訊息迴圈
        while True:
            message = recv_encrypted(conn, client_crypto)
            if not message:
                break
            
            message = message.rstrip("\r\n")
            
            if message == "/quit":
                send_encrypted(conn, "Goodbye!", client_crypto)
                break
            elif message == "/users":
                with clients_lock:
                    users = list(clients.keys())
                user_list = f"[system] 🔒 在線用戶 ({len(users)}): {', '.join(users)} (全部加密)"
                send_encrypted(conn, user_list, client_crypto)
            else:
                # 廣播聊天訊息 (加密)
                timestamp = datetime.now().strftime("%H:%M:%S")
                full_message = f"[{timestamp}] {nickname}: {message}"
                
                # 回傳給發送者
                send_encrypted(conn, full_message, client_crypto)
                # 廣播給其他人
                broadcast_encrypted(full_message, sender=nickname)
                
                print(f"🔐 [{timestamp}] {nickname}: {message}")
    
    except ConnectionResetError:
        pass
    except ValueError as e:
        # 解密失敗或安全性錯誤
        print(f"⚠️ 安全性錯誤 from {nickname}: {e}")
        with suppress(OSError):
            error_msg = f"[system] 安全性錯誤: {str(e)}"
            send_message(conn, json.dumps({'type': 'plain', 'data': error_msg}))
    finally:
        if registered:
            remove_client(nickname)
            broadcast_encrypted(f"[system] {nickname} 已離開聊天室")
            print(f"🔓 [TCP] {nickname} 已斷開連線 {address}")
        else:
            with suppress(OSError):
                conn.close()


def handle_udp_messages(udp_sock: socket.socket) -> None:
    """處理 UDP 訊息 (心跳等狀態更新)"""
    print(f"📡 [UDP] 監聽 port {UDP_PORT} (狀態更新)...")
    
    while True:
        try:
            data, addr = udp_sock.recvfrom(BUFFER_SIZE)
            message = data.decode(ENCODING, errors='ignore').strip()
            
            if not message:
                continue
            
            parts = message.split('|', 2)
            if len(parts) < 2:
                continue
            
            command = parts[0]
            nickname = parts[1]
            
            if command == "HEARTBEAT":
                # 更新心跳時間
                with clients_lock:
                    if nickname in clients:
                        clients[nickname]['udp_addr'] = addr
                        clients[nickname]['last_heartbeat'] = time.time()
                udp_sock.sendto(b"ACK", addr)
                
            elif command == "TYPING":
                # 正在輸入狀態
                with clients_lock:
                    if nickname in clients:
                        clients[nickname]['last_heartbeat'] = time.time()
                # 廣播給其他客戶端
                broadcast_udp(udp_sock, f"TYPING|{nickname}", sender=nickname)
                
            elif command == "REGISTER":
                with clients_lock:
                    if nickname in clients:
                        clients[nickname]['udp_addr'] = addr
                udp_sock.sendto(b"REGISTERED", addr)
                
        except OSError as e:
            print(f"[UDP] 錯誤: {e}")


def check_heartbeats() -> None:
    """檢查心跳超時"""
    TIMEOUT = 30
    
    while True:
        time.sleep(10)
        current_time = time.time()
        
        with clients_lock:
            timeout_clients = [
                nick for nick, info in clients.items()
                if current_time - info['last_heartbeat'] > TIMEOUT
            ]
        
        for nick in timeout_clients:
            print(f"⏰ {nick} 心跳超時")
            remove_client(nick)
            broadcast_encrypted(f"[system] {nick} 連線超時")


def serve_forever(host: str, tcp_port: int, udp_port: int) -> None:
    """啟動安全聊天室伺服器"""
    
    # 生成伺服器 RSA 金鑰對
    print("\n" + "="*70)
    print("🔐 安全聊天室伺服器 (加密版)")
    print("="*70)
    server_crypto.generate_rsa_keypair()
    
    # TCP Socket
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((host, tcp_port))
    tcp_server.listen()
    
    # UDP Socket
    udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_server.bind((host, udp_port))
    
    print(f"\n🔒 加密方案:")
    print(f"   • RSA-2048: 金鑰交換")
    print(f"   • AES-256-CBC: 訊息加密")
    print(f"   • HMAC-SHA256: 完整性驗證")
    print(f"   • Timestamp + Nonce: 防重放攻擊")
    
    print(f"\n📡 監聽端口:")
    print(f"   • TCP (加密聊天): {host}:{tcp_port}")
    print(f"   • UDP (狀態更新): {host}:{udp_port}")
    print("="*70 + "\n")
    
    # 啟動 UDP 執行緒
    udp_thread = threading.Thread(target=handle_udp_messages, args=(udp_server,), daemon=True)
    udp_thread.start()
    
    # 啟動心跳檢查執行緒
    heartbeat_thread = threading.Thread(target=check_heartbeats, daemon=True)
    heartbeat_thread.start()
    
    try:
        while True:
            conn, address = tcp_server.accept()
            print(f"📞 新連線: {address}")
            thread = threading.Thread(target=handle_tcp_client, args=(conn, address), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        print("\n\n⏹️  正在停止伺服器...")
    finally:
        with clients_lock:
            active = list(clients.keys())
        for nickname in active:
            remove_client(nickname)
        tcp_server.close()
        udp_server.close()
        print("✅ 伺服器已停止")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全聊天室伺服器 (加密版)")
    parser.add_argument("--host", default=HOST, help="綁定的 IP")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP 埠號")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP 埠號")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    serve_forever(args.host, args.tcp_port, args.udp_port)
