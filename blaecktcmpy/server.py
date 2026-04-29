"""BlaeckTCmPy - MicroPython BlaeckTCP server implementation."""

import time
import struct

# Compatibility shim: provide ticks_ms/ticks_diff on CPython
if not hasattr(time, "ticks_ms"):
    def _ticks_ms():
        return int(time.time() * 1000)

    def _ticks_diff(a, b):
        return a - b

    time.ticks_ms = _ticks_ms
    time.ticks_diff = _ticks_diff

from .signal import Signal, SignalList, DATATYPE_TO_CODE
from .encoder import (
    MSG_SYMBOL_LIST,
    MSG_DATA,
    MSG_DEVICES,
    STATUS_OK,
    build_header,
    wrap_frame,
    build_data_frame,
    build_symbol_payload,
    encode_device_entry,
    build_client_trailer,
    compute_schema_hash,
)
from .tcp import ClientManager

__version__ = "1.0.0"

# Interval mode constants
INTERVAL_CLIENT = -2
INTERVAL_OFF = -1

# Timestamp mode constants
TIMESTAMP_NONE = 0
TIMESTAMP_UNIX = 2

# Message IDs for data frames
_MSG_ID_ACTIVATE = 185273099
_MSG_ID_HUB = 185273100


class _IntervalTimer:
    """Reusable interval timer using ticks_ms."""

    def __init__(self):
        self._interval_ms = 0
        self._base_ms = 0
        self._setpoint_ms = 0
        self._first_tick = False

    @property
    def interval_ms(self):
        return self._interval_ms

    def activate(self, interval_ms):
        self._interval_ms = interval_ms
        self._first_tick = True

    def deactivate(self):
        self._interval_ms = 0
        self._first_tick = False

    def elapsed(self):
        if self._interval_ms == 0:
            return True
        now = time.ticks_ms()
        if self._first_tick:
            self._base_ms = now
            self._setpoint_ms = self._interval_ms
            self._first_tick = False
            return True
        elapsed_ms = time.ticks_diff(now, self._base_ms)
        if elapsed_ms < self._setpoint_ms:
            return False
        while self._setpoint_ms <= elapsed_ms:
            self._setpoint_ms += self._interval_ms
        return True


