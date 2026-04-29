"""TCP client connection management for BlaeckTCmPy.

Handles socket lifecycle, client accept/disconnect, polling,
and message broadcasting. Uses select.poll() for readiness
with blocking client sockets for reliable sends.
"""

import socket
import select


_CLIENT_RECV_CHUNK = 1024
_MAX_RECV_BUFFER = 8192

# Poll event flags
_POLLIN = select.POLLIN
_POLLHUP = getattr(select, "POLLHUP", 0x10)
_POLLERR = getattr(select, "POLLERR", 0x08)


class ClientManager:
    """Manages TCP server socket and downstream client connections."""

    def __init__(self, server, verbose=False):
        self._server = server
        self._verbose = verbose
        self._server_socket = None
        self._clients = {}
        self._next_client_id = 0
        self._commanding_client = None
        self._poll = None
        self._recv_buffers = {}
        self.data_clients = set()
        self._client_meta = {}
        self._client_addrs = {}
        # Map fileno -> socket for poll lookup
        self._fd_to_sock = {}

    # -- Socket lifecycle --

    def init_socket(self):
        """Create TCP server socket."""
        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def bind(self, ip, port):
        """Bind server socket to ip:port."""
        self._server_socket.bind((ip, port))

    def start_listening(self):
        """Set non-blocking mode on server socket and start listening."""
        self._server_socket.setblocking(False)
        self._server_socket.listen(5)
        self._clients = {}
        self._next_client_id = 0
        self._commanding_client = None
        self._poll = select.poll()
        self._poll.register(self._server_socket, _POLLIN)
        self._fd_to_sock = {}
        self._fd_to_sock[self._server_socket.fileno()] = self._server_socket

    # -- Client connections --

    def accept(self):
        """Accept all pending new connections."""
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

                self._poll.register(conn, _POLLIN)
                self._fd_to_sock[conn.fileno()] = conn

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

    def client_id_for(self, conn):
        """Find the client ID for a given socket, or -1 if not found."""
        for cid, c in self._clients.items():
            if c is conn:
                return cid
        return -1

    def disconnect(self, conn):
        """Remove and close a client connection."""
        client_id = self.client_id_for(conn)

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

    def read_commands(self):
        """Non-blocking read from all clients; parse <cmd,p1,p2> messages."""
        if self._poll is None:
            return []

        messages = []

        # Poll with 0 timeout (non-blocking)
        try:
            # Use ipoll if available (less allocation)
            if hasattr(self._poll, "ipoll"):
                events = self._poll.ipoll(0)
            else:
                events = self._poll.poll(0)
        except OSError:
            return messages

        for item in events:
            fd = item[0]
            event = item[1]

            # Resolve socket from fileno or direct object
            if isinstance(fd, int):
                sock = self._fd_to_sock.get(fd)
            else:
                sock = fd

            if sock is None:
                continue

            # Handle errors/hangups
            if event & (_POLLHUP | _POLLERR):
                if sock is not self._server_socket:
                    self.disconnect(sock)
                continue

            if not (event & _POLLIN):
                continue

            if sock is self._server_socket:
                self.accept()
            else:
                try:
                    chunk = sock.recv(_CLIENT_RECV_CHUNK)
                    if not chunk:
                        self.disconnect(sock)
                        continue

                    buf = self._recv_buffers.get(sock, "") + chunk.decode("utf-8")

                    if len(buf) > _MAX_RECV_BUFFER:
                        self.disconnect(sock)
                        continue

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

        return messages

    def send_all(self, data):
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

    def send_data(self, data):
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

    def close(self):
        """Close all client sockets, the poll object, and the server socket."""
        for conn in list(self._clients.values()):
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
