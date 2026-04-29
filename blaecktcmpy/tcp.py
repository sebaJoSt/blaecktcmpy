"""TCP client connection management for BlaeckTCmPy.

Handles socket lifecycle, client accept/disconnect, polling,
and message broadcasting. Uses select.poll() when available
(MicroPython, Linux) with select.select() fallback (Windows).
Client sockets are blocking for reliable sends.
"""

import socket
import select

try:
    from typing import Any
except ImportError:
    pass


_CLIENT_RECV_CHUNK = 1024
_MAX_RECV_BUFFER = 8192

# Detect poll availability
_HAS_POLL = hasattr(select, "poll")

if _HAS_POLL:
    _POLLIN = select.POLLIN
    _POLLHUP = getattr(select, "POLLHUP", 0x10)
    _POLLERR = getattr(select, "POLLERR", 0x08)


class ClientManager:
    """Manages TCP server socket and downstream client connections."""

    def __init__(self, server: "Any", verbose: bool = False) -> None:
        self._server = server
        self._verbose = verbose
        self._server_socket: "socket.socket | None" = None
        self._clients: "dict[int, socket.socket]" = {}
        self._next_client_id: int = 0
        self._commanding_client: "socket.socket | None" = None
        self._poll: "Any" = None
        self._recv_buffers: "dict[socket.socket, str]" = {}
        self.data_clients: "set[int]" = set()
        self._client_meta: "dict[int, dict[str, str]]" = {}
        self._client_addrs: "dict[int, str]" = {}
        # Map fileno -> socket for poll lookup
        self._fd_to_sock: "dict[int, socket.socket]" = {}

    # -- Socket lifecycle --

    def init_socket(self) -> None:
        """Create TCP server socket."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def bind(self, ip: str, port: int) -> None:
        """Bind server socket to ip:port."""
        assert self._server_socket is not None
        self._server_socket.bind((ip, port))

    def start_listening(self) -> None:
        """Set non-blocking mode on server socket and start listening."""
        assert self._server_socket is not None
        self._server_socket.setblocking(False)
        self._server_socket.listen(5)
        self._clients = {}
        self._next_client_id = 0
        self._commanding_client = None
        self._fd_to_sock = {}
        self._fd_to_sock[self._server_socket.fileno()] = self._server_socket

        if _HAS_POLL:
            self._poll = select.poll()
            self._poll.register(self._server_socket, _POLLIN)
        else:
            self._poll = None

    # -- Client connections --

    def accept(self) -> None:
        """Accept all pending new connections."""
        assert self._server_socket is not None
        while True:
            try:
                conn, addr = self._server_socket.accept()
                # Keep client sockets blocking for reliable sendall()
                conn.setblocking(True)
                # Set TCP_NODELAY for low latency
                try:
                    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except (AttributeError, OSError):
                    pass
                # Set short timeout so reads don't block forever
                conn.settimeout(0.01)

                self._fd_to_sock[conn.fileno()] = conn
                if _HAS_POLL:
                    self._poll.register(conn, _POLLIN)

                client_id = self._next_client_id
                self._next_client_id += 1
                self._clients[client_id] = conn
                self._recv_buffers[conn] = ""
                self.data_clients.add(client_id)
                self._client_meta[client_id] = {"name": "", "type": "unknown"}
                self._client_addrs[client_id] = "{}:{}".format(addr[0], addr[1])

                if self._verbose:
                    print("Client #{} connected: {}:{}".format(client_id, addr[0], addr[1]))

                if self._server._connect_callback is not None:
                    self._server._connect_callback(client_id)
            except OSError:
                break

    def client_id_for(self, conn: "socket.socket") -> int:
        """Find the client ID for a given socket, or -1 if not found."""
        for cid, c in self._clients.items():
            if c is conn:
                return cid
        return -1

    def disconnect(self, conn: "socket.socket") -> None:
        """Remove and close a client connection."""
        client_id = self.client_id_for(conn)

        if _HAS_POLL and self._poll is not None:
            try:
                self._poll.unregister(conn)
            except Exception:
                pass

        fd = None
        for f, s in self._fd_to_sock.items():
            if s is conn:
                fd = f
                break
        if fd is not None:
            del self._fd_to_sock[fd]

        try:
            conn.close()
        except OSError:
            pass

        if client_id >= 0:
            self._clients.pop(client_id, None)
            self.data_clients.discard(client_id)
            meta = self._client_meta.pop(client_id, {})
            self._client_addrs.pop(client_id, None)
        else:
            meta = {}

        self._recv_buffers.pop(conn, None)
        if self._commanding_client is conn:
            self._commanding_client = None

        if self._verbose:
            name = meta.get("name", "")
            cid = client_id if client_id >= 0 else "?"
            if name:
                print("Client #{} disconnected ({})".format(cid, name))
            else:
                print("Client #{} disconnected".format(cid))

        if client_id >= 0 and self._server._disconnect_callback is not None:
            self._server._disconnect_callback(client_id)

    # -- I/O --

    def read_commands(self) -> "list[tuple[str, list[str], socket.socket]]":
        """Non-blocking read from all clients; parse <cmd,p1,p2> messages."""
        messages = []

        if _HAS_POLL and self._poll is not None:
            ready_socks = self._poll_ready()
        else:
            ready_socks = self._select_ready()

        for sock in ready_socks:
            if sock is self._server_socket:
                self.accept()
            else:
                self._read_from_client(sock, messages)

        return messages

    def _poll_ready(self) -> "list[socket.socket]":
        """Get ready sockets using poll()."""
        ready = []
        try:
            if hasattr(self._poll, "ipoll"):
                events = self._poll.ipoll(0)
            else:
                events = self._poll.poll(0)
        except OSError:
            return ready

        for item in events:
            fd = item[0]
            event = item[1]

            if isinstance(fd, int):
                sock = self._fd_to_sock.get(fd)
            else:
                sock = fd

            if sock is None:
                continue

            if event & (_POLLHUP | _POLLERR):
                if sock is not self._server_socket:
                    self.disconnect(sock)
                continue

            if event & _POLLIN:
                ready.append(sock)

        return ready

    def _select_ready(self) -> "list[Any]":
        """Get ready sockets using select.select() (Windows fallback)."""
        all_socks = [self._server_socket] + list(self._clients.values())
        try:
            readable, _, _ = select.select(all_socks, [], [], 0)
        except (OSError, ValueError):
            return []
        return readable

    def _read_from_client(self, sock: "socket.socket", messages: "list[tuple[str, list[str], socket.socket]]") -> None:
        """Read and parse commands from a single client socket."""
        try:
            chunk = sock.recv(_CLIENT_RECV_CHUNK)
            if not chunk:
                self.disconnect(sock)
                return

            buf = self._recv_buffers.get(sock, "") + chunk.decode("utf-8")

            if len(buf) > _MAX_RECV_BUFFER:
                self.disconnect(sock)
                return

            # Parse <cmd,param> messages
            while True:
                start = buf.find("<")
                if start == -1:
                    buf = ""
                    break
                end = buf.find(">", start)
                if end == -1:
                    buf = buf[start:]
                    break
                content = buf[start + 1:end]
                buf = buf[end + 1:]

                parts = content.split(",")
                command = parts[0].strip()
                params = [p.strip() for p in parts[1:]] if len(parts) > 1 else []
                messages.append((command, params, sock))

            self._recv_buffers[sock] = buf

        except OSError:
            self.disconnect(sock)

    def send_all(self, data: bytes) -> bool:
        """Broadcast data to all connected clients."""
        if not self._clients:
            return False
        sent = False
        for conn in list(self._clients.values()):
            try:
                conn.sendall(data)
                sent = True
            except OSError:
                self.disconnect(conn)
        return sent

    def send_data(self, data: bytes) -> bool:
        """Send data only to clients in data_clients set."""
        if not self._clients:
            return False
        sent = False
        for client_id, conn in list(self._clients.items()):
            if client_id not in self.data_clients:
                continue
            try:
                conn.sendall(data)
                sent = True
            except OSError:
                self.disconnect(conn)
        return sent

    # -- Cleanup --

    def close(self) -> None:
        """Close all client sockets, the poll object, and the server socket."""
        for conn in list(self._clients.values()):
            if _HAS_POLL and self._poll is not None:
                try:
                    self._poll.unregister(conn)
                except Exception:
                    pass
            try:
                conn.close()
            except OSError:
                pass
        self._clients.clear()
        self._fd_to_sock.clear()

        if self._poll is not None and self._server_socket is not None:
            try:
                self._poll.unregister(self._server_socket)
            except Exception:
                pass

        if self._server_socket is not None:
            self._server_socket.close()
