from __future__ import annotations
import argparse
import socket
import threading
import struct
from contextlib import suppress

HOST = "0.0.0.0"
PORT = 5678
ENCODING = "utf-8"
BUFFER_SIZE = 1024  # 限制 buffer 大小以測試訊息分割

clients = {}
clients_lock = threading.Lock()


def send_message(conn: socket.socket, message: str) -> None:
    """使用長度前綴協議發送完整訊息,支援超過 buffer size 的大訊息"""
    try:
        data = message.encode(ENCODING)
        length = len(data)
        # 發送 4 bytes 的訊息長度 (big-endian)
        conn.sendall(struct.pack('>I', length))
        # 分塊發送實際訊息內容
        sent = 0
        while sent < length:
            chunk = data[sent:sent + BUFFER_SIZE]
            conn.sendall(chunk)
            sent += len(chunk)
    except OSError:
        pass


def recv_message(conn: socket.socket) -> str | None:
    """使用長度前綴協議接收完整訊息,支援超過 buffer size 的大訊息"""
    try:
        # 先接收 4 bytes 的長度資訊
        length_data = b''
        while len(length_data) < 4:
            chunk = conn.recv(4 - len(length_data))
            if not chunk:
                return None
            length_data += chunk
        
        length = struct.unpack('>I', length_data)[0]
        
        # 根據長度接收完整訊息
        data = b''
        while len(data) < length:
            remaining = length - len(data)
            chunk_size = min(BUFFER_SIZE, remaining)
            chunk = conn.recv(chunk_size)
            if not chunk:
                return None
            data += chunk
        
        return data.decode(ENCODING, errors='ignore')
    except OSError:
        return None


def broadcast(message: str, sender: str | None = None) -> None:
    """Send message to every connected client except the sender."""
    with clients_lock:
        targets = [(nick, conn) for nick, conn in clients.items() if nick != sender]
    for nick, conn in targets:
        send_message(conn, message)


def safe_register(nickname: str, conn: socket.socket) -> str:
    candidate = nickname or "guest"
    with clients_lock:
        base = candidate
        suffix = 1
        while candidate in clients:
            candidate = f"{base}_{suffix}"
            suffix += 1
        clients[candidate] = conn
    return candidate


def remove_client(nickname: str) -> None:
    with clients_lock:
        conn = clients.pop(nickname, None)
    if conn:
        with suppress(OSError):
            conn.close()


def handle_client(conn: socket.socket, address: tuple[str, int]) -> None:
    nickname = "unknown"
    registered = False
    try:
        send_message(conn, "Enter nickname: ")
        nickname_input = recv_message(conn)
        if not nickname_input:
            return
        nickname = safe_register(nickname_input.strip(), conn)
        registered = True
        send_message(conn, f"Welcome {nickname}! Type /quit to exit.\n")
        broadcast(f"[system] {nickname} joined the chat.\n")
        while True:
            message = recv_message(conn)
            if not message:
                break
            message = message.rstrip("\r\n")
            if message == "/quit":
                with suppress(OSError):
                    send_message(conn, "Goodbye!\n")
                break
            print(f"[{nickname}] sent {len(message)} bytes: {message[:50]}{'...' if len(message) > 50 else ''}")
            broadcast(f"{nickname}: {message}\n", sender=nickname)
    except ConnectionResetError:
        pass
    finally:
        if registered:
            remove_client(nickname)
            broadcast(f"[system] {nickname} left the chat.\n")
            print(f"Disconnected: {nickname} {address}")
        else:
            with suppress(OSError):
                conn.close()


def serve_forever(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen()
        print(f"[chat server] listening on {host}:{port}")
        try:
            while True:
                conn, address = server.accept()
                print(f"Connected: {address}")
                thread = threading.Thread(target=handle_client, args=(conn, address), daemon=True)
                thread.start()
        except KeyboardInterrupt:
            print("Stopping server...")
        finally:
            with clients_lock:
                active = list(clients.keys())
            for nickname in active:
                remove_client(nickname)
            print("Server stopped.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-client TCP chat server")
    parser.add_argument("--host", default=HOST, help="Host/IP to bind")
    parser.add_argument("--port", type=int, default=PORT, help="TCP port to bind")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    serve_forever(args.host, args.port)

