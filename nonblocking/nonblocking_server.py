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

from nonblocking.crypto_utils import CryptoManager

HOST = "0.0.0.0"
TCP_PORT = 6678  # 聊天訊息
UDP_PORT = 6679  # 狀態更新
FILE_PORT = 6680  # 檔案傳輸
ENCODING = "utf-8"
BUFFER_SIZE = 4096  # 加密後訊息會變大
FILE_CHUNK_SIZE = 8192  # 檔案傳輸分塊大小

# 閒置超時設定
IDLE_WARNING_TIMEOUT = 30  # 5 分鐘 - 發送警告
IDLE_DISCONNECT_TIMEOUT = 60  # 10 分鐘 - 自動斷線

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


def safe_register(nickname: str, conn: socket.socket, crypto: CryptoManager) -> tuple[str, bool]:
    """
    註冊客戶端
    返回: (實際使用的暱稱, 是否為重新連線)
    """
    candidate = nickname or "guest"
    current_time = time.time()
    is_reconnection = False
    
    with clients_lock:
        # 如果暱稱已存在，標記為重新連線並替換
        if candidate in clients:
            print(f"🔄 {candidate} 重新連線 (將替換舊連線)")
            # 不要關閉舊 socket，讓舊執行緒自己結束
            # 只需要替換記錄即可
            is_reconnection = True
        
        clients[candidate] = {
            'tcp_conn': conn,
            'crypto': crypto,
            'udp_addr': None,
            'last_heartbeat': current_time,
            'last_activity': current_time,
            'idle_warned': False
        }
    
    return candidate, is_reconnection


def remove_client(nickname: str, reason: str = "正常斷線") -> None:
    """移除客戶端"""
    with clients_lock:
        info = clients.pop(nickname, None)
    if info and info['tcp_conn']:
        with suppress(OSError):
            info['tcp_conn'].close()
        print(f"🔓 [TCP] {nickname} 已斷開連線 ({reason})")


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
        nickname, is_reconnection = safe_register(nickname, conn, client_crypto)
        registered = True
        
        # 檢查是否已被新連線替換
        def is_current_connection():
            with clients_lock:
                return nickname in clients and clients[nickname]['tcp_conn'] == conn
        
        # 如果已被替換，靜默退出
        if not is_current_connection():
            print(f"🔄 {nickname} 的舊連線被新連線替換，舊執行緒退出")
            return
        
        # 3. 發送歡迎訊息 (加密)
        welcome = f"🔒 歡迎 {nickname}! 連線已加密 (AES-256 + HMAC-SHA256)"
        send_encrypted(conn, welcome, client_crypto)
        
        # 4. 廣播加入訊息 (重新連線時不廣播，避免重複)
        if not is_reconnection:
            broadcast_encrypted(f"[system] 🔐 {nickname} 已加入聊天室 (加密連線)")
            print(f"🔒 [TCP] {nickname} 已建立加密連線 from {address}")
        else:
            print(f"🔄 [TCP] {nickname} 已重新連線 from {address}")
        
        # 5. 主訊息迴圈
        while True:
            # 檢查是否已被替換
            if not is_current_connection():
                print(f"🔄 {nickname} 的連線已被替換，舊執行緒退出")
                registered = False  # 不要在 finally 中清理
                break
            
            message = recv_encrypted(conn, client_crypto)
            if not message:
                break
            
            # 更新活動時間
            with clients_lock:
                if nickname in clients and clients[nickname]['tcp_conn'] == conn:
                    clients[nickname]['last_activity'] = time.time()
                    clients[nickname]['idle_warned'] = False
            
            message = message.rstrip("\r\n")
            
            if message == "/quit":
                with suppress(OSError, ValueError):
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
            remove_client(nickname, reason="客戶端斷線")
            broadcast_encrypted(f"[system] {nickname} 已離開聊天室")
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
                # 更新心跳時間 (UDP 心跳不算作活動時間，只用於連線檢測)
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
            remove_client(nick, reason="心跳超時")
            broadcast_encrypted(f"[system] {nick} 連線超時")


