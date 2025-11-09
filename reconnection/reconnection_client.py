"""
安全聊天室客戶端 (加密版)
支援命令列和 GUI 介面
"""

from __future__ import annotations

import argparse
import socket
import threading
import queue
import sys
import time
import struct
import json
import base64
from datetime import datetime
from contextlib import suppress
from pathlib import Path

# 添加父目錄到路徑
sys.path.append(str(Path(__file__).parent.parent))

from reconnection.crypto_utils import CryptoManager

HOST = "127.0.0.1"
TCP_PORT = 6678
UDP_PORT = 6679
ENCODING = "utf-8"
BUFFER_SIZE = 4096

# 自動重新連線設定
MAX_RECONNECT_ATTEMPTS = 5  # 最大重連次數
RECONNECT_DELAY = 3  # 重連延遲 (秒)

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
except ModuleNotFoundError:
    tk = None
    scrolledtext = None
    messagebox = None


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
        
        if length > 10 * 1024 * 1024:
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
            return crypto.decrypt_message(packet['data'], check_replay=False)
        elif packet['type'] == 'plain':
            return packet['data']
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"解密錯誤: {e}")
        return None


def perform_key_exchange(sock: socket.socket, nickname: str) -> CryptoManager | None:
    """
    執行金鑰交換
    
    流程:
    1. 接收伺服器 RSA 公鑰
    2. 生成 AES 會話金鑰
    3. 用 RSA 公鑰加密 AES 金鑰
    4. 發送給伺服器
    """
    try:
        print("🔑 開始金鑰交換...")
        
        # 1. 接收伺服器公鑰
        response = recv_message(sock)
        if not response:
            print("❌ 未收到伺服器公鑰")
            return None
        
        print(f"📥 收到回應: {response[:100]}...")
        
        packet = json.loads(response)
        if packet.get('type') != 'plain':
            print(f"❌ 無效的封包類型: {packet.get('type')}")
            return None
        
        data = json.loads(packet['data'])
        if data.get('action') != 'public_key':
            print(f"❌ 無效的動作: {data.get('action')}")
            return None
        
        server_public_key_pem = data['key']
        server_public_key = CryptoManager.import_public_key(server_public_key_pem)
        print("✅ 成功匯入伺服器公鑰")
        
        # 2. 生成 AES 會話金鑰
        crypto = CryptoManager()
        crypto.generate_aes_key()
        print("🔐 AES-256 會話金鑰已生成")
        
        # 3. 用 RSA 公鑰加密 AES 金鑰
        aes_key_bundle = crypto.get_aes_key_bundle()
        encrypted_aes_key = crypto.encrypt_with_rsa(aes_key_bundle, server_public_key)
        print(f"🔒 AES 金鑰已加密 ({len(encrypted_aes_key)} bytes)")
        
        # 4. 發送加密的 AES 金鑰
        packet = {
            'type': 'plain',
            'data': json.dumps({
                'action': 'aes_key',
                'encrypted_key': base64.b64encode(encrypted_aes_key).decode()
            })
        }
        send_message(sock, json.dumps(packet))
        print("📤 已發送加密的 AES 金鑰")
        
        # 5. 確認金鑰交換成功
        response = recv_message(sock)
        if not response:
            print("❌ 未收到確認回應")
            return None
        
        print(f"📥 收到確認: {response[:100]}...")
        
        data = json.loads(response)
        if data.get('type') == 'plain':
            confirm = json.loads(data['data'])
            if confirm.get('action') == 'key_exchange_ok':
                print("✅ 金鑰交換成功")
                
                # 6. 發送暱稱 (加密)
                send_encrypted(sock, nickname, crypto)
                print(f"📤 已發送暱稱: {nickname}")
                
                return crypto
            else:
                print(f"❌ 無效的確認動作: {confirm.get('action')}")
        else:
            print(f"❌ 無效的確認類型: {data.get('type')}")
        
        return None
        
    except Exception as e:
        print(f"❌ 金鑰交換失敗: {e}")
        import traceback
        traceback.print_exc()
        return None


