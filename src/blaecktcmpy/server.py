"""BlaeckTCmPy - MicroPython BlaeckTCP server implementation."""

import time
import struct

# Compatibility shim: provide ticks_ms/ticks_diff on CPython
if hasattr(time, "ticks_ms"):
    _ticks_ms = getattr(time, "ticks_ms")
    _ticks_diff = getattr(time, "ticks_diff")
    _ticks_us = getattr(time, "ticks_us")
else:
    def _ticks_ms() -> int:
        return int(time.time() * 1000)

    def _ticks_diff(a: int, b: int) -> int:
        return a - b

    def _ticks_us() -> int:
        return int(time.time() * 1_000_000)


def _now_us(epoch_offset_us: int) -> int:
    """Return current time in microseconds since Unix epoch.

    On CPython: uses time.time_ns() for full precision.
    On MicroPython: uses time.time() (integer seconds) + ticks_us()
    for sub-second interpolation, with a consistency check to avoid
    a race at second boundaries.
    """
    if hasattr(time, "time_ns"):
        return time.time_ns() // 1_000 + epoch_offset_us
    # MicroPython: time.time() is integer seconds from platform epoch.
    # Read seconds before and after ticks_us to detect a boundary crossing.
    sec1 = int(time.time())
    frac = _ticks_us() % 1_000_000
    sec2 = int(time.time())
    if sec1 != sec2:
        # Second rolled over during read — re-sample with new second
        frac = _ticks_us() % 1_000_000
        sec1 = sec2
    return sec1 * 1_000_000 + frac + epoch_offset_us

from .signal import Signal, SignalList, DATATYPE_TO_CODE
from .encoder import (
    MSG_SYMBOL_LIST,
    MSG_DATA,
    MSG_DEVICES,
    STATUS_OK,
    wrap_frame,
    build_data_frame,
    build_symbol_payload,
    encode_device_entry,
    build_client_trailer,
    compute_schema_hash,
)
from .tcp import ClientManager

try:
    from typing import Any, Callable
except ImportError:
    pass

from . import __version__


class IntervalMode:
    """Timed data interval modes.

    OFF (-1): Timed data disabled; client ACTIVATE ignored.
    CLIENT (-2): Client controlled (default).
    """

    OFF: int = -1
    CLIENT: int = -2


class TimestampMode:
    """Timestamp modes for data frames.

    NONE (0): No timestamp in data frames (default).
    MICROS (1): Microseconds since start (like Arduino micros()).
    UNIX (2): Microseconds since Unix epoch.
    """

    NONE: int = 0
    MICROS: int = 1
    UNIX: int = 2


# Legacy constants (kept for backwards compatibility)
INTERVAL_CLIENT = IntervalMode.CLIENT
INTERVAL_OFF = IntervalMode.OFF

# Timestamp mode constants
TIMESTAMP_NONE = TimestampMode.NONE
TIMESTAMP_MICROS = TimestampMode.MICROS
TIMESTAMP_UNIX = TimestampMode.UNIX

# Message IDs for data frames
_MSG_ID_ACTIVATE = 185273099
_MSG_ID_HUB = 185273100