def format_time(seconds: int) -> str:
    """格式化時間顯示 (秒或分鐘)"""
    if seconds < 60:
        return f"{seconds} 秒"
    else:
        minutes = seconds // 60
        return f"{minutes} 分鐘"


def check_idle_timeout() -> None:
    """
    檢查閒置超時
    - 5 分鐘無活動: 發送警告
    - 10 分鐘無活動: 自動斷線
    """
    while True:
        time.sleep(30)  # 每 30 秒檢查一次
        current_time = time.time()
        
        with clients_lock:
            # 複製客戶端列表以避免在迭代時修改
            clients_snapshot = [(nick, info.copy()) for nick, info in clients.items()]
        
        for nickname, info in clients_snapshot:
            idle_time = current_time - info['last_activity']
            
            # 10 分鐘無活動 - 自動斷線
            if idle_time >= IDLE_DISCONNECT_TIMEOUT:
                time_str = format_time(IDLE_DISCONNECT_TIMEOUT)
                print(f"💤 {nickname} 閒置超過 {time_str}，自動斷線")
                
                # 發送最後通知
                with suppress(OSError, ValueError):
                    send_encrypted(
                        info['tcp_conn'],
                        f"[system] ⏰ 你已閒置 {time_str}，連線已被關閉",
                        info['crypto']
                    )
                
                remove_client(nickname, reason=f"閒置超時 ({time_str})")
                broadcast_encrypted(f"[system] {nickname} 因閒置過久已離開聊天室")
            
            # 5 分鐘無活動 - 發送警告 (只發送一次)
            elif idle_time >= IDLE_WARNING_TIMEOUT and not info.get('idle_warned', False):
                warning_time_str = format_time(IDLE_WARNING_TIMEOUT)
                disconnect_time_str = format_time(IDLE_DISCONNECT_TIMEOUT)
                print(f"⚠️  {nickname} 閒置 {warning_time_str}，發送警告")
                
                with suppress(OSError, ValueError):
                    send_encrypted(
                        info['tcp_conn'],
                        f"[system] ⏰ 警告: 你已閒置 {warning_time_str}，"
                        f"超過 {disconnect_time_str} 將自動斷線",
                        info['crypto']
                    )
                
                # 標記已警告
                with clients_lock:
                    if nickname in clients:
                        clients[nickname]['idle_warned'] = True


def handle_file_transfer(conn: socket.socket, address: tuple) -> None:
    """
    處理檔案傳輸 (TCP 6680)
    這是模擬版本，用於展示多埠口架構
    """
    try:
        print(f"📁 檔案傳輸連線: {address}")
        
        # 接收檔案 metadata
        metadata_json = recv_message(conn)
        if not metadata_json:
            return
        
        metadata = json.loads(metadata_json)
        sender = metadata.get('sender')
        filename = metadata.get('filename')
        filesize = metadata.get('filesize')
        recipient = metadata.get('recipient', 'all')
        
        print(f"📤 {sender} 正在傳送檔案: {filename} ({filesize} bytes)")
        
        # 🔧 更新發送者的活動時間 (檔案傳輸也算活動)
        with clients_lock:
            if sender in clients:
                clients[sender]['last_activity'] = time.time()
                clients[sender]['idle_warned'] = False  # 重置警告狀態
        
        # 模擬接收檔案 (實際上不儲存)
        total_received = 0
        while total_received < filesize:
            chunk_size = min(FILE_CHUNK_SIZE, filesize - total_received)
            chunk = conn.recv(chunk_size)
            if not chunk:
                break
            total_received += len(chunk)
            
            # 回報進度
            progress = int((total_received / filesize) * 100)
            if progress % 20 == 0:  # 每 20% 回報一次
                print(f"   進度: {progress}%")
        
        if total_received == filesize:
            print(f"✅ 檔案接收完成: {filename}")
            
            # 🔧 再次更新活動時間 (檔案傳輸完成)
            with clients_lock:
                if sender in clients:
                    clients[sender]['last_activity'] = time.time()
            
            # 廣播檔案傳輸通知
            if recipient == 'all':
                broadcast_encrypted(
                    f"[system] 📁 {sender} 發送了檔案: {filename} ({filesize} bytes)"
                )
                # 發送確認給發送者
                send_message(conn, json.dumps({'status': 'success', 'message': '檔案已廣播'}))
            else:
                # 通知特定接收者
                with clients_lock:
                    if recipient in clients:
                        send_encrypted(
                            clients[recipient]['tcp_conn'],
                            f"[system] 📁 {sender} 發送了檔案: {filename} ({filesize} bytes)",
                            clients[recipient]['crypto']
                        )
                send_message(conn, json.dumps({'status': 'success', 'message': f'檔案已發送給 {recipient}'}))
        else:
            print(f"❌ 檔案接收失敗: 預期 {filesize} bytes, 實際接收 {total_received} bytes")
            send_message(conn, json.dumps({'status': 'error', 'message': '檔案傳輸不完整'}))
            
    except Exception as e:
        print(f"❌ 檔案傳輸錯誤 {address}: {e}")
    finally:
        conn.close()