def tcp_receiver_loop(sock: socket.socket, crypto: CryptoManager, 
                     stop_event: threading.Event, on_message) -> None:
    """TCP 接收執行緒"""
    while not stop_event.is_set():
        try:
            message = recv_encrypted(sock, crypto)
        except OSError:
            break
        
        if not message:
            stop_event.set()
            on_message("[system] 🔒 加密連線已關閉\n")
            break
        
        on_message(message + "\n" if not message.endswith("\n") else message)


def udp_receiver_loop(udp_sock: socket.socket, stop_event: threading.Event, on_udp_message) -> None:
    """UDP 接收執行緒"""
    udp_sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            data, addr = udp_sock.recvfrom(BUFFER_SIZE)
            message = data.decode(ENCODING, errors='ignore')
            on_udp_message(message)
        except socket.timeout:
            continue
        except OSError:
            break


def heartbeat_loop(udp_sock: socket.socket, server_addr: tuple[str, int],
                  nickname: str, stop_event: threading.Event) -> None:
    """心跳執行緒"""
    while not stop_event.is_set():
        try:
            udp_sock.sendto(f"HEARTBEAT|{nickname}".encode(ENCODING), server_addr)
            time.sleep(5)
        except OSError:
            break


def connect_to_server(host: str, tcp_port: int, udp_port: int, nickname: str) -> tuple:
    """
    連線到伺服器
    返回: (tcp_sock, udp_sock, crypto, server_udp_addr) 或 (None, None, None, None)
    """
    try:
        # 建立 TCP 連線
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.connect((host, tcp_port))
        
        # 執行金鑰交換
        crypto = perform_key_exchange(tcp_sock, nickname)
        if not crypto:
            print("❌ 金鑰交換失敗")
            tcp_sock.close()
            return None, None, None, None
        
        # 接收歡迎訊息
        welcome = recv_encrypted(tcp_sock, crypto)
        if welcome:
            print(welcome)
        
        # 建立 UDP socket
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_udp_addr = (host, udp_port)
        udp_sock.sendto(f"REGISTER|{nickname}".encode(ENCODING), server_udp_addr)
        
        return tcp_sock, udp_sock, crypto, server_udp_addr
        
    except OSError as exc:
        print(f"❌ 連線失敗: {exc}")
        return None, None, None, None