class _IntervalTimer:
    """Reusable interval timer using ticks_ms."""

    def __init__(self) -> None:
        self._interval_ms: int = 0
        self._base_ms: int = 0
        self._setpoint_ms: int = 0
        self._first_tick: bool = False

    @property
    def interval_ms(self) -> int:
        return self._interval_ms

    def activate(self, interval_ms: int) -> None:
        self._interval_ms = interval_ms
        self._first_tick = True

    def deactivate(self) -> None:
        self._interval_ms = 0
        self._first_tick = False

    def elapsed(self) -> bool:
        if self._interval_ms == 0:
            return True
        now = _ticks_ms()
        if self._first_tick:
            self._base_ms = now
            self._setpoint_ms = self._interval_ms
            self._first_tick = False
            return True
        elapsed_ms = _ticks_diff(now, self._base_ms)
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

    def __init__(self, ip: str, port: int, device_name: str, device_hw_version: str = "", device_fw_version: str = "1.0", verbose: bool = True) -> None:
        """Initialize BlaeckTCmPy.

        Args:
            ip: IP address to bind to
            port: TCP port to listen on
            device_name: Name of the device
            device_hw_version: Hardware version string
            device_fw_version: Firmware version string
            verbose: Enable print output for connection events
        """
        self._ip: str = ip
        self._port: int = port
        self._verbose: bool = verbose

        # Device info
        self.signals: SignalList = SignalList()
        self._device_name: bytes = device_name.encode() if isinstance(device_name, str) else device_name
        self._device_hw_version: bytes = device_hw_version.encode() if isinstance(device_hw_version, str) else device_hw_version
        self._device_fw_version: bytes = device_fw_version.encode() if isinstance(device_fw_version, str) else device_fw_version

        # Protocol state
        self._timed_activated: bool = False
        self._fixed_interval_ms: int = INTERVAL_CLIENT
        self._timer: _IntervalTimer = _IntervalTimer()
        self._command_handlers: "dict[str, Callable[..., Any]]" = {}
        self._read_callback: "Callable[..., Any] | None" = None
        self._connect_callback: "Callable[[int], Any] | None" = None
        self._disconnect_callback: "Callable[[int], Any] | None" = None
        self._before_write_callback: "Callable[[], Any] | None" = None
        self._server_restarted: bool = True
        self._restart_flag_pending: bool = True
        self._tcp: ClientManager = ClientManager(self, verbose)
        self._closed: bool = False
        self._timestamp_mode: int = TIMESTAMP_NONE
        self._schema_hash: int = 0
        self._started: bool = False
        self._start_time_us: int = 0
        self._start_ticks: int = 0

        # Epoch offset for UNIX timestamps (MicroPython may use 2000 epoch)
        self._epoch_offset_us: int = 0

    # ================================================================
    # Lifecycle
    # ================================================================

    def start(self) -> None:
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
        self._start_time_us: int = _now_us(self._epoch_offset_us)
        self._start_ticks: int = _ticks_ms()
        self._update_schema_hash()

        # Activate fixed interval if set
        if self._fixed_interval_ms >= 0:
            self._timed_activated = True
            self._timer.activate(self._fixed_interval_ms)

        if self._verbose:
            print("blaecktcmpy v{} - Listening on {}:{}".format(
                __version__, self._ip, self._port
            ))

    def close(self) -> None:
        """Close all connections."""
        if self._closed:
            return
        self._closed = True
        self._tcp.close()
        if self._verbose:
            print("Server closed")

    def __enter__(self) -> "BlaeckTCmPy":
        return self

    def __exit__(self, *args: "Any") -> None:
        self.close()

    def _detect_epoch(self) -> None:
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

    def add_signal(self, signal_or_name: "Signal | str", datatype: str = "", value: "Any" = 0) -> "Signal":
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

    def add_signals(self, signals: "list[Signal]") -> None:
        """Add multiple signals at once."""
        for sig in signals:
            self.add_signal(sig)

    def delete_signals(self) -> None:
        """Remove all signals."""
        self.signals.clear()
        if self._started:
            self._update_schema_hash()

    def _resolve_signal(self, key: "str | int") -> int:
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

    def write(self, key: "str | int", value: "Any", msg_id: int = 1, unix_timestamp: "float | int | None" = None) -> None:
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

    def update(self, key: "str | int", value: "Any") -> None:
        """Update a signal's value and mark it as updated (no send)."""
        idx = self._resolve_signal(key)
        self.signals[idx].value = value
        self.signals[idx].updated = True

    def mark_signal_updated(self, key: "str | int") -> None:
        """Mark a signal as updated without changing its value."""
        idx = self._resolve_signal(key)
        self.signals[idx].updated = True

    def mark_all_signals_updated(self) -> None:
        """Mark all signals as updated."""
        for i in range(len(self.signals)):
            self.signals[i].updated = True

    def clear_all_update_flags(self) -> None:
        """Clear the updated flag on all signals."""
        for i in range(len(self.signals)):
            self.signals[i].updated = False

    @property
    def has_updated_signals(self) -> bool:
        """True if any signal is marked as updated."""
        for i in range(len(self.signals)):
            if self.signals[i].updated:
                return True
        return False

    def write_all_data(self, msg_id: int = 1, unix_timestamp: "float | int | None" = None) -> None:
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

    def write_updated_data(self, msg_id: int = 1, unix_timestamp: "float | int | None" = None) -> None:
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

    def timed_write_all_data(self, msg_id: "int | None" = None, unix_timestamp: "float | int | None" = None) -> bool:
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

    def timed_write_updated_data(self, msg_id: "int | None" = None, unix_timestamp: "float | int | None" = None) -> bool:
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

    def tick(self, msg_id: "int | None" = None) -> bool:
        """Main loop tick - read commands and send all data on timer.

        Call this repeatedly in your main loop.
        Returns True if timed data was sent.
        """
        self.read()
        return self.timed_write_all_data(msg_id)

    def tick_updated(self, msg_id: "int | None" = None) -> bool:
        """Main loop tick - read commands and send only updated data.

        Returns True if timed data was sent.
        """
        self.read()
        return self.timed_write_updated_data(msg_id)

    # ================================================================
    # Connection & State
    # ================================================================

    @property
    def connected(self) -> bool:
        """True if any client is connected."""
        return bool(self._tcp._clients)

    @property
    def commanding_client(self):
        """The client socket that sent the most recent command, or None."""
        return self._tcp._commanding_client

    @property
    def start_time(self) -> float:
        """Wall-clock time when start() was called (seconds since Unix epoch)."""
        return self._start_time_us / 1_000_000.0

    @property
    def elapsed_ms(self) -> int:
        """Milliseconds elapsed since start() was called (high resolution)."""
        return _ticks_diff(_ticks_ms(), self._start_ticks)

    @property
    def data_clients(self) -> "set[int]":
        """Set of client IDs that receive data frames."""
        return self._tcp.data_clients

    @data_clients.setter
    def data_clients(self, value: "set[int]") -> None:
        self._tcp.data_clients = value

    @property
    def local_interval_ms(self) -> int:
        """Local signal timed data interval mode.

        >= 0: Lock at given rate (ms). Client ACTIVATE/DEACTIVATE ignored.
        INTERVAL_OFF (-1): Timed data off.
        INTERVAL_CLIENT (-2): Client controlled (default).
        """
        return self._fixed_interval_ms

    @local_interval_ms.setter
    def local_interval_ms(self, value: int) -> None:
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
    def timestamp_mode(self) -> int:
        """Timestamp mode: NONE (0), MICROS (1), or UNIX (2)."""
        return self._timestamp_mode

    @timestamp_mode.setter
    def timestamp_mode(self, value: int) -> None:
        if value not in (TIMESTAMP_NONE, TIMESTAMP_MICROS, TIMESTAMP_UNIX):
            raise ValueError("Invalid timestamp_mode: {}".format(value))
        self._timestamp_mode = value

    # ================================================================
    # Callbacks (decorator API)
    # ================================================================

    def on_command(self, command: "str | None" = None) -> "Callable[..., Any]":
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
        def decorator(func: "Callable[..., Any]") -> "Callable[..., Any]":
            if command is None:
                self._read_callback = func
            else:
                self._command_handlers[command] = func
            return func
        return decorator

    def on_before_write(self) -> "Callable[..., Any]":
        """Decorator for callback that fires before data is written.

        Example:
            @bltcp.on_before_write()
            def refresh():
                bltcp.signals[0].value = read_sensor()
        """
        def decorator(func: "Callable[..., Any]") -> "Callable[..., Any]":
            self._before_write_callback = func
            return func
        return decorator

    def on_client_connected(self) -> "Callable[..., Any]":
        """Decorator for callback when a client connects.

        Example:
            @bltcp.on_client_connected()
            def on_connect(client_id):
                print("Client", client_id, "connected")
        """
        def decorator(func: "Callable[..., Any]") -> "Callable[..., Any]":
            self._connect_callback = func
            return func
        return decorator

    def on_client_disconnected(self) -> "Callable[..., Any]":
        """Decorator for callback when a client disconnects.

        Example:
            @bltcp.on_client_disconnected()
            def on_disconnect(client_id):
                print("Client", client_id, "left")
        """
        def decorator(func: "Callable[..., Any]") -> "Callable[..., Any]":
            self._disconnect_callback = func
            return func
        return decorator

    # ================================================================
    # Command Processing
    # ================================================================

    def read(self) -> None:
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

    def _dispatch_protocol_command(self, command: str, params: "list[str]", conn: "Any") -> None:
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

    def _update_client_identity(self, params: "list[str]", conn: "Any") -> None:
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
    def _decode_four_byte(params: "list[str]") -> int:
        """Decode up to 4 parameter bytes into a little-endian integer."""
        result = 0
        for i in range(min(4, len(params))):
            try:
                result += int(params[i]) << (i * 8)
            except ValueError:
                pass
        return result

    def _set_timed_data(self, activated: bool, interval_ms: int = 0) -> None:
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

    def write_symbols(self, msg_id: int = 1) -> None:
        """Send symbol list to connected clients."""
        if not self.connected:
            return
        header = MSG_SYMBOL_LIST + b":" + struct.pack("<I", msg_id) + b":"
        payload = build_symbol_payload(self.signals)
        data = wrap_frame(header + payload)
        self._tcp.send_all(data)

    def write_devices(self, msg_id: int = 1) -> None:
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

    def _build_device_payload(self, client_id: int) -> bytes:
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

    def _build_data_msg(self, header: bytes, start: int = 0, end: int = -1, only_updated: bool = False, timestamp: "int | None" = None, status: int = STATUS_OK, status_payload: bytes = b"\x00\x00\x00\x00") -> bytes:
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

    def _update_schema_hash(self) -> None:
        """Recompute schema hash from all signals."""
        pairs: "list[tuple[str, int]]" = []
        for sig in self.signals:
            code = DATATYPE_TO_CODE.get(sig.datatype, 0)
            pairs.append((sig.signal_name, code))
        self._schema_hash = compute_schema_hash(pairs)

    def _resolve_timestamp(self, unix_timestamp: "float | int | None") -> "int | None":
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

    def _auto_timestamp(self) -> "int | None":
        """Return auto-generated timestamp for current mode, or None."""
        if self._timestamp_mode == TIMESTAMP_UNIX:
            return _now_us(self._epoch_offset_us)
        if self._timestamp_mode == TIMESTAMP_MICROS:
            return _now_us(self._epoch_offset_us) - self._start_time_us
        return None

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("Server not started - call start() first")

    def __repr__(self) -> str:
        n = len(self._tcp._clients)
        active = "active" if self._timed_activated else "inactive"
        return "blaecktcmpy [{} client(s)] [{}] ({} signals)".format(
            n, active, len(self.signals)
        )
