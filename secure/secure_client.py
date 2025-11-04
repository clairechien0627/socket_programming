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
from contextlib import suppress
from pathlib import Path

# 添加父目錄到路徑
sys.path.append(str(Path(__file__).parent.parent))

from secure.crypto_utils import CryptoManager

HOST = "127.0.0.1"
TCP_PORT = 6678
UDP_PORT = 6679
ENCODING = "utf-8"
BUFFER_SIZE = 4096

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


def console_client(host: str, tcp_port: int, udp_port: int, nickname: str) -> None:
    """命令列客戶端"""
    tcp_sock = None
    udp_sock = None
    stop_event = threading.Event()
    
    try:
        # 建立 TCP 連線
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.connect((host, tcp_port))
        
        # 執行金鑰交換
        crypto = perform_key_exchange(tcp_sock, nickname)
        if not crypto:
            print("❌ 金鑰交換失敗")
            return
        
        # 接收歡迎訊息
        welcome = recv_encrypted(tcp_sock, crypto)
        if welcome:
            print(welcome)
        
        # 建立 UDP socket
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_udp_addr = (host, udp_port)
        udp_sock.sendto(f"REGISTER|{nickname}".encode(ENCODING), server_udp_addr)
        
        def display(text: str) -> None:
            print(text, end="")
        
        def display_udp(text: str) -> None:
            if text == "ACK" or text == "REGISTERED":
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
        print("指令: /quit=離開 | /users=查看在線用戶\n")
        
        while not stop_event.is_set():
            try:
                message = input()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            
            if message.strip() == "":
                continue
            
            if message == "/quit":
                send_encrypted(tcp_sock, "/quit", crypto)
                break
            
            send_encrypted(tcp_sock, message, crypto)
    
    except OSError as exc:
        print(f"連線失敗: {exc}")
    finally:
        stop_event.set()
        if tcp_sock:
            with suppress(OSError):
                tcp_sock.shutdown(socket.SHUT_RDWR)
            tcp_sock.close()
        if udp_sock:
            udp_sock.close()


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
        
        # 建立 GUI
        self.root = tk.Tk()
        self.root.title(f"🔒 安全聊天室 - {nickname}")
        self.root.geometry("750x600")
        
        # 頂部資訊列 (綠色 = 安全)
        info_frame = tk.Frame(self.root, bg="#27ae60", height=50)
        info_frame.pack(fill=tk.X, side=tk.TOP)
        info_frame.pack_propagate(False)
        
        title_label = tk.Label(info_frame, text=f"🔒 {nickname} (加密連線)", 
                              bg="#27ae60", fg="white", font=("Arial", 12, "bold"))
        title_label.pack(side=tk.LEFT, padx=15, pady=12)
        
        self.status_label = tk.Label(info_frame, text="● AES-256", 
                                     bg="#27ae60", fg="white", font=("Arial", 10))
        self.status_label.pack(side=tk.RIGHT, padx=15, pady=12)
        
        # 加密說明列
        explain_frame = tk.Frame(self.root, bg="#2ecc71", height=45)
        explain_frame.pack(fill=tk.X)
        explain_frame.pack_propagate(False)
        
        explain_text = "🔐 端到端加密 | RSA-2048 金鑰交換 | AES-256-CBC 訊息加密 | HMAC-SHA256 完整性驗證"
        explain_label = tk.Label(explain_frame, text=explain_text,
                                bg="#2ecc71", fg="white", font=("Arial", 9))
        explain_label.pack(pady=12)
        
        # 聊天區域
        chat_frame = tk.Frame(self.root, bg="#ecf0f1")
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))
        
        self.text = scrolledtext.ScrolledText(
            chat_frame, state=tk.DISABLED, wrap=tk.WORD,
            bg="#ffffff", font=("Arial", 10), relief=tk.FLAT, padx=10, pady=10
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        
        # 標籤樣式
        self.text.tag_config("system", foreground="#27ae60", font=("Arial", 9, "italic"))
        self.text.tag_config("self", foreground="#2980b9", font=("Arial", 10, "bold"))
        self.text.tag_config("other", foreground="#8e44ad", font=("Arial", 10, "bold"))
        self.text.tag_config("message", foreground="#2c3e50", font=("Arial", 10))
        
        # 輸入區域
        input_frame = tk.Frame(self.root, bg="#ecf0f1")
        input_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        
        self.entry = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Arial", 10))
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.entry.bind("<Return>", self.on_send)
        self.entry.bind("<Shift-Return>", self.on_newline)
        self.entry.focus()
        
        # 發送按鈕
        button_frame = tk.Frame(input_frame, bg="#ecf0f1")
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        send_button = tk.Button(
            button_frame, text="🔒發送\n(Enter)", command=self.on_send,
            bg="#27ae60", fg="white", font=("Arial", 9, "bold"),
            width=8, relief=tk.FLAT, cursor="hand2"
        )
        send_button.pack(fill=tk.BOTH, expand=True)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def start(self) -> None:
        try:
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
            
        except OSError as exc:
            if messagebox:
                messagebox.showerror("連線失敗", str(exc))
            else:
                print(f"連線失敗: {exc}")
            self.root.destroy()
            return
        
        # 啟動執行緒
        threading.Thread(target=tcp_receiver_loop,
                        args=(self.tcp_sock, self.crypto, self.stop_event, self.tcp_queue.put),
                        daemon=True).start()
        threading.Thread(target=heartbeat_loop,
                        args=(self.udp_sock, server_udp_addr, self.nickname, self.stop_event),
                        daemon=True).start()
        
        self.root.after(100, self.process_queues)
        self.root.mainloop()
    
    def append_text(self, message: str) -> None:
        """顯示訊息"""
        self.text.configure(state=tk.NORMAL)
        
        if message.startswith("[system]"):
            self.text.insert(tk.END, message, "system")
        elif ":" in message and not message.startswith("🔒"):
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
        
        if not self.stop_event.is_set():
            self.root.after(100, self.process_queues)
        else:
            if messagebox:
                messagebox.showinfo("已斷線", "安全連線已關閉.")
            self.on_close()
    
    def on_send(self, event=None) -> None:
        """發送訊息"""
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return "break"
        if text == "/quit":
            self.on_close()
            return "break"
        
        if self.tcp_sock and self.crypto:
            with suppress(OSError):
                send_encrypted(self.tcp_sock, text, self.crypto)
        
        self.entry.delete("1.0", tk.END)
        return "break"
    
    def on_newline(self, event=None) -> None:
        """Shift+Enter 換行"""
        return None
    
    def on_close(self) -> None:
        """關閉連線"""
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
