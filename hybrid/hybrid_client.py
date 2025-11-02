from __future__ import annotations

import argparse
import socket
import threading
import queue
import sys
import time
import struct
from contextlib import suppress

HOST = "127.0.0.1"
TCP_PORT = 5678
UDP_PORT = 5679
ENCODING = "utf-8"
BUFFER_SIZE = 1024

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
except ModuleNotFoundError:
    tk = None
    scrolledtext = None
    messagebox = None


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


def connect_to_server(host: str, tcp_port: int, nickname: str) -> tuple[socket.socket, str]:
    """建立 TCP 連線"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, tcp_port))
    greeting = recv_message(sock)
    send_message(sock, f"{nickname}\n")
    return sock, greeting or ""


def tcp_receiver_loop(sock: socket.socket, stop_event: threading.Event, on_message) -> None:
    """TCP 接收執行緒 - 接收聊天訊息"""
    while not stop_event.is_set():
        try:
            data = recv_message(sock)
        except OSError:
            break
        if not data:
            stop_event.set()
            on_message("[system] TCP connection closed by server.\n")
            break
        on_message(data)


def udp_receiver_loop(udp_sock: socket.socket, stop_event: threading.Event, on_udp_message) -> None:
    """UDP 接收執行緒 - 接收狀態更新"""
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
    """UDP 心跳執行緒 - 定期發送心跳包"""
    while not stop_event.is_set():
        try:
            udp_sock.sendto(f"HEARTBEAT|{nickname}".encode(ENCODING), server_addr)
            time.sleep(5)  # 每 5 秒發送一次心跳
        except OSError:
            break


def console_client(host: str, tcp_port: int, udp_port: int, nickname: str) -> None:
    """命令列客戶端"""
    tcp_sock = None
    udp_sock = None
    stop_event = threading.Event()
    
    try:
        # 建立 TCP 連線
        tcp_sock, greeting = connect_to_server(host, tcp_port, nickname)
        if greeting:
            print(greeting, end="")
        
        # 建立 UDP socket
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_udp_addr = (host, udp_port)
        
        # 向伺服器註冊 UDP 位址
        udp_sock.sendto(f"REGISTER|{nickname}".encode(ENCODING), server_udp_addr)
        
        def display(text: str) -> None:
            print(text, end="")
        
        def display_udp(text: str) -> None:
            if text.startswith("TYPING|"):
                parts = text.split('|', 1)
                if len(parts) == 2:
                    # 在同一行顯示,不影響聊天記錄
                    sys.stdout.write(f"\r💬 {parts[1]} 正在輸入...{' '*30}")
                    sys.stdout.flush()
                    # 2 秒後清除
                    threading.Timer(2.0, lambda: sys.stdout.write("\r" + " "*60 + "\r")).start()
            elif text == "ACK":
                pass  # 心跳回應,不顯示
        
        # 啟動執行緒
        threading.Thread(target=tcp_receiver_loop, args=(tcp_sock, stop_event, display), daemon=True).start()
        threading.Thread(target=udp_receiver_loop, args=(udp_sock, stop_event, display_udp), daemon=True).start()
        threading.Thread(target=heartbeat_loop, args=(udp_sock, server_udp_addr, nickname, stop_event), daemon=True).start()
        
        print(f"\n{'='*60}")
        print("📡 協議狀態:")
        print(f"  ✓ TCP 連線建立 (用於聊天訊息)")
        print(f"  ✓ UDP 連線建立 (用於狀態更新)")
        print(f"{'='*60}\n")
        print("指令: /quit=離開 | /users=查看在線用戶\n")
        
        while not stop_event.is_set():
            try:
                message = input()
            except EOFError:
                break
            except KeyboardInterrupt:
                print()
                break
            
            if message.strip() == "":
                continue
            
            if message == "/quit":
                send_message(tcp_sock, "/quit\n")
                break
            
            # 發送正在輸入狀態 (UDP)
            udp_sock.sendto(f"TYPING|{nickname}".encode(ENCODING), server_udp_addr)
            
            # 發送聊天訊息 (TCP)
            send_message(tcp_sock, f"{message}\n")
            
    except OSError as exc:
        print(f"Failed to connect: {exc}")
    finally:
        stop_event.set()
        if tcp_sock:
            with suppress(OSError):
                tcp_sock.shutdown(socket.SHUT_RDWR)
            tcp_sock.close()
        if udp_sock:
            udp_sock.close()


class HybridChatGUI:
    def __init__(self, host: str, tcp_port: int, udp_port: int, nickname: str) -> None:
        if tk is None:
            raise RuntimeError("Tkinter is not available in this environment.")
        
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.nickname = nickname
        self.tcp_sock: socket.socket | None = None
        self.udp_sock: socket.socket | None = None
        self.stop_event = threading.Event()
        self.tcp_queue: queue.Queue[str] = queue.Queue()
        self.udp_queue: queue.Queue[str] = queue.Queue()
        
        # 建立 GUI
        self.root = tk.Tk()
        self.root.title(f"混合協議聊天室 - {nickname}")
        self.root.geometry("750x600")
        
        # 頂部資訊列
        info_frame = tk.Frame(self.root, bg="#2c3e50", height=60)
        info_frame.pack(fill=tk.X, side=tk.TOP)
        info_frame.pack_propagate(False)
        
        title_label = tk.Label(info_frame, text=f"👤 {nickname}", 
                              bg="#2c3e50", fg="white", font=("Arial", 11, "bold"))
        title_label.pack(side=tk.LEFT, padx=15, pady=8)
        
        # 協議狀態指示器
        self.tcp_status = tk.Label(info_frame, text="TCP: ●", 
                                   bg="#2c3e50", fg="#2ecc71", font=("Arial", 9))
        self.tcp_status.pack(side=tk.RIGHT, padx=5, pady=8)
        
        self.udp_status = tk.Label(info_frame, text="UDP: ●", 
                                   bg="#2c3e50", fg="#3498db", font=("Arial", 9))
        self.udp_status.pack(side=tk.RIGHT, padx=5, pady=8)
        
        # 狀態標籤
        self.status_label = tk.Label(info_frame, text="", 
                                     bg="#2c3e50", fg="#f39c12", font=("Arial", 9, "italic"))
        self.status_label.pack(side=tk.RIGHT, padx=15, pady=8)
        
        # 說明標籤
        explain_frame = tk.Frame(self.root, bg="#34495e", height=50)
        explain_frame.pack(fill=tk.X)
        explain_frame.pack_propagate(False)
        
        explain_text = "💡 TCP=聊天訊息(可靠) | UDP=狀態更新/心跳(快速)"
        explain_label = tk.Label(explain_frame, text=explain_text,
                                bg="#34495e", fg="white", font=("Arial", 9))
        explain_label.pack(pady=15)
        
        # 訊息顯示區域
        chat_frame = tk.Frame(self.root, bg="#ecf0f1")
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))
        
        # 正在輸入狀態列 (在聊天區域上方)
        self.typing_frame = tk.Frame(chat_frame, bg="#f8f9fa", height=25, relief=tk.FLAT)
        self.typing_frame.pack(fill=tk.X, pady=(0, 2))
        self.typing_frame.pack_propagate(False)
        
        self.typing_label = tk.Label(
            self.typing_frame, 
            text="",
            bg="#f8f9fa", 
            fg="#7f8c8d", 
            font=("Arial", 9, "italic"),
            anchor="w",
            padx=10
        )
        self.typing_label.pack(fill=tk.BOTH, expand=True)
        self.typing_users = set()  # 追蹤正在輸入的用戶
        self.typing_timers = {}  # 用戶的清除計時器
        
        self.text = scrolledtext.ScrolledText(
            chat_frame, state=tk.DISABLED, wrap=tk.WORD,
            bg="#ffffff", font=("Arial", 10), relief=tk.FLAT, padx=10, pady=10
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        
        # 設定標籤樣式
        self.text.tag_config("system", foreground="#95a5a6", font=("Arial", 9, "italic"))
        self.text.tag_config("self", foreground="#2980b9", font=("Arial", 10, "bold"))
        self.text.tag_config("other", foreground="#27ae60", font=("Arial", 10, "bold"))
        self.text.tag_config("message", foreground="#2c3e50", font=("Arial", 10))
        self.text.tag_config("udp_status", foreground="#f39c12", font=("Arial", 9, "italic"))
        
        # 輸入區域
        input_frame = tk.Frame(self.root, bg="#ecf0f1")
        input_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        
        self.entry = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Arial", 10))
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.entry.bind("<Return>", self.on_send)
        self.entry.bind("<Shift-Return>", self.on_newline)
        self.entry.bind("<KeyRelease>", self.on_typing)
        self.entry.focus()
        
        # 按鈕區域
        button_frame = tk.Frame(input_frame, bg="#ecf0f1")
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        send_button = tk.Button(
            button_frame, text="發送\n(Enter)", command=self.on_send,
            bg="#3498db", fg="white", font=("Arial", 9, "bold"),
            width=8, relief=tk.FLAT, cursor="hand2"
        )
        send_button.pack(fill=tk.BOTH, expand=True)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.last_typing_time = 0
    
    def start(self) -> None:
        try:
            # 建立 TCP 連線
            self.tcp_sock, greeting = connect_to_server(self.host, self.tcp_port, self.nickname)
            
            # 建立 UDP socket
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_udp_addr = (self.host, self.udp_port)
            
            # 註冊 UDP
            self.udp_sock.sendto(f"REGISTER|{self.nickname}".encode(ENCODING), server_udp_addr)
            
        except OSError as exc:
            if messagebox:
                messagebox.showerror("連線失敗", str(exc))
            else:
                print(f"Failed to connect: {exc}")
            self.root.destroy()
            return
        
        if greeting:
            self.append_text(greeting)
        
        # 啟動執行緒
        threading.Thread(target=tcp_receiver_loop, 
                        args=(self.tcp_sock, self.stop_event, self.tcp_queue.put), 
                        daemon=True).start()
        threading.Thread(target=udp_receiver_loop, 
                        args=(self.udp_sock, self.stop_event, self.udp_queue.put), 
                        daemon=True).start()
        threading.Thread(target=heartbeat_loop, 
                        args=(self.udp_sock, server_udp_addr, self.nickname, self.stop_event), 
                        daemon=True).start()
        
        self.root.after(100, self.process_queues)
        self.root.mainloop()
    
    def append_text(self, message: str, tag: str = None) -> None:
        """顯示訊息"""
        self.text.configure(state=tk.NORMAL)
        
        if tag:
            self.text.insert(tk.END, message, tag)
        elif message.startswith("[system]"):
            self.text.insert(tk.END, message, "system")
        elif ":" in message and not message.startswith("Welcome"):
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
        # 處理 TCP 訊息
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
        
        if not self.stop_event.is_set():
            self.root.after(100, self.process_queues)
        else:
            if messagebox:
                messagebox.showinfo("已斷線", "連線已關閉.")
            self.on_close()
    
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
        """發送訊息 (TCP)"""
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return "break"
        if text == "/quit":
            self.on_close()
            return "break"
        
        if self.tcp_sock:
            with suppress(OSError):
                send_message(self.tcp_sock, f"{text}\n")
        
        # 清除自己的輸入狀態
        self.remove_typing_user(self.nickname)
        
        self.entry.delete("1.0", tk.END)
        return "break"
    
    def on_newline(self, event=None) -> None:
        """Shift+Enter 換行"""
        return None
    
    def on_close(self) -> None:
        """關閉連線"""
        self.stop_event.set()
        if self.tcp_sock:
            with suppress(OSError):
                send_message(self.tcp_sock, "/quit\n")
                self.tcp_sock.shutdown(socket.SHUT_RDWR)
                self.tcp_sock.close()
        if self.udp_sock:
            self.udp_sock.close()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Hybrid TCP/UDP chat client")
    parser.add_argument("nickname", help="Nickname shown in chat")
    parser.add_argument("--host", default=HOST, help="Server host")
    parser.add_argument("--tcp-port", type=int, default=TCP_PORT, help="TCP port")
    parser.add_argument("--udp-port", type=int, default=UDP_PORT, help="UDP port")
    parser.add_argument("--gui", action="store_true", help="Launch GUI")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.gui:
        try:
            gui = HybridChatGUI(args.host, args.tcp_port, args.udp_port, args.nickname)
        except RuntimeError as exc:
            print(f"[error] {exc}")
            return
        gui.start()
    else:
        console_client(args.host, args.tcp_port, args.udp_port, args.nickname)


if __name__ == "__main__":
    main()