class BlaeckTCmPy:
    """MicroPython BlaeckTCP server.

    Wire-compatible with BlaeckTCP protocol. Works with Loggbok
    and all BlaeckTCP clients.
    """

    def __init__(self, ip, port, device_name, device_hw_version="", device_fw_version="1.0", verbose=True):
        """Initialize BlaeckTCmPy.

        Args:
            ip: IP address to bind to
            port: TCP port to listen on
            device_name: Name of the device
            device_hw_version: Hardware version string
            device_fw_version: Firmware version string
            verbose: Enable print output for connection events
        """
        self._ip = ip
        self._port = port
        self._verbose = verbose

        # Device info
        self.signals = SignalList()
        self._device_name = device_name.encode() if isinstance(device_name, str) else device_name
        self._device_hw_version = device_hw_version.encode() if isinstance(device_hw_version, str) else device_hw_version
        self._device_fw_version = device_fw_version.encode() if isinstance(device_fw_version, str) else device_fw_version

        # Protocol state
        self._timed_activated = False
        self._fixed_interval_ms = INTERVAL_CLIENT
        self._timer = _IntervalTimer()
        self._command_handlers = {}
        self._read_callback = None
        self._connect_callback = None
        self._disconnect_callback = None
        self._before_write_callback = None
        self._server_restarted = True
        self._restart_flag_pending = True
        self._tcp = ClientManager(self, verbose)
        self._closed = False
        self._timestamp_mode = TIMESTAMP_NONE
        self._schema_hash = 0
        self._started = False

        # Epoch offset for UNIX timestamps (MicroPython may use 2000 epoch)
        self._epoch_offset_us = 0

    # ================================================================
    # Lifecycle
    # ================================================================

    def start(self):
        """Create socket, bind, listen, and activate."""
        if self._started:
            raise RuntimeError("Already started")

        # Detect epoch for UNIX timestamp mode
        self._detect_epoch()

        # Create and bind socket
        self._tcp.init_socket()
        self._tcp.bind(self._ip, self._port)
        self._tcp.start_listening()

        self._started = True
        self._start_time = time.time()
        self._update_schema_hash()

        # Activate fixed interval if set
        if self._fixed_interval_ms >= 0:
            self._timed_activated = True
            self._timer.activate(self._fixed_interval_ms)

        if self._verbose:
            print("blaecktcmpy v{} - Listening on {}:{}".format(
                __version__, self._ip, self._port
            ))

    def close(self):
        """Close all connections."""
        if self._closed:
            return
        self._closed = True
        self._tcp.close()
        if self._verbose:
            print("Server closed")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _detect_epoch(self):
        """Detect MicroPython epoch and compute offset to Unix epoch."""
        try:
            epoch_year = time.gmtime(0)[0]
            if epoch_year == 2000:
                # MicroPython uses 2000-01-01 as epoch
                # Offset: seconds between 1970-01-01 and 2000-01-01
                self._epoch_offset_us = 946684800 * 1_000_000
            else:
                self._epoch_offset_us = 0
        except (AttributeError, TypeError):
            self._epoch_offset_us = 0

    # ================================================================
    # Signal Management
    # ================================================================

    def add_signal(self, signal_or_name, datatype="", value=0):
        """Add a local signal.

        Can be called with a Signal object or with arguments:
            add_signal(Signal('temp', 'float', 0.0))
            add_signal('temp', 'float', 0.0)

        Returns the added Signal.
        """
        if isinstance(signal_or_name, Signal):
            sig = signal_or_name
        elif isinstance(signal_or_name, str):
            sig = Signal(signal_or_name, datatype, value)
        else:
            raise TypeError("Expected Signal or str")

        self.signals.append(sig)
        if self._started:
            self._update_schema_hash()
        return sig

    def add_signals(self, signals):
        """Add multiple signals at once."""
        for sig in signals:
            self.add_signal(sig)

    def delete_signals(self):
        """Remove all signals."""
        self.signals.clear()
        if self._started:
            self._update_schema_hash()

    def _resolve_signal(self, key):
        """Resolve a signal name or index to a valid index."""
        if isinstance(key, int):
            if 0 <= key < len(self.signals):
                return key
            raise IndexError("Signal index {} out of range".format(key))
        idx = self.signals.index_of(key)
        if idx is not None:
            return idx
        raise KeyError("Signal '{}' not found".format(key))

    # ================================================================
    # Write Methods
    # ================================================================

    def write(self, key, value, msg_id=1, unix_timestamp=None):
        """Update a single signal's value and immediately send it."""
        self._require_started()
        idx = self._resolve_signal(key)
        self.signals[idx].value = value
        if not self.connected:
            return
        ts = self._resolve_timestamp(unix_timestamp)
        header = MSG_DATA + b":" + struct.pack("<I", msg_id) + b":"
        data = wrap_frame(
            self._build_data_msg(header, idx, idx, timestamp=ts)
        )
        self._tcp.send_data(data)

    def update(self, key, value):
        """Update a signal's value and mark it as updated (no send)."""
        idx = self._resolve_signal(key)
        self.signals[idx].value = value
        self.signals[idx].updated = True

    def mark_signal_updated(self, key):
        """Mark a signal as updated without changing its value."""
        idx = self._resolve_signal(key)
        self.signals[idx].updated = True

    def mark_all_signals_updated(self):
        """Mark all signals as updated."""
        for i in range(len(self.signals)):
            self.signals[i].updated = True

    def clear_all_update_flags(self):
        """Clear the updated flag on all signals."""
        for i in range(len(self.signals)):
            self.signals[i].updated = False

    @property
    def has_updated_signals(self):
        """True if any signal is marked as updated."""
        for i in range(len(self.signals)):
            if self.signals[i].updated:
                return True
        return False

    def write_all_data(self, msg_id=1, unix_timestamp=None):
        """Send all signal data to data-enabled clients."""
        self._require_started()
        if not self.connected:
            return
        n = len(self.signals)
        if n == 0:
            return
        if self._before_write_callback is not None:
            self._before_write_callback()
        ts = self._resolve_timestamp(unix_timestamp)
        header = MSG_DATA + b":" + struct.pack("<I", msg_id) + b":"
        data = wrap_frame(
            self._build_data_msg(header, start=0, end=n - 1, timestamp=ts)
        )
        self._tcp.send_data(data)

    def write_updated_data(self, msg_id=1, unix_timestamp=None):
        """Send only updated signals to data-enabled clients."""
        self._require_started()
        if not self.connected or not self.has_updated_signals:
            return
        n = len(self.signals)
        if n == 0:
            return
        if self._before_write_callback is not None:
            self._before_write_callback()
        ts = self._resolve_timestamp(unix_timestamp)
        header = MSG_DATA + b":" + struct.pack("<I", msg_id) + b":"
        data = wrap_frame(
            self._build_data_msg(header, start=0, end=n - 1, only_updated=True, timestamp=ts)
        )
        self._tcp.send_data(data)

    def timed_write_all_data(self, msg_id=None, unix_timestamp=None):
        """Send all data if timer interval has elapsed. Returns True if sent."""
        self._require_started()
        if msg_id is None:
            msg_id = _MSG_ID_HUB if self._fixed_interval_ms >= 0 else _MSG_ID_ACTIVATE
        if not self.connected:
            return False
        if not self._timed_activated:
            return False
        n = len(self.signals)
        if n == 0:
            return False
        if not self._timer.elapsed():
            return False
        if self._before_write_callback is not None:
            self._before_write_callback()
        ts = self._resolve_timestamp(unix_timestamp)
        header = MSG_DATA + b":" + struct.pack("<I", msg_id) + b":"
        data = wrap_frame(
            self._build_data_msg(header, start=0, end=n - 1, timestamp=ts)
        )
        return self._tcp.send_data(data)

    def timed_write_updated_data(self, msg_id=None, unix_timestamp=None):
        """Send only updated signals if timer interval has elapsed. Returns True if sent."""
        self._require_started()
        if msg_id is None:
            msg_id = _MSG_ID_HUB if self._fixed_interval_ms >= 0 else _MSG_ID_ACTIVATE
        if not self.connected:
            return False
        if not self._timed_activated:
            return False
        n = len(self.signals)
        if n == 0:
            return False
        if not self._timer.elapsed():
            return False
        if not self.has_updated_signals:
            return False
        if self._before_write_callback is not None:
            self._before_write_callback()
        ts = self._resolve_timestamp(unix_timestamp)
        header = MSG_DATA + b":" + struct.pack("<I", msg_id) + b":"
        data = wrap_frame(
            self._build_data_msg(header, start=0, end=n - 1, only_updated=True, timestamp=ts)
        )
        return self._tcp.send_data(data)

    # ================================================================
    # Main Loop
    # ================================================================

    def tick(self, msg_id=None):
        """Main loop tick - read commands and send all data on timer.

        Call this repeatedly in your main loop.
        Returns True if timed data was sent.
        """
        self.read()
        return self.timed_write_all_data(msg_id)

    def tick_updated(self, msg_id=None):
        """Main loop tick - read commands and send only updated data.

        Returns True if timed data was sent.
        """
        self.read()
        return self.timed_write_updated_data(msg_id)

    # ================================================================
    # Connection & State
    # ================================================================

    @property
    def connected(self):
        """True if any client is connected."""
        return bool(self._tcp._clients)

    @property
    def start_time(self):
        """Wall-clock time when start() was called (time.time())."""
        return self._start_time

    @property
    def data_clients(self):
        """Set of client IDs that receive data frames."""
        return self._tcp.data_clients

    @data_clients.setter
    def data_clients(self, value):
        self._tcp.data_clients = value

    @property
    def local_interval_ms(self):
        """Local signal timed data interval mode.

        >= 0: Lock at given rate (ms). Client ACTIVATE/DEACTIVATE ignored.
        INTERVAL_OFF (-1): Timed data off.
        INTERVAL_CLIENT (-2): Client controlled (default).
        """
        return self._fixed_interval_ms

    @local_interval_ms.setter
    def local_interval_ms(self, value):
        if value >= 0:
            self._fixed_interval_ms = value
            self._timed_activated = True
            self._timer.activate(value)
        elif value == INTERVAL_OFF:
            self._fixed_interval_ms = INTERVAL_OFF
            self._timed_activated = False
            self._timer.deactivate()
        elif value == INTERVAL_CLIENT:
            self._fixed_interval_ms = INTERVAL_CLIENT
        else:
            raise ValueError("Invalid local_interval_ms: {}".format(value))

    @property
    def timestamp_mode(self):
        """Timestamp mode: TIMESTAMP_NONE (0) or TIMESTAMP_UNIX (2)."""
        return self._timestamp_mode

    @timestamp_mode.setter
    def timestamp_mode(self, value):
        if value not in (TIMESTAMP_NONE, TIMESTAMP_UNIX):
            raise ValueError("Invalid timestamp_mode: {}".format(value))
        self._timestamp_mode = value

    # ================================================================
    # Callbacks (decorator API)
    # ================================================================

    def on_command(self, command=None):
        """Decorator to register a command handler.

        With a command name: handles that specific command.
        Without: catch-all for every command.

        Example:
            @bltcp.on_command("SET_LED")
            def handle_led(state):
                print("LED =", state)

            @bltcp.on_command()
            def log_all(command, *params):
                print(command, params)
        """
        def decorator(func):
            if command is None:
                self._read_callback = func
            else:
                self._command_handlers[command] = func
            return func
        return decorator

    def on_before_write(self):
        """Decorator for callback that fires before data is written.

        Example:
            @bltcp.on_before_write()
            def refresh():
                bltcp.signals[0].value = read_sensor()
        """
        def decorator(func):
            self._before_write_callback = func
            return func
        return decorator

    def on_client_connected(self):
        """Decorator for callback when a client connects.

        Example:
            @bltcp.on_client_connected()
            def on_connect(client_id):
                print("Client", client_id, "connected")
        """
        def decorator(func):
            self._connect_callback = func
            return func
        return decorator

    def on_client_disconnected(self):
        """Decorator for callback when a client disconnects.

        Example:
            @bltcp.on_client_disconnected()
            def on_disconnect(client_id):
                print("Client", client_id, "left")
        """
        def decorator(func):
            self._disconnect_callback = func
            return func
        return decorator

    # ================================================================
    # Command Processing
    # ================================================================

    def read(self):
        """Read and process all pending messages from clients."""
        self._require_started()
        messages = self._tcp.read_commands()

        for command, params, conn in messages:
            self._tcp._commanding_client = conn
            self._dispatch_protocol_command(command, params, conn)

            # Dispatch to specific handler
            handler = self._command_handlers.get(command)
            if handler is not None:
                handler(*params)

            # Fire catch-all
            if self._read_callback is not None:
                self._read_callback(command, *params)

    def _dispatch_protocol_command(self, command, params, conn):
        """Handle BLAECK.* protocol commands."""
        if command == "BLAECK.WRITE_SYMBOLS":
            self.write_symbols(self._decode_four_byte(params))
        elif command == "BLAECK.GET_DEVICES":
            self._update_client_identity(params, conn)
            self.write_devices(self._decode_four_byte(params))
        elif command == "BLAECK.ACTIVATE":
            if self._fixed_interval_ms == INTERVAL_CLIENT:
                self._set_timed_data(True, self._decode_four_byte(params))
        elif command == "BLAECK.DEACTIVATE":
            if self._fixed_interval_ms == INTERVAL_CLIENT:
                self._set_timed_data(False)
        elif command == "BLAECK.WRITE_DATA":
            self.write_all_data(self._decode_four_byte(params))

    def _update_client_identity(self, params, conn):
        """Extract optional client name/type from GET_DEVICES params."""
        if len(params) <= 4:
            return
        client_id = self._tcp.client_id_for(conn)
        if client_id < 0:
            return
        name = params[4].strip() if len(params) > 4 else ""
        rtype = params[5].strip() if len(params) > 5 else "unknown"
        if name:
            self._tcp._client_meta[client_id] = {"name": name, "type": rtype}
            if self._verbose:
                print("Client #{} identified ({}: {})".format(client_id, rtype, name))

    @staticmethod
    def _decode_four_byte(params):
        """Decode up to 4 parameter bytes into a little-endian integer."""
        result = 0
        for i in range(min(4, len(params))):
            try:
                result += int(params[i]) << (i * 8)
            except ValueError:
                pass
        return result

    def _set_timed_data(self, activated, interval_ms=0):
        """Activate or deactivate timed data transmission."""
        self._timed_activated = activated
        if activated:
            self._timer.activate(interval_ms)
            if self._verbose:
                print("Interval: {} ms (ACTIVATE)".format(interval_ms))
        else:
            self._timer.deactivate()
            if self._verbose:
                print("Interval: OFF (DEACTIVATE)")

        # Deactivate when no clients remain
        if not self._tcp._clients and self._fixed_interval_ms == INTERVAL_CLIENT:
            self._timed_activated = False

    # ================================================================
    # Message Writers
    # ================================================================

    def write_symbols(self, msg_id=1):
        """Send symbol list to connected clients."""
        if not self.connected:
            return
        header = MSG_SYMBOL_LIST + b":" + struct.pack("<I", msg_id) + b":"
        payload = build_symbol_payload(self.signals)
        data = wrap_frame(header + payload)
        self._tcp.send_all(data)

    def write_devices(self, msg_id=1):
        """Send device information to each connected client."""
        if not self.connected:
            return
        header = MSG_DEVICES + b":" + struct.pack("<I", msg_id) + b":"

        for client_id, conn in list(self._tcp._clients.items()):
            payload = self._build_device_payload(client_id)
            data = wrap_frame(header + payload)
            try:
                conn.sendall(data)
            except OSError:
                self._tcp.disconnect(conn)

        self._server_restarted = False

    def _build_device_payload(self, client_id):
        """Build B6 payload: DeviceCount=1 + device entry + client trailer."""
        payload = b"\x01" + encode_device_entry(
            b"\x00",
            b"\x00",
            self._device_name,
            self._device_hw_version,
            self._device_fw_version,
            __version__.encode(),
            b"blaecktcmpy",
            b"1" if self._server_restarted else b"0",
            b"server",
            b"0",
        )
        return payload + build_client_trailer(
            client_id, self._tcp.data_clients, self._tcp._client_meta
        )

    # ================================================================
    # Internal Protocol
    # ================================================================

    def _build_data_msg(self, header, start=0, end=-1, only_updated=False, timestamp=None, status=STATUS_OK, status_payload=b"\x00\x00\x00\x00"):
        """Build data message with CRC32 checksum."""
        restart = self._restart_flag_pending
        self._restart_flag_pending = False
        return build_data_frame(
            header,
            self.signals,
            start,
            end,
            schema_hash=self._schema_hash,
            restart_flag=restart,
            timestamp_mode=self._timestamp_mode,
            timestamp=timestamp,
            only_updated=only_updated,
            status=status,
            status_payload=status_payload,
        )

    def _update_schema_hash(self):
        """Recompute schema hash from all signals."""
        pairs = []
        for sig in self.signals:
            code = DATATYPE_TO_CODE.get(sig.datatype, 0)
            pairs.append((sig.signal_name, code))
        self._schema_hash = compute_schema_hash(pairs)

    def _resolve_timestamp(self, unix_timestamp):
        """Resolve timestamp to microseconds or None."""
        if unix_timestamp is not None:
            if self._timestamp_mode != TIMESTAMP_UNIX:
                raise ValueError("unix_timestamp requires TIMESTAMP_UNIX mode")
            if isinstance(unix_timestamp, float):
                return int(unix_timestamp * 1_000_000)
            if isinstance(unix_timestamp, int):
                return unix_timestamp
            raise TypeError("unix_timestamp must be float (seconds) or int (us)")

        return self._auto_timestamp()

    def _auto_timestamp(self):
        """Return auto-generated timestamp for current mode, or None."""
        if self._timestamp_mode == TIMESTAMP_UNIX:
            # time.time() returns seconds since MicroPython epoch
            return int(time.time()) * 1_000_000 + self._epoch_offset_us
        return None

    def _require_started(self):
        if not self._started:
            raise RuntimeError("Server not started - call start() first")

    def __repr__(self):
        n = len(self._tcp._clients)
        active = "active" if self._timed_activated else "inactive"
        return "blaecktcmpy [{} client(s)] [{}] ({} signals)".format(
            n, active, len(self.signals)
        )
