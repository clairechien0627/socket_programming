from __future__ import annotations

import argparse
import socket
import threading
import queue
import sys
from contextlib import suppress

HOST = "127.0.0.1"
PORT = 5678
ENCODING = "utf-8"

try:
    import tkinter as tk
    from tkinter import scrolledtext, messagebox
except ModuleNotFoundError:  # GUI remains optional
    tk = None
    scrolledtext = None
    messagebox = None


def connect_to_server(host: str, port: int, nickname: str) -> tuple[socket.socket, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((host, port))
    greeting = sock.recv(1024).decode(ENCODING, errors="ignore")
    sock.sendall(f"{nickname}\n".encode(ENCODING))
    return sock, greeting


def receiver_loop(sock: socket.socket, stop_event: threading.Event, on_message) -> None:
    while not stop_event.is_set():
        try:
            data = sock.recv(1024)
        except OSError:
            break
        if not data:
            stop_event.set()
            on_message("[system] Connection closed by server.\n")
            break
        on_message(data.decode(ENCODING, errors="ignore"))


def console_client(host: str, port: int, nickname: str) -> None:
    sock = None
    stop_event = threading.Event()
    try:
        sock, greeting = connect_to_server(host, port, nickname)
        if greeting:
            print(greeting, end="")

        def display(text: str) -> None:
            print(text, end="")

        threading.Thread(target=receiver_loop, args=(sock, stop_event, display), daemon=True).start()

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
                sock.sendall(b"/quit\n")
                break
            sock.sendall(f"{message}\n".encode(ENCODING))
            display(f"{nickname} (you): {message}\n")
    except OSError as exc:
        print(f"Failed to connect: {exc}")
    finally:
        stop_event.set()
        if sock:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()


class ChatGUI:
    def __init__(self, host: str, port: int, nickname: str) -> None:
        if tk is None:
            raise RuntimeError("Tkinter is not available in this environment.")
        self.host = host
        self.port = port
        self.nickname = nickname
        self.sock: socket.socket | None = None
        self.stop_event = threading.Event()
        self.queue: queue.Queue[str] = queue.Queue()
        
        # 建立主視窗
        self.root = tk.Tk()
        self.root.title(f"聊天室 - {nickname}")
        self.root.geometry("700x550")
        
        # 頂部資訊列
        info_frame = tk.Frame(self.root, bg="#2c3e50", height=40)
        info_frame.pack(fill=tk.X, side=tk.TOP)
        info_frame.pack_propagate(False)
        
        title_label = tk.Label(info_frame, text=f"👤 {nickname}", 
                              bg="#2c3e50", fg="white", font=("Arial", 11, "bold"))
        title_label.pack(side=tk.LEFT, padx=15, pady=8)
        
        self.status_label = tk.Label(info_frame, text="● 連線中", 
                                     bg="#2c3e50", fg="#2ecc71", font=("Arial", 9))
        self.status_label.pack(side=tk.RIGHT, padx=15, pady=8)
        
        # 訊息顯示區域
        chat_frame = tk.Frame(self.root, bg="#ecf0f1")
        chat_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))
        
        self.text = scrolledtext.ScrolledText(
            chat_frame, 
            state=tk.DISABLED, 
            wrap=tk.WORD, 
            bg="#ffffff",
            font=("Arial", 10),
            relief=tk.FLAT,
            padx=10,
            pady=10
        )
        self.text.pack(fill=tk.BOTH, expand=True)
        
        # 設定訊息標籤樣式
        self.text.tag_config("system", foreground="#95a5a6", font=("Arial", 9, "italic"))
        self.text.tag_config("self", foreground="#2980b9", font=("Arial", 10, "bold"))
        self.text.tag_config("other", foreground="#27ae60", font=("Arial", 10, "bold"))
        self.text.tag_config("message", foreground="#2c3e50", font=("Arial", 10))
        
        # 輸入區域
        input_frame = tk.Frame(self.root, bg="#ecf0f1")
        input_frame.pack(fill=tk.X, padx=10, pady=(5, 10))
        
        # 使用 Text 而非 Entry 以支援多行
        self.entry = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Arial", 10))
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.entry.bind("<Return>", self.on_send)
        self.entry.bind("<Shift-Return>", self.on_newline)
        self.entry.focus()
        
        # 按鈕區域
        button_frame = tk.Frame(input_frame, bg="#ecf0f1")
        button_frame.pack(side=tk.RIGHT, fill=tk.Y)
        
        send_button = tk.Button(
            button_frame, 
            text="發送\n(Enter)", 
            command=self.on_send,
            bg="#3498db",
            fg="white",
            font=("Arial", 9, "bold"),
            width=8,
            relief=tk.FLAT,
            cursor="hand2"
        )
        send_button.pack(fill=tk.BOTH, expand=True)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def start(self) -> None:
        try:
            self.sock, greeting = connect_to_server(self.host, self.port, self.nickname)
        except OSError as exc:
            if messagebox:
                messagebox.showerror("Connection failed", str(exc))
            else:
                print(f"Failed to connect: {exc}")
            self.root.destroy()
            return

        if greeting:
            self.append_text(greeting)

        threading.Thread(target=receiver_loop, args=(self.sock, self.stop_event, self.queue.put), daemon=True).start()
        self.root.after(100, self.process_queue)
        self.root.mainloop()

    def append_text(self, message: str) -> None:
        """智慧顯示訊息,根據類型套用不同樣式"""
        self.text.configure(state=tk.NORMAL)
        
        # 判斷訊息類型並套用樣式
        if message.startswith("[system]"):
            self.text.insert(tk.END, message, "system")
        elif message.startswith(f"{self.nickname} (you):"):
            # 自己的訊息
            parts = message.split(":", 1)
            self.text.insert(tk.END, parts[0] + ": ", "self")
            if len(parts) > 1:
                self.text.insert(tk.END, parts[1], "message")
        elif ":" in message and not message.startswith("Welcome"):
            # 別人的訊息
            parts = message.split(":", 1)
            self.text.insert(tk.END, parts[0] + ": ", "other")
            if len(parts) > 1:
                self.text.insert(tk.END, parts[1], "message")
        else:
            # 一般訊息
            self.text.insert(tk.END, message)
        
        self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def process_queue(self) -> None:
        while not self.queue.empty():
            self.append_text(self.queue.get())
        if not self.stop_event.is_set():
            self.root.after(100, self.process_queue)
        else:
            if messagebox:
                messagebox.showinfo("Disconnected", "Connection closed.")
            self.on_close()

    def on_send(self, event=None) -> None:
        """發送訊息 (Enter 鍵或點擊按鈕)"""
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return "break"  # 防止 Text widget 預設行為
        if text == "/quit":
            self.on_close()
            return "break"
        if self.sock:
            with suppress(OSError):
                self.sock.sendall(f"{text}\n".encode(ENCODING))
        self.append_text(f"{self.nickname} (you): {text}\n")
        self.entry.delete("1.0", tk.END)
        return "break"  # 防止換行
    
    def on_newline(self, event=None) -> None:
        """Shift+Enter 換行"""
        return None  # 允許預設行為 (插入換行)

    def on_close(self) -> None:
        self.stop_event.set()
        if self.sock:
            with suppress(OSError):
                self.sock.sendall(b"/quit\n")
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP chat client with optional GUI")
    parser.add_argument("nickname", help="Nickname shown in chat")
    parser.add_argument("--host", default=HOST, help="Server host")
    parser.add_argument("--port", type=int, default=PORT, help="Server port")
    parser.add_argument("--gui", action="store_true", help="Launch Tkinter interface")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.gui:
        try:
            gui = ChatGUI(args.host, args.port, args.nickname)
        except RuntimeError as exc:
            print(f"[error] {exc}")
            return
        gui.start()
    else:
        console_client(args.host, args.port, args.nickname)


if __name__ == "__main__":
    main()