def console_client(host: str, tcp_port: int, udp_port: int, nickname: str) -> None:
    """命令列客戶端 (支援自動重新連線)"""
    stop_event = threading.Event()
    reconnect_count = 0
    
    while not stop_event.is_set() and reconnect_count <= MAX_RECONNECT_ATTEMPTS:
        if reconnect_count > 0:
            print(f"\n🔄 嘗試重新連線... ({reconnect_count}/{MAX_RECONNECT_ATTEMPTS})")
            time.sleep(RECONNECT_DELAY)
        
        # 連線到伺服器
        tcp_sock, udp_sock, crypto, server_udp_addr = connect_to_server(
            host, tcp_port, udp_port, nickname
        )
        
        if not tcp_sock:
            reconnect_count += 1
            continue
        
        # 重置重連計數器 (成功連線)
        reconnect_count = 0
        print("\n✅ 連線成功!")
        
        try:
            connection_lost = threading.Event()
        
            def display(text: str) -> None:
                print(text, end="")
                if "[system] 🔒 加密連線已關閉" in text:
                    connection_lost.set()
            
            def display_udp(text: str) -> None:
                if text.startswith("TYPING|"):
                    parts = text.split('|', 1)
                    if len(parts) == 2:
                        sys.stdout.write(f"\r💬 {parts[1]} 正在輸入...{' '*30}")
                        sys.stdout.flush()
                        threading.Timer(2.0, lambda: sys.stdout.write("\r" + " "*60 + "\r")).start()
                elif text == "ACK" or text == "REGISTERED":
                    pass
            
            # 啟動執行緒
            threading.Thread(target=tcp_receiver_loop, 
                            args=(tcp_sock, crypto, stop_event, display), 
                            daemon=True).start()
            threading.Thread(target=udp_receiver_loop, 
                            args=(udp_sock, stop_event, display_udp), 
                            daemon=True).start()
            threading.Thread(target=heartbeat_loop, 
                            args=(udp_sock, server_udp_addr, nickname, stop_event), 
                            daemon=True).start()
            
            print("\n" + "="*60)
            print("🔒 安全連線已建立")
            print("="*60)
            print("指令: /quit=離開 | /users=查看在線用戶")
            print("💡 連線中斷會自動重新連線\n")
            
            last_typing_time = 0
            user_quit = False
            
            while not stop_event.is_set() and not connection_lost.is_set():
                try:
                    message = input()
                except (EOFError, KeyboardInterrupt):
                    print()
                    user_quit = True
                    break
                
                if message.strip() == "":
                    continue
                
                if message == "/quit":
                    user_quit = True
                    send_encrypted(tcp_sock, "/quit", crypto)
                    break
                
                current_time = time.time()
                if current_time - last_typing_time > 2:
                    last_typing_time = current_time
                    with suppress(OSError):
                        udp_sock.sendto(f"TYPING|{nickname}".encode(ENCODING), server_udp_addr)
                
                try:
                    send_encrypted(tcp_sock, message, crypto)
                except OSError:
                    print("\n⚠️ 訊息發送失敗，連線可能已中斷")
                    connection_lost.set()
                    break
            
            # 清理當前連線
            stop_event.set()
            if tcp_sock:
                with suppress(OSError):
                    tcp_sock.shutdown(socket.SHUT_RDWR)
                tcp_sock.close()
            if udp_sock:
                udp_sock.close()
            
            # 判斷是否需要重新連線
            if user_quit:
                print("👋 再見!")
                return
            
            if connection_lost.is_set():
                print("\n⚠️ 連線中斷")
                reconnect_count += 1
                stop_event.clear()  # 重置 stop_event 以便重新連線
        
        except OSError as exc:
            print(f"❌ 連線錯誤: {exc}")
            reconnect_count += 1
    
    if reconnect_count > MAX_RECONNECT_ATTEMPTS:
        print(f"\n❌ 已達最大重連次數 ({MAX_RECONNECT_ATTEMPTS})，放棄重新連線")
    else:
        print("\n👋 再見!")


