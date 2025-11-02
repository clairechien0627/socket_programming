from __future__ import annotations
import argparse
import socket
import threading
from contextlib import suppress

HOST = "0.0.0.0"
PORT = 5678
ENCODING = "utf-8"

clients = {}
clients_lock = threading.Lock()


def broadcast(message: str, sender: str | None = None) -> None:
    """Send message to every connected client except the sender."""
    data = message.encode(ENCODING)
    with clients_lock:
        targets = [conn for nick, conn in clients.items() if nick != sender]
    for conn in targets:
        with suppress(OSError):
            conn.sendall(data)


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
        conn.sendall(b"Enter nickname: ")
        raw = conn.recv(1024)
        if not raw:
            return
        nickname = safe_register(raw.decode(ENCODING, errors="ignore").strip(), conn)
        registered = True
        conn.sendall(f"Welcome {nickname}! Type /quit to exit.\n".encode(ENCODING))
        broadcast(f"[system] {nickname} joined the chat.\n")
        while True:
            data = conn.recv(1024)
            if not data:
                break
            message = data.decode(ENCODING, errors="ignore").rstrip("\r\n")
            if message == "/quit":
                conn.sendall(b"Goodbye!\n")
                break
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

