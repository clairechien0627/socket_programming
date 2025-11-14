"""
P2P 客戶端 (分散式下載 + 安全聊天室)

每個客戶端既是 Client 也是 Server,支援命令列和 GUI 介面

功能:
1. 檔案分片與分享 (64KB chunks)
2. 從多個 Peer 並行下載
3. SHA256 完整性驗證
4. 自動成為 Seeder
5. GUI 介面整合
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
import os
import hashlib
from datetime import datetime
from contextlib import suppress
from pathlib import Path

# 添加父目錄到路徑
sys.path.append(str(Path(__file__).parent.parent))

from p2p.crypto_utils import CryptoManager

HOST = "127.0.0.1"
TCP_PORT = 6678  # 聊天訊息
UDP_PORT = 6679  # 狀態更新
P2P_PORT = 6681  # P2P 協調
ENCODING = "utf-8"
BUFFER_SIZE = 4096
P2P_CHUNK_SIZE = 64 * 1024  # P2P 片段大小: 64KB
FILE_CHUNK_SIZE = 8192  # 檔案傳輸分塊大小

# 自動重新連線設定
MAX_RECONNECT_ATTEMPTS = 5  # 最大重連次數
RECONNECT_DELAY = 3  # 重連延遲 (秒)

# P2P 設定
P2P_PORT_RANGE = (7001, 7100)  # Peer 監聽 port 範圍
DOWNLOADS_DIR = Path(__file__).parent / "downloads"

# 確保下載目錄存在
DOWNLOADS_DIR.mkdir(exist_ok=True)

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
    def __init__(self, host: str, tcp_port: int, udp_port: int, nickname: str, p2p_client: P2PClient) -> None:
        if tk is None:
            raise RuntimeError("Tkinter is not available.")
        
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.nickname = nickname
        self.p2p_client = p2p_client  # P2P 客戶端
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
        
        # P2P 相關
        self.shared_files = []  # 本地分享的檔案
        self.search_results = []  # 搜尋結果
        
        # 建立 GUI
        self.root = tk.Tk()
        self.root.title(f"🔒 P2P 聊天室 - {nickname}")
        self.root.geometry("700x700")
        self.root.configure(bg="#f5f6fa")
        
        # 頂部資訊列 (漸層效果 - 深藍到淺藍)
        info_frame = tk.Frame(self.root, bg="#3498db", height=60)
        info_frame.pack(fill=tk.X, side=tk.TOP)
        info_frame.pack_propagate(False)
        
        # 左側：暱稱和連線圖示
        left_info = tk.Frame(info_frame, bg="#3498db")
        left_info.pack(side=tk.LEFT, padx=20, pady=10)
        
        title_label = tk.Label(left_info, text=f"{nickname}", 
                              bg="#3498db", fg="white", font=("Arial", 14, "bold"))
        title_label.pack(anchor="w")
        
        subtitle_label = tk.Label(left_info, text=f"P2P: 127.0.0.1:{p2p_client.p2p_port}", 
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
        explain_frame = tk.Frame(self.root, bg="#2c3e50", height=20)
        explain_frame.pack(fill=tk.X)
        explain_frame.pack_propagate(False)
        
        explain_text = "🔐 端到端加密  |  🔑 RSA-2048  |  🛡️ HMAC-SHA256  |  ⚡ 自動重新連線"
        explain_label = tk.Label(explain_frame, text=explain_text,
                                bg="#2c3e50", fg="#bdc3c7", font=("Arial", 9))
        explain_label.pack(pady=10)
        
        # 主容器 (左側 P2P + 右側聊天)
        main_container = tk.Frame(self.root, bg="#f5f6fa")
        main_container.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)
        
        # ==================== 左側 P2P 面板 ====================
        p2p_panel = tk.Frame(main_container, bg="#ffffff", relief=tk.SOLID, borderwidth=1, width=250)
        p2p_panel.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 10))
        p2p_panel.pack_propagate(False)
        
        # ==================== 右側聊天面板 ====================
        chat_panel = tk.Frame(main_container, bg="#ffffff", relief=tk.SOLID, borderwidth=1)
        chat_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # P2P 標題
        p2p_title = tk.Label(p2p_panel, text="📁 P2P 檔案下載", 
                            bg="#2c3e50", fg="white", font=("Arial", 11, "bold"), 
                            pady=8)
        p2p_title.pack(fill=tk.X)
        
        # 說明文字
        info_frame = tk.Frame(p2p_panel, bg="#e8f4f8", pady=8)
        info_frame.pack(fill=tk.X, padx=10, pady=(10, 0))
        
        # 已分享檔案列表（只顯示，不能新增）
        shared_frame = tk.Frame(p2p_panel, bg="#ffffff", pady=10)
        shared_frame.pack(fill=tk.X, padx=10)
        
        shared_label = tk.Label(shared_frame, text="📤 我分享的檔案:", 
                               bg="#ffffff", fg="#2c3e50", font=("Arial", 9, "bold"))
        shared_label.pack(anchor="w")
        
        self.shared_listbox = tk.Listbox(
            shared_frame, height=3, font=("Consolas", 8),
            bg="#ecf0f1", relief=tk.FLAT, borderwidth=0
        )
        self.shared_listbox.pack(fill=tk.X, pady=(5, 0))
        
        # 分隔線
        tk.Frame(p2p_panel, bg="#bdc3c7", height=1).pack(fill=tk.X, pady=10)
        
        # 搜尋區域
        search_frame = tk.Frame(p2p_panel, bg="#ffffff", pady=10)
        search_frame.pack(fill=tk.X, padx=10)
        
        search_label = tk.Label(search_frame, text="🔍 搜尋檔案", 
                               bg="#ffffff", fg="#2c3e50", font=("Arial", 10, "bold"))
        search_label.pack(anchor="w")
        
        search_input_frame = tk.Frame(search_frame, bg="#ffffff")
        search_input_frame.pack(fill=tk.X, pady=(5, 0))
        
        self.search_entry = tk.Entry(
            search_input_frame, font=("Arial", 9),
            bg="#ecf0f1", relief=tk.FLAT, borderwidth=5
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind("<Return>", lambda e: self.on_search_files())
        
        search_btn = tk.Button(
            search_input_frame, text="🔍", command=self.on_search_files,
            bg="#3498db", fg="white", font=("Arial", 9, "bold"),
            relief=tk.FLAT, cursor="hand2", width=3
        )
        search_btn.pack(side=tk.RIGHT, padx=(5, 0))
        
        # 搜尋結果
        result_label = tk.Label(search_frame, text="搜尋結果:", 
                               bg="#ffffff", fg="#7f8c8d", font=("Arial", 8))
        result_label.pack(anchor="w", pady=(10, 2))
        
        # 搜尋結果列表框架
        result_container = tk.Frame(search_frame, bg="#ecf0f1")
        result_container.pack(fill=tk.BOTH, expand=True)
        
        result_scrollbar = tk.Scrollbar(result_container)
        result_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.result_listbox = tk.Listbox(
            result_container, font=("Consolas", 8),
            bg="#ecf0f1", relief=tk.FLAT, borderwidth=0,
            yscrollcommand=result_scrollbar.set
        )
        self.result_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        result_scrollbar.config(command=self.result_listbox.yview)
        
        # 創建 tooltip label 用於顯示完整檔名
        self.tooltip_label = tk.Label(
            self.root, text="", bg="#34495e", fg="white", 
            font=("Arial", 9), relief=tk.SOLID, borderwidth=1,
            padx=8, pady=6, justify=tk.LEFT, anchor="w"
        )
        self.tooltip_label.place_forget()  # 初始隱藏
        
        # 綁定滑鼠事件到搜尋結果列表
        self.result_listbox.bind("<Motion>", self.on_result_mouse_move)
        self.result_listbox.bind("<Leave>", self.on_result_mouse_leave)
        
        # 下載按鈕
        download_btn = tk.Button(
            search_frame, text="📥 下載選取檔案", command=self.on_download_file,
            bg="#e67e22", fg="white", font=("Arial", 9, "bold"),
            relief=tk.FLAT, cursor="hand2", pady=5
        )
        download_btn.pack(fill=tk.X, pady=(5, 0))
        
        # 下載進度
        self.progress_label = tk.Label(
            search_frame, text="", 
            bg="#ffffff", fg="#27ae60", font=("Arial", 8, "bold")
        )
        self.progress_label.pack(anchor="w", pady=(5, 0))
        
        # ==================== 右側聊天區域 ====================
        # 聊天區域
        chat_frame = tk.Frame(chat_panel, bg="#ffffff")
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
        send_button.pack(side=tk.LEFT, padx=(0, 5))
        
        # 檔案傳輸按鈕 (綠色)
        file_button = tk.Button(
            button_container, text="📎 傳檔", command=self.on_send_file,
            bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
            width=10, relief=tk.FLAT, cursor="hand2",
            activebackground="#229954", activeforeground="white",
            padx=12, pady=8
        )
        file_button.pack(side=tk.LEFT)
        
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
    
    def on_send_file(self) -> None:
        """發送檔案 (分享到 P2P + 聊天室通知)"""
        try:
            from tkinter import filedialog
            
            # 選擇檔案
            filepath = filedialog.askopenfilename(
                title="選擇要分享的檔案",
                filetypes=[
                    ("所有檔案", "*.*"),
                    ("文字檔案", "*.txt"),
                    ("PDF檔案", "*.pdf"),
                    ("圖片檔案", "*.png *.jpg *.jpeg *.gif"),
                    ("壓縮檔案", "*.zip *.rar *.7z")
                ]
            )
            
            if not filepath:
                return
            
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
            
            # 格式化檔案大小
            if filesize < 1024:
                size_str = f"{filesize} B"
            elif filesize < 1024 * 1024:
                size_str = f"{filesize / 1024:.1f} KB"
            else:
                size_str = f"{filesize / (1024 * 1024):.1f} MB"
            
            # 顯示處理中
            self.append_text(f"[system] 📤 正在分享檔案: {filename} ({size_str})...\n")
            
            # 在背景執行緒中分享檔案
            def share_thread():
                try:
                    # 1. 分享到 P2P 網路
                    success = self.p2p_client.share_file(filepath)
                    
                    if success:
                        # 2. 更新 UI
                        self.root.after(0, self.append_text, 
                                       f"[system] ✅ 檔案已分享到 P2P 網路: {filename}\n")
                        self.root.after(0, self.update_shared_files)
                        
                        # 3. 在聊天室發送通知 ⭐ 關鍵功能！
                        notification = f"📁 我分享了檔案: {filename} ({size_str})"
                        
                        if self.tcp_sock and self.crypto:
                            try:
                                # 發送加密通知到聊天室
                                send_encrypted(self.tcp_sock, notification, self.crypto)
                                self.root.after(0, self.append_text, 
                                              f"[system] 💬 已通知聊天室有新檔案\n")
                            except OSError as e:
                                self.root.after(0, self.append_text, 
                                              f"[system] ⚠️ 無法發送聊天通知: {e}\n")
                        else:
                            self.root.after(0, self.append_text, 
                                          f"[system] ⚠️ 未連接到聊天室，無法發送通知\n")
                    else:
                        self.root.after(0, self.append_text, 
                                       f"[system] ❌ 檔案分享失敗\n")
                
                except Exception as e:
                    self.root.after(0, self.append_text, 
                                   f"[system] ❌ 分享錯誤: {e}\n")
            
            threading.Thread(target=share_thread, daemon=True).start()
            
        except Exception as e:
            self.append_text(f"[system] ❌ 錯誤: {e}\n")
    
    def update_shared_files(self) -> None:
        """更新共享檔案列表"""
        self.shared_listbox.delete(0, tk.END)
        for filename in self.p2p_client.peer_server.shared_files.keys():
            self.shared_listbox.insert(tk.END, f"✅ {filename}")
    
    def on_search_files(self) -> None:
        """搜尋檔案 (空輸入則顯示所有檔案)"""
        query = self.search_entry.get().strip()
        
        # 空查詢時搜尋所有檔案
        if not query:
            self.append_text(f"[system] 🔍 搜尋所有檔案...\n")
        else:
            self.append_text(f"[system] 🔍 搜尋: {query}...\n")
        
        # 在背景執行緒中搜尋
        def search_thread():
            # 空查詢傳空字串給 server，server 會返回所有檔案
            results = self.p2p_client.search_files(query if query else "")
            self.search_results = results
            
            self.root.after(0, self.update_search_results, results)
        
        threading.Thread(target=search_thread, daemon=True).start()
    
    def update_search_results(self, results: list[dict]) -> None:
        """更新搜尋結果"""
        self.result_listbox.delete(0, tk.END)

        if not results:
            self.append_text(f"[system] ℹ️  未找到檔案\n")
            self.result_listbox.insert(tk.END, "  (未找到檔案)")
            return

        self.append_text(f"[system] ✅ 找到 {len(results)} 個檔案\n")

        for i, result in enumerate(results):
            filename = result['filename']
            filesize = result['filesize']
            peers = result['peers']
            peer_names = ', '.join(p['nickname'] for p in peers)

            size_kb = filesize / 1024
            if size_kb < 1024:
                size_str = f"{size_kb:.1f} KB"
            else:
                size_str = f"{size_kb/1024:.1f} MB"

            # 顯示格式：檔名 (大小) - 擁有者
            display_text = f"{i+1}. {filename} ({size_str}) - {peer_names}"
            # 懸停時顯示完整資訊 (分行顯示)
            tooltip_text = f"{filename} ({size_str})\n由 {peer_names} 提供"

            self.result_listbox.insert(tk.END, display_text)
            # 將完整資訊存儲在項目中，用於懸停顯示
            self.result_listbox.tooltip_data = getattr(self.result_listbox, 'tooltip_data', {})
            self.result_listbox.tooltip_data[i] = tooltip_text
    
    def on_download_file(self) -> None:
        """下載選取的檔案"""
        selection = self.result_listbox.curselection()
        if not selection:
            self.append_text("[system] ⚠️  請先選擇要下載的檔案\n")
            return
        
        index = selection[0]
        if index >= len(self.search_results):
            return
        
        file_info = self.search_results[index]
        filename = file_info['filename']
        
        self.append_text(f"[system] 📥 開始下載: {filename}...\n")
        self.progress_label.config(text="📊 下載中...")
        
        # 在背景執行緒中下載
        def download_thread():
            downloaded_chunks = [0]
            total_chunks = file_info['chunks']
            
            def progress_callback(chunk_id):
                downloaded_chunks[0] += 1
                progress = int((downloaded_chunks[0] / total_chunks) * 100)
                self.root.after(0, self.progress_label.config, 
                              {'text': f"📊 {progress}% ({downloaded_chunks[0]}/{total_chunks})"})
            
            def log_callback(message):
                self.root.after(0, self.append_text, f"[system] {message}\n")
            
            success = self.p2p_client.download_file(filename, file_info, progress_callback, log_callback)
            
            if success:
                self.root.after(0, self.progress_label.config, {'text': "✅ 下載完成!"})
                self.root.after(0, self.update_shared_files)
            else:
                self.root.after(0, self.append_text, f"[system] ❌ 下載失敗\n")
                self.root.after(0, self.progress_label.config, {'text': "❌ 下載失敗"})
            
            # 3 秒後清除進度
            self.root.after(3000, self.progress_label.config, {'text': ""})
        
        threading.Thread(target=download_thread, daemon=True).start()
    
    def on_result_mouse_move(self, event):
        """滑鼠在搜尋結果上移動時顯示完整資訊"""
        try:
            # 獲取滑鼠位置對應的項目索引
            index = self.result_listbox.nearest(event.y)

            if index >= 0 and index < self.result_listbox.size():
                # 檢查是否有存儲的完整資訊
                tooltip_data = getattr(self.result_listbox, 'tooltip_data', {})
                if index in tooltip_data:
                    tooltip_text = tooltip_data[index]

                    # 檢查是否需要顯示 tooltip (文字是否被截斷)
                    listbox_width = self.result_listbox.winfo_width()
                    item_text = self.result_listbox.get(index)
                    text_width = len(item_text) * 8  # 粗略估計字元寬度

                    if text_width > listbox_width - 20:  # 如果文字寬度超過 Listbox 寬度
                        # 顯示 tooltip - 放在項目右側，與項目對齊
                        self.tooltip_label.config(text=tooltip_text)

                        # 計算 tooltip 位置 (貼近項目文字右側)
                        # 使用更準確的項目高度計算
                        font_height = 16  # 估計字體高度
                        item_padding = 4  # 估計 padding
                        item_height = font_height + item_padding
                        item_y = self.result_listbox.winfo_rooty() + (index * item_height)  # 稍微往上一點
                        
                        # 計算項目文字的實際寬度
                        item_text = self.result_listbox.get(index)
                        text_width = len(item_text) * 7  # 更精確的字元寬度估計
                        
                        x = self.result_listbox.winfo_rootx() + min(text_width - 5, self.result_listbox.winfo_width() - 80)  # 更往左一點
                        y = item_y  # 與項目文字基線對齊

                        # 確保 tooltip 不超出螢幕邊界
                        screen_width = self.root.winfo_screenwidth()
                        screen_height = self.root.winfo_screenheight()
                        tooltip_width = len(tooltip_text.split('\n')[0]) * 8 + 20  # 使用第一行計算寬度
                        tooltip_height = 40  # 估計高度

                        if x + tooltip_width > screen_width:
                            x = self.result_listbox.winfo_rootx() - tooltip_width - 5  # 放在左側
                        
                        if y + tooltip_height > screen_height:
                            y = screen_height - tooltip_height - 10

                        self.tooltip_label.place(x=x, y=y)
                        return
                
            # 如果不需要顯示 tooltip，隱藏它
            self.tooltip_label.place_forget()
            
        except Exception:
            self.tooltip_label.place_forget()
    
    def on_result_mouse_leave(self, event):
        """滑鼠離開搜尋結果時隱藏 tooltip"""
        self.tooltip_label.place_forget()
    
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


# ============================================================================
# P2P 功能模組
# ============================================================================

def split_file_into_chunks(filepath: str) -> tuple[list[dict], str]:
    """
    將檔案切成多個片段
    返回: (chunks, file_hash)
    """
    chunks = []
    file_data = b''
    
    with open(filepath, 'rb') as f:
        chunk_id = 0
        while True:
            data = f.read(P2P_CHUNK_SIZE)
            if not data:
                break
            file_data += data
            chunks.append({
                'id': chunk_id,
                'data': data,
                'hash': hashlib.sha256(data).hexdigest()
            })
            chunk_id += 1
    
    file_hash = hashlib.sha256(file_data).hexdigest()
    return chunks, file_hash


def merge_chunks_to_file(chunks: list[bytes], filepath: str) -> bool:
    """
    將片段合併成檔案
    """
    try:
        with open(filepath, 'wb') as f:
            for chunk in chunks:
                f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ 合併檔案失敗: {e}")
        return False


def find_available_port(start: int, end: int) -> int | None:
    """
    尋找可用的 port
    """
    for port in range(start, end + 1):
        try:
            test_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.bind(('0.0.0.0', port))
            test_sock.close()
            return port
        except OSError:
            continue
    return None


class PeerServer:
    """
    Peer Server: 提供檔案片段給其他 Peer
    """
    def __init__(self, port: int):
        self.port = port
        self.shared_files = {}  # {filename: {'chunks': [bytes], 'hash': str}}
        self.server_sock: socket.socket | None = None
        self.running = False
    
    def add_file(self, filename: str, chunks: list[dict], file_hash: str):
        """添加要分享的檔案"""
        self.shared_files[filename] = {
            'chunks': [chunk['data'] for chunk in chunks],
            'hash': file_hash
        }
        print(f"📤 [P2P Server] 開始分享: {filename} ({len(chunks)} 片段)")
    
    def handle_peer_request(self, conn: socket.socket, addr: tuple):
        """處理其他 Peer 的請求"""
        try:
            request_json = recv_message(conn)
            if not request_json:
                return
            
            request = json.loads(request_json)
            action = request.get('action')
            
            if action == 'request_chunks':
                filename = request['filename']
                chunk_ids = request['chunk_ids']
                
                if filename not in self.shared_files:
                    response = {'status': 'error', 'message': '檔案不存在'}
                    send_message(conn, json.dumps(response))
                    return
                
                chunks = self.shared_files[filename]['chunks']
                
                # 發送片段
                for chunk_id in chunk_ids:
                    if chunk_id < len(chunks):
                        chunk_data = chunks[chunk_id]
                        chunk_hash = hashlib.sha256(chunk_data).hexdigest()
                        
                        response = {
                            'status': 'success',
                            'chunk_id': chunk_id,
                            'data': base64.b64encode(chunk_data).decode(),
                            'hash': chunk_hash
                        }
                        send_message(conn, json.dumps(response))
                    else:
                        response = {'status': 'error', 'message': f'片段 {chunk_id} 不存在'}
                        send_message(conn, json.dumps(response))
                
                print(f"📤 [P2P] 發送片段 {chunk_ids} 給 {addr}")
        
        except Exception as e:
            print(f"❌ [P2P Server] 錯誤: {e}")
        finally:
            conn.close()
    
    def serve_forever(self):
        """啟動 Peer Server"""
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind(('0.0.0.0', self.port))
        self.server_sock.listen(5)
        self.running = True
        
        print(f"🌐 [P2P Server] 監聽 port {self.port}")
        
        while self.running:
            try:
                conn, addr = self.server_sock.accept()
                thread = threading.Thread(
                    target=self.handle_peer_request,
                    args=(conn, addr),
                    daemon=True
                )
                thread.start()
            except OSError:
                break
    
    def stop(self):
        """停止 Peer Server"""
        self.running = False
        if self.server_sock:
            self.server_sock.close()


class P2PClient:
    """
    P2P 客戶端
    支援分享檔案、搜尋、從多個 Peer 下載
    """
    def __init__(self, nickname: str, p2p_port: int):
        self.nickname = nickname
        self.p2p_port = p2p_port
        self.peer_server = PeerServer(p2p_port)
        
        # 啟動 Peer Server
        server_thread = threading.Thread(target=self.peer_server.serve_forever, daemon=True)
        server_thread.start()
    
    def share_file(self, filepath: str) -> bool:
        """分享檔案到 P2P 網路"""
        try:
            filename = os.path.basename(filepath)
            filesize = os.path.getsize(filepath)
            
            # 切片
            print(f"📦 切片檔案: {filename}")
            chunks, file_hash = split_file_into_chunks(filepath)
            
            # 添加到本地 Peer Server
            self.peer_server.add_file(filename, chunks, file_hash)
            
            # 通知 Tracker
            tracker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tracker_sock.connect((HOST, P2P_PORT))
            
            request = {
                'action': 'share_file',
                'nickname': self.nickname,
                'filename': filename,
                'filesize': filesize,
                'chunks': len(chunks),
                'hash': file_hash,
                'peer_ip': HOST,
                'peer_port': self.p2p_port
            }
            send_message(tracker_sock, json.dumps(request))
            
            response_json = recv_message(tracker_sock)
            tracker_sock.close()
            
            if response_json:
                response = json.loads(response_json)
                if response['status'] == 'success':
                    print(f"✅ 檔案已分享: {filename} ({len(chunks)} 片段)")
                    return True
            
            return False
            
        except Exception as e:
            print(f"❌ 分享檔案失敗: {e}")
            return False
    
    def search_files(self, query: str) -> list[dict]:
        """搜尋檔案"""
        try:
            tracker_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tracker_sock.connect((HOST, P2P_PORT))
            
            request = {
                'action': 'search',
                'query': query
            }
            send_message(tracker_sock, json.dumps(request))
            
            response_json = recv_message(tracker_sock)
            tracker_sock.close()
            
            if response_json:
                response = json.loads(response_json)
                if response['status'] == 'success':
                    return response['results']
            
            return []
            
        except Exception as e:
            print(f"❌ 搜尋失敗: {e}")
            return []
    
    def download_from_peer(self, peer: dict, filename: str, chunk_ids: list[int],
                          result_chunks: dict, progress_callback=None, log_callback=None) -> None:
        """從單個 Peer 下載指定片段"""
        try:
            peer_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            peer_sock.connect((peer['ip'], peer['port']))

            request = {
                'action': 'request_chunks',
                'filename': filename,
                'chunk_ids': chunk_ids
            }
            send_message(peer_sock, json.dumps(request))

            # 接收片段
            downloaded_count = 0
            for chunk_id in chunk_ids:
                response_json = recv_message(peer_sock)
                if not response_json:
                    break

                response = json.loads(response_json)
                if response['status'] == 'success':
                    chunk_data = base64.b64decode(response['data'])
                    chunk_hash = response['hash']

                    # 驗證完整性
                    if hashlib.sha256(chunk_data).hexdigest() == chunk_hash:
                        result_chunks[chunk_id] = chunk_data
                        downloaded_count += 1
                        # 顯示詳細的下載進度
                        if log_callback:
                            log_callback(f"📥 [{peer['nickname']}] 片段 {chunk_id} 下載成功 ({downloaded_count}/{len(chunk_ids)})")
                        else:
                            print(f"📥 [{peer['nickname']}] 片段 {chunk_id} 下載成功 ({downloaded_count}/{len(chunk_ids)})")
                        if progress_callback:
                            progress_callback(chunk_id)
                    else:
                        if log_callback:
                            log_callback(f"⚠️  [{peer['nickname']}] 片段 {chunk_id} 雜湊值不匹配")
                        else:
                            print(f"⚠️  [{peer['nickname']}] 片段 {chunk_id} 雜湊值不匹配")
                else:
                    if log_callback:
                        log_callback(f"❌ [{peer['nickname']}] 片段 {chunk_id} 下載失敗: {response.get('message', '未知錯誤')}")
                    else:
                        print(f"❌ [{peer['nickname']}] 片段 {chunk_id} 下載失敗: {response.get('message', '未知錯誤')}")

            peer_sock.close()

            # 總結從這個peer的下載結果
            success_rate = downloaded_count / len(chunk_ids) * 100
            if log_callback:
                log_callback(f"✅ [{peer['nickname']}] 下載完成: {downloaded_count}/{len(chunk_ids)} 片段 ({success_rate:.1f}%)")
            else:
                print(f"✅ [{peer['nickname']}] 下載完成: {downloaded_count}/{len(chunk_ids)} 片段 ({success_rate:.1f}%)")

        except Exception as e:
            if log_callback:
                log_callback(f"❌ 從 {peer['nickname']} 下載失敗: {e}")
            else:
                print(f"❌ 從 {peer['nickname']} 下載失敗: {e}")
    
    def download_file(self, filename: str, file_info: dict, progress_callback=None, log_callback=None) -> bool:
        """從多個 Peer 下載檔案"""
        try:
            total_chunks = file_info['chunks']
            peers = file_info['peers']
            
            if not peers:
                if log_callback:
                    log_callback("❌ 沒有可用的 Seeder")
                else:
                    print("❌ 沒有可用的 Seeder")
                return False
            
            if log_callback:
                log_callback(f"📥 開始下載: {filename}")
                log_callback(f"   • 片段數: {total_chunks}")
                log_callback(f"   • 來源數: {len(peers)}")
                log_callback(f"   • 可用 Peers: {', '.join([p['nickname'] for p in peers])}")
            else:
                print(f"📥 開始下載: {filename}")
                print(f"   • 片段數: {total_chunks}")
                print(f"   • 來源數: {len(peers)}")
                print(f"   • 可用 Peers: {', '.join([p['nickname'] for p in peers])}")
                print()

            # 分配片段給不同的 Peer
            chunks_per_peer = total_chunks // len(peers)
            result_chunks = {}
            threads = []

            for i, peer in enumerate(peers):
                start_chunk = i * chunks_per_peer
                end_chunk = start_chunk + chunks_per_peer if i < len(peers) - 1 else total_chunks
                chunk_ids = list(range(start_chunk, end_chunk))

                if log_callback:
                    log_callback(f"🔄 分配給 [{peer['nickname']}]: 片段 {start_chunk}-{end_chunk-1} ({len(chunk_ids)} 個片段)")
                else:
                    print(f"🔄 分配給 [{peer['nickname']}]: 片段 {start_chunk}-{end_chunk-1} ({len(chunk_ids)} 個片段)")

                thread = threading.Thread(
                    target=self.download_from_peer,
                    args=(peer, filename, chunk_ids, result_chunks, progress_callback, log_callback),
                    daemon=True
                )
                threads.append(thread)
                thread.start()

            if log_callback:
                log_callback(f"\n⏳ 等待所有 Peer 下載完成...")
            else:
                print(f"\n⏳ 等待所有 Peer 下載完成...")
            # 等待所有下載完成
            for thread in threads:
                thread.join()

            if not log_callback:
                print()
            
            # 檢查是否所有片段都下載成功
            if len(result_chunks) == total_chunks:
                # 合併片段
                if log_callback:
                    log_callback("📦 合併片段...")
                else:
                    print("📦 合併片段...")
                ordered_chunks = [result_chunks[i] for i in range(total_chunks)]
                output_path = DOWNLOADS_DIR / filename
                
                if merge_chunks_to_file(ordered_chunks, str(output_path)):
                    if log_callback:
                        log_callback(f"✅ 下載完成: downloads/{filename}")
                    else:
                        print(f"✅ 下載完成: {output_path}")
                    
                    # 添加到本地 Peer Server (成為 Seeder)
                    chunks_with_hash = [
                        {
                            'id': i,
                            'data': chunk,
                            'hash': hashlib.sha256(chunk).hexdigest()
                        }
                        for i, chunk in enumerate(ordered_chunks)
                    ]
                    self.peer_server.add_file(filename, chunks_with_hash, file_info['hash'])
                    
                    # 通知 Tracker 我也有這個檔案了
                    self.share_file(str(output_path))
                    
                    return True
                else:
                    if log_callback:
                        log_callback("❌ 合併片段失敗")
                    else:
                        print("❌ 合併片段失敗")
                    return False
            else:
                if log_callback:
                    log_callback(f"❌ 下載不完整: {len(result_chunks)}/{total_chunks} 片段")
                else:
                    print(f"❌ 下載不完整: {len(result_chunks)}/{total_chunks} 片段")
                return False
                
        except Exception as e:
            if log_callback:
                log_callback(f"❌ 下載失敗: {e}")
            else:
                print(f"❌ 下載失敗: {e}")
            return False
# ============================================================================
# 命令列參數解析和主程式
# ============================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P2P 客戶端 (分散式下載 + 安全聊天室)")
    parser.add_argument("nickname", help="暱稱")
    parser.add_argument("--host", default=HOST, help="伺服器 IP")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP 埠號")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP 埠號")
    parser.add_argument("--p2p-port", type=int, default=7001, help="P2P 監聽 port")
    parser.add_argument("--gui", action="store_true", help="啟動 GUI")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    # 尋找可用的 P2P port
    p2p_port = find_available_port(*P2P_PORT_RANGE)
    if not p2p_port:
        print("❌ 找不到可用的 P2P port")
        return
    
    if args.p2p_port != 7001:
        p2p_port = args.p2p_port
    
    # 建立 P2P 客戶端
    p2p_client = P2PClient(args.nickname, p2p_port)
    
    print(f"✅ P2P 客戶端已啟動: {args.nickname} (Port {p2p_port})")
    
    if args.gui:
        print("\n💬 啟動 P2P GUI 聊天室...")
        try:
            gui = SecureChatGUI(args.host, args.tcp_port, args.udp_port, args.nickname, p2p_client)
        except RuntimeError as exc:
            print(f"[error] {exc}")
            return
        gui.start()
    else:
        print("指令: share <filepath> | search <query> | download <filename> | chat | quit")
        # 命令列模式：P2P + 聊天室
        while True:
            try:
                cmd = input("\n> ").strip()
                if not cmd:
                    continue
                
                parts = cmd.split(maxsplit=1)
                action = parts[0]
                
                if action == "share" and len(parts) == 2:
                    filepath = parts[1]
                    p2p_client.share_file(filepath)
                
                elif action == "search" and len(parts) == 2:
                    query = parts[1]
                    results = p2p_client.search_files(query)
                    print(f"\n🔍 找到 {len(results)} 個結果:")
                    for i, result in enumerate(results, 1):
                        print(f"   {i}. {result['filename']} ({result['filesize']} bytes)")
                        print(f"      來源: {', '.join(p['nickname'] for p in result['peers'])}")
                
                elif action == "download" and len(parts) == 2:
                    filename = parts[1]
                    results = p2p_client.search_files(filename)
                    if results:
                        file_info = results[0]
                        p2p_client.download_file(filename, file_info)
                    else:
                        print("❌ 找不到檔案")
                
                elif action == "chat":
                    print("💬 進入聊天室模式...")
                    console_client(args.host, args.tcp_port, args.udp_port, args.nickname)
                
                elif action == "quit":
                    break
                
                else:
                    print("❌ 無效指令")
                    print("可用指令: share <filepath> | search <query> | download <filename> | chat | quit")
            
            except KeyboardInterrupt:
                break
        
        print("\n👋 再見!")


if __name__ == "__main__":
    main()