class SecureChatGUI:
    def __init__(self, host: str, tcp_port: int, udp_port: int, nickname: str) -> None:
        if tk is None:
            raise RuntimeError("Tkinter is not available.")
        
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.nickname = nickname
        self.tcp_sock: socket.socket | None = None
        self.udp_sock: socket.socket | None = None
        self.crypto: CryptoManager | None = None
        self.stop_event = threading.Event()
        self.tcp_queue: queue.Queue[str] = queue.Queue()
        self.udp_queue: queue.Queue[str] = queue.Queue()
        
        # 重新連線相關
        self.reconnect_count = 0
        self.is_reconnecting = False
        self.user_quit = False
        self.connection_id = 0  # 連線 ID,每次連線遞增
        self.kicked_by_server = False  # 標記是否被伺服器踢出 (閒置逾時等)
        
        # 建立 GUI
        self.root = tk.Tk()
        self.root.title(f"🔒 安全聊天室 - {nickname}")
        self.root.geometry("500x700")
        self.root.configure(bg="#f5f6fa")
        
        # 頂部資訊列 (漸層效果 - 深藍到淺藍)
        info_frame = tk.Frame(self.root, bg="#3498db", height=60)
        info_frame.pack(fill=tk.X, side=tk.TOP)
        info_frame.pack_propagate(False)
        
        # 左側：暱稱和連線圖示
        left_info = tk.Frame(info_frame, bg="#3498db")
        left_info.pack(side=tk.LEFT, padx=20, pady=10)
        
        title_label = tk.Label(left_info, text=f"� {nickname}", 
                              bg="#3498db", fg="white", font=("Arial", 14, "bold"))
        title_label.pack(anchor="w")
        
        subtitle_label = tk.Label(left_info, text="加密聊天室", 
                                 bg="#3498db", fg="#ecf0f1", font=("Arial", 9))
        subtitle_label.pack(anchor="w")
        
        # 右側：狀態指示器
        self.status_frame = tk.Frame(info_frame, bg="#3498db")
        self.status_frame.pack(side=tk.RIGHT, padx=20, pady=10)
        
        self.status_label = tk.Label(self.status_frame, text="🟢 已連線", 
                                     bg="#3498db", fg="#2ecc71", font=("Arial", 11, "bold"))
        self.status_label.pack(anchor="e")
        
        encryption_label = tk.Label(self.status_frame, text="🔐 AES-256 加密", 
                                    bg="#3498db", fg="#ecf0f1", font=("Arial", 9))
        encryption_label.pack(anchor="e")
        
        # 加密說明列 (更現代的設計)
        explain_frame = tk.Frame(self.root, bg="#2c3e50", height=40)
        explain_frame.pack(fill=tk.X)
        explain_frame.pack_propagate(False)
        
        explain_text = "🔐 端到端加密  |  🔑 RSA-2048  |  🛡️ HMAC-SHA256  |  ⚡ 自動重新連線"
        explain_label = tk.Label(explain_frame, text=explain_text,
                                bg="#2c3e50", fg="#bdc3c7", font=("Arial", 9))
        explain_label.pack(pady=10)
        
        # 主聊天區域容器
        main_container = tk.Frame(self.root, bg="#f5f6fa")
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        
        # 聊天區域
        chat_frame = tk.Frame(main_container, bg="#ffffff", relief=tk.SOLID, borderwidth=1)
        chat_frame.pack(fill=tk.BOTH, expand=True)
        
        # 正在輸入狀態列
        self.typing_frame = tk.Frame(chat_frame, bg="#e8f4f8", height=30, relief=tk.FLAT)
        self.typing_frame.pack(fill=tk.X, pady=(0, 0))
        self.typing_frame.pack_propagate(False)
        
        self.typing_label = tk.Label(
            self.typing_frame, 
            text="",
            bg="#e8f4f8",
            fg="#34495e",
            font=("Arial", 9, "italic"),
            anchor="w",
            padx=15
        )
        self.typing_label.pack(fill=tk.BOTH, expand=True)
        self.typing_users = set()
        self.typing_timers = {}
        
        # 分隔線
        separator = tk.Frame(chat_frame, bg="#bdc3c7", height=1)
        separator.pack(fill=tk.X)
        
        # 聊天文字區域
        self.text = scrolledtext.ScrolledText(
            chat_frame, state=tk.DISABLED, wrap=tk.WORD,
            bg="#ffffff", font=("Consolas", 10), relief=tk.FLAT, 
            padx=15, pady=10, borderwidth=0, highlightthickness=0
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        
        # 標籤樣式 (更多彩的顏色)
        self.text.tag_config("timestamp", foreground="#95a5a6", font=("Arial", 8))
        self.text.tag_config("system", foreground="#7f8c8d", font=("Arial", 9, "italic"))
        self.text.tag_config("system_success", foreground="#27ae60", font=("Arial", 9, "bold"))
        self.text.tag_config("system_error", foreground="#e74c3c", font=("Arial", 9, "bold"))
        self.text.tag_config("system_warning", foreground="#f39c12", font=("Arial", 9, "bold"))
        self.text.tag_config("system_info", foreground="#3498db", font=("Arial", 9, "bold"))
        self.text.tag_config("self", foreground="#2980b9", font=("Consolas", 10, "bold"))
        self.text.tag_config("other", foreground="#8e44ad", font=("Consolas", 10, "bold"))
        self.text.tag_config("message", foreground="#2c3e50", font=("Consolas", 10))
        
        # 輸入區域
        input_container = tk.Frame(self.root, bg="#f5f6fa")
        input_container.pack(fill=tk.X, padx=15, pady=(0, 15))
        
        input_frame = tk.Frame(input_container, bg="#ffffff", relief=tk.SOLID, borderwidth=1)
        input_frame.pack(fill=tk.X)
        
        # 輸入框
        entry_container = tk.Frame(input_frame, bg="#ffffff")
        entry_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
        
        self.entry = tk.Text(entry_container, height=2, wrap=tk.WORD, 
                            font=("Consolas", 10), relief=tk.FLAT,
                            bg="#ffffff", fg="#2c3e50", insertbackground="#3498db")
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.entry.bind("<Return>", self.on_send)
        self.entry.bind("<Shift-Return>", self.on_newline)
        self.entry.bind("<KeyRelease>", self.on_typing)
        self.entry.focus()
        
        # 按鈕區域
        button_container = tk.Frame(input_frame, bg="#ffffff")
        button_container.pack(side=tk.RIGHT, padx=10, pady=8)
        
        # 發送按鈕 (藍色)
        send_button = tk.Button(
            button_container, text="📤 發送", command=self.on_send,
            bg="#3498db", fg="white", font=("Arial", 10, "bold"),
            width=10, relief=tk.FLAT, cursor="hand2",
            activebackground="#2980b9", activeforeground="white",
            padx=12, pady=8
        )
        send_button.pack()
        
        # 提示文字
        hint_label = tk.Label(input_container, text="💡 Enter 發送 | Shift+Enter 換行",
                             bg="#f5f6fa", fg="#7f8c8d", font=("Arial", 8))
        hint_label.pack(pady=(5, 0))
        
        self.last_typing_time = 0
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def start(self) -> None:
        """啟動並連線"""
        self.connect_to_server()
        self.root.after(100, self.process_queues)
        self.root.mainloop()
    
    def connect_to_server(self) -> None:
        """連線到伺服器"""
        try:
            if self.is_reconnecting:
                self.append_text(f"[system] 🔄 正在重新連線... ({self.reconnect_count}/{MAX_RECONNECT_ATTEMPTS})\n")
                # 停止舊的執行緒
                self.stop_event.set()
                time.sleep(0.2)  # 給執行緒時間停止
                # 創建新的 stop_event 給新連線使用
                self.stop_event = threading.Event()
            
            # 遞增連線 ID
            self.connection_id += 1
            
            # 建立 TCP 連線
            self.tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_sock.connect((self.host, self.tcp_port))
            
            # 執行金鑰交換
            self.crypto = perform_key_exchange(self.tcp_sock, self.nickname)
            if not self.crypto:
                raise ConnectionError("金鑰交換失敗")
            
            # 接收歡迎訊息
            welcome = recv_encrypted(self.tcp_sock, self.crypto)
            if welcome:
                self.tcp_queue.put(welcome + "\n")
            
            # 建立 UDP socket
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_udp_addr = (self.host, self.udp_port)
            self.udp_sock.sendto(f"REGISTER|{self.nickname}".encode(ENCODING), server_udp_addr)
            
            # 重置狀態並顯示連線成功訊息
            was_reconnecting = self.is_reconnecting
            self.reconnect_count = 0
            self.is_reconnecting = False
            self.kicked_by_server = False  # 重置被踢標記
            
            # 更新狀態標籤
            self.status_label.config(text="🟢 已連線", fg="#2ecc71")
            
            if was_reconnecting:
                self.append_text("[system] ✅ 重新連線成功!\n")
            
            # 啟動執行緒
            threading.Thread(target=self.tcp_receiver_wrapper,
                            daemon=True).start()
            threading.Thread(target=udp_receiver_loop,
                            args=(self.udp_sock, self.stop_event, self.udp_queue.put),
                            daemon=True).start()
            threading.Thread(target=heartbeat_loop,
                            args=(self.udp_sock, server_udp_addr, self.nickname, self.stop_event),
                            daemon=True).start()
            
        except OSError as exc:
            error_msg = str(exc)
            if self.is_reconnecting:
                self.append_text(f"[system] ❌ 重新連線失敗: {error_msg}\n")
                self.attempt_reconnect()
            else:
                if messagebox:
                    messagebox.showerror("連線失敗", error_msg)
                self.root.destroy()
    
    def tcp_receiver_wrapper(self) -> None:
        """TCP 接收執行緒包裝器 (監測連線中斷)"""
        # 保存當前連線的 ID
        my_connection_id = self.connection_id
        current_sock = self.tcp_sock
        current_crypto = self.crypto
        current_stop_event = self.stop_event
        
        def on_message(msg: str) -> None:
            self.tcp_queue.put(msg)
            
            # 檢查是否為閒置逾時警告或被踢出的訊息
            if "超過 1 分鐘 將自動斷線" in msg or "你已被伺服器踢出" in msg or "閒置時間過長" in msg:
                self.kicked_by_server = True
            
            if "[system] 🔒 加密連線已關閉" in msg:
                # 只有當前活躍連線的執行緒才能觸發中斷處理
                if my_connection_id == self.connection_id and not self.is_reconnecting:
                    self.on_connection_lost()
        
        tcp_receiver_loop(current_sock, current_crypto, current_stop_event, on_message)
    
    def on_connection_lost(self) -> None:
        """連線中斷處理"""
        if self.user_quit or self.is_reconnecting:
            return
        
        # 檢查是否被伺服器踢出 (閒置逾時等)
        if self.kicked_by_server:
            self.append_text("[system] 🚫 因閒置時間過長而被伺服器斷線，請重新啟動程式\n")
            self.status_label.config(text="🔴 已斷線", fg="#e74c3c")
            # 不進行自動重新連線
            return
        
        # 立即設置重新連線標記，防止重複觸發
        self.is_reconnecting = True
        
        # 更新狀態標籤
        self.status_label.config(text="🟡 重新連線中...", fg="#f39c12")
        
        self.append_text("[system] ⚠️ 連線中斷\n")
        self.stop_event.set()
        
        # 清理當前連線
        if self.tcp_sock:
            with suppress(OSError):
                self.tcp_sock.close()
        if self.udp_sock:
            with suppress(OSError):
                self.udp_sock.close()
        
        self.attempt_reconnect()
    
    def attempt_reconnect(self) -> None:
        """嘗試重新連線"""
        self.reconnect_count += 1
        
        if self.reconnect_count > MAX_RECONNECT_ATTEMPTS:
            self.is_reconnecting = False  # 重置標記
            # 更新狀態標籤
            self.status_label.config(text="🔴 連線失敗", fg="#e74c3c")
            self.append_text(f"[system] ❌ 已達最大重連次數 ({MAX_RECONNECT_ATTEMPTS})，放棄重新連線\n")
            if messagebox:
                messagebox.showerror("連線失敗", "無法重新連線到伺服器")
            return
        
        # 延遲後重新連線
        self.root.after(RECONNECT_DELAY * 1000, self.connect_to_server)
    
    def append_text(self, message: str) -> None:
        """顯示訊息 (支援彩色系統訊息和時間戳記)"""
        self.text.configure(state=tk.NORMAL)
        
        # 檢查訊息是否已經包含時間戳記 (格式: [HH:MM:SS])
        import re
        has_timestamp = re.match(r'^\[\d{2}:\d{2}:\d{2}\]', message)
        
        # 如果訊息還沒有時間戳記,就加上
        if not has_timestamp:
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        
        if message.startswith("[system]"):
            # 根據內容選擇不同顏色
            if "✅" in message or "成功" in message:
                self.text.insert(tk.END, message, "system_success")
            elif "❌" in message or "失敗" in message or "錯誤" in message:
                self.text.insert(tk.END, message, "system_error")
            elif "⚠️" in message or "警告" in message or "中斷" in message:
                self.text.insert(tk.END, message, "system_warning")
            elif "🔄" in message or "重新" in message or "🔐" in message:
                self.text.insert(tk.END, message, "system_info")
            else:
                self.text.insert(tk.END, message, "system")
        elif ":" in message and not message.startswith("🔒"):
            # 如果有時間戳記,先顯示時間戳記部分
            if has_timestamp:
                timestamp_part = message[:11]  # "[HH:MM:SS] "
                self.text.insert(tk.END, timestamp_part, "timestamp")
                message = message[11:]  # 移除時間戳記部分
            
            parts = message.split(":", 1)
            if self.nickname in parts[0]:
                self.text.insert(tk.END, parts[0] + ": ", "self")
            else:
                self.text.insert(tk.END, parts[0] + ": ", "other")
            if len(parts) > 1:
                self.text.insert(tk.END, parts[1], "message")
        else:
            self.text.insert(tk.END, message)
        
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)
    
    def process_queues(self) -> None:
        """處理訊息佇列"""
        while not self.tcp_queue.empty():
            self.append_text(self.tcp_queue.get())
        
        # 處理 UDP 訊息
        while not self.udp_queue.empty():
            udp_msg = self.udp_queue.get()
            if udp_msg.startswith("TYPING|"):
                parts = udp_msg.split('|', 1)
                if len(parts) == 2:
                    username = parts[1]
                    self.add_typing_user(username)
        
        if not self.user_quit:
            self.root.after(100, self.process_queues)
    
    def add_typing_user(self, username: str) -> None:
        """添加正在輸入的用戶"""
        # 取消之前的計時器
        if username in self.typing_timers:
            self.root.after_cancel(self.typing_timers[username])
        
        # 添加用戶到集合
        self.typing_users.add(username)
        self.update_typing_display()
        
        # 設定 3 秒後清除
        timer_id = self.root.after(3000, lambda: self.remove_typing_user(username))
        self.typing_timers[username] = timer_id
    
    def remove_typing_user(self, username: str) -> None:
        """移除正在輸入的用戶"""
        self.typing_users.discard(username)
        if username in self.typing_timers:
            del self.typing_timers[username]
        self.update_typing_display()
    
    def update_typing_display(self) -> None:
        """更新正在輸入的顯示"""
        if not self.typing_users:
            self.typing_label.config(text="")
            self.typing_frame.config(bg="#f8f9fa")
        else:
            users = sorted(self.typing_users)
            if len(users) == 1:
                text = f"💬 {users[0]} 正在輸入..."
            elif len(users) == 2:
                text = f"💬 {users[0]} 和 {users[1]} 正在輸入..."
            else:
                text = f"💬 {users[0]} 和其他 {len(users)-1} 人正在輸入..."
            
            self.typing_label.config(text=text)
            self.typing_frame.config(bg="#e8f4f8")
    
    def on_typing(self, event=None) -> None:
        """發送正在輸入狀態 (UDP)"""
        current_time = time.time()
        if current_time - self.last_typing_time > 2:  # 每 2 秒最多發送一次
            self.last_typing_time = current_time
            if self.udp_sock:
                with suppress(OSError):
                    server_addr = (self.host, self.udp_port)
                    self.udp_sock.sendto(f"TYPING|{self.nickname}".encode(ENCODING), server_addr)
    
    def on_send(self, event=None) -> None:
        """發送訊息"""
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return "break"
        if text == "/quit":
            self.on_close()
            return "break"
        
        if self.tcp_sock and self.crypto:
            try:
                send_encrypted(self.tcp_sock, text, self.crypto)
            except OSError:
                self.append_text("[system] ⚠️ 訊息發送失敗\n")
                self.on_connection_lost()
        
        # 清除自己的輸入狀態
        self.remove_typing_user(self.nickname)
        
        self.entry.delete("1.0", tk.END)
        return "break"
    
    def on_newline(self, event=None) -> None:
        """Shift+Enter 換行"""
        return None
    
    def on_close(self) -> None:
        """關閉連線"""
        self.user_quit = True
        self.stop_event.set()
        if self.tcp_sock and self.crypto:
            with suppress(OSError):
                send_encrypted(self.tcp_sock, "/quit", self.crypto)
                self.tcp_sock.shutdown(socket.SHUT_RDWR)
                self.tcp_sock.close()
        if self.udp_sock:
            self.udp_sock.close()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="安全聊天室客戶端 (加密版)")
    parser.add_argument("nickname", help="暱稱")
    parser.add_argument("--host", default=HOST, help="伺服器 IP")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP 埠號")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP 埠號")
    parser.add_argument("--gui", action="store_true", help="啟動 GUI")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.gui:
        try:
            gui = SecureChatGUI(args.host, args.tcp_port, args.udp_port, args.nickname)
        except RuntimeError as exc:
            print(f"[error] {exc}")
            return
        gui.start()
    else:
        console_client(args.host, args.tcp_port, args.udp_port, args.nickname)


if __name__ == "__main__":
    main()