def file_transfer_server(host: str, file_port: int) -> None:
    """檔案傳輸服務器 (獨立埠口)"""
    file_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    file_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    file_server.bind((host, file_port))
    file_server.listen()
    
    print(f"   • TCP (檔案傳輸): {host}:{file_port}")
    
    while True:
        conn, address = file_server.accept()
        thread = threading.Thread(target=handle_file_transfer, args=(conn, address), daemon=True)
        thread.start()


def serve_forever(host: str, tcp_port: int, udp_port: int, file_port: int = FILE_PORT) -> None:
    """啟動安全聊天室伺服器"""
    
    # 生成伺服器 RSA 金鑰對
    print("\n" + "="*70)
    print("🔐 安全聊天室伺服器 (多埠口架構)")
    print("="*70)
    server_crypto.generate_rsa_keypair()
    
    # TCP Socket (聊天)
    tcp_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_server.bind((host, tcp_port))
    tcp_server.listen()
    
    # UDP Socket (狀態)
    udp_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_server.bind((host, udp_port))
    
    print(f"\n🔒 加密方案:")
    print(f"   • RSA-2048: 金鑰交換")
    print(f"   • AES-256-CBC: 訊息加密")
    print(f"   • HMAC-SHA256: 完整性驗證")
    print(f"   • Timestamp + Nonce: 防重放攻擊")
    
    print(f"\n⏰ 超時設定:")
    print(f"   • 閒置警告: {format_time(IDLE_WARNING_TIMEOUT)}")
    print(f"   • 閒置斷線: {format_time(IDLE_DISCONNECT_TIMEOUT)}")
    print(f"   • 心跳超時: 30 秒")
    
    print(f"\n📡 監聽端口:")
    print(f"   • TCP (加密聊天): {host}:{tcp_port}")
    print(f"   • UDP (狀態更新): {host}:{udp_port}")
    print(f"   • TCP (檔案傳輸): {host}:{file_port}")
    print("="*70 + "\n")
    
    # 啟動檔案傳輸服務器 (獨立執行緒)
    file_thread = threading.Thread(target=file_transfer_server, args=(host, file_port), daemon=True)
    file_thread.start()
    
    # 啟動 UDP 執行緒
    udp_thread = threading.Thread(target=handle_udp_messages, args=(udp_server,), daemon=True)
    udp_thread.start()
    
    # 啟動心跳檢查執行緒
    heartbeat_thread = threading.Thread(target=check_heartbeats, daemon=True)
    heartbeat_thread.start()
    
    # 啟動閒置超時檢查執行緒
    idle_thread = threading.Thread(target=check_idle_timeout, daemon=True)
    idle_thread.start()
    
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
    parser = argparse.ArgumentParser(description="安全聊天室伺服器 (多埠口架構)")
    parser.add_argument("--host", default=HOST, help="綁定的 IP")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP 埠號 (聊天)")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP 埠號 (狀態)")
    parser.add_argument("--file-port", type=int, default=FILE_PORT, help="TCP 埠號 (檔案)")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    serve_forever(args.host, args.tcp_port, args.udp_port, args.file_port)
