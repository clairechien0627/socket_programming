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
        self.root = tk.Tk()
        self.root.title(f"Chat - {nickname}")
        self.text = scrolledtext.ScrolledText(self.root, state=tk.DISABLED, wrap=tk.WORD, width=60, height=20)
        self.text.pack(padx=12, pady=12, fill=tk.BOTH, expand=True)
        self.entry = tk.Entry(self.root)
        self.entry.pack(padx=12, pady=(0, 12), fill=tk.X)
        self.entry.bind("<Return>", self.on_send)
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
        self.text.configure(state=tk.NORMAL)
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
        text = self.entry.get().strip()
        if not text:
            return
        if text == "/quit":
            self.on_close()
            return
        if self.sock:
            with suppress(OSError):
                self.sock.sendall(f"{text}\n".encode(ENCODING))
        self.append_text(f"{self.nickname} (you): {text}\n")
        self.entry.delete(0, tk.END)

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

