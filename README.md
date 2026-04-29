# blaecktcmpy

A MicroPython BlaeckTCP server library for real-time streaming of named, typed signals over TCP.

Wire-compatible with [Loggbok](https://loggbok.net) and all BlaeckTCP clients.

## Supported Hardware

- Arduino Giga R1 WiFi (primary target)
- Any MicroPython board with WiFi/Ethernet and `socket`/`select` support

## Installation

### Using mpremote

```bash
mpremote mip install github:sebaJoSt/blaecktcmpy
```

### Manual

Copy the `blaecktcmpy/` folder to your board's `/lib/` directory.

## Quick Start

```python
import time
import network
from blaecktcmpy import BlaeckTCmPy

# Connect to WiFi
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect("SSID", "PASSWORD")
while not wlan.isconnected():
    time.sleep(0.5)

# Create server
bltcp = BlaeckTCmPy(
    ip=wlan.ifconfig()[0],
    port=9325,
    device_name="My Device",
)

# Add signals
bltcp.add_signal("temperature", "float", 0.0)
bltcp.add_signal("counter", "unsigned int", 0)

# Optional: update values before each send
@bltcp.on_before_write()
def refresh():
    bltcp.signals["counter"].value += 1

# Start and run
bltcp.start()
while True:
    bltcp.tick()
```

Open Loggbok, connect to your device's IP on port 9325, and you'll see live data.

## API Reference

### BlaeckTCmPy(ip, port, device_name, device_hw_version="", device_fw_version="1.0", verbose=True)

Create a server instance.

### Lifecycle

| Method | Description |
|--------|-------------|
| `start()` | Bind socket and start listening |
| `close()` | Close all connections |

### Signal Management

| Method | Description |
|--------|-------------|
| `add_signal(name, datatype, value)` | Add a signal (returns Signal) |
| `add_signals(signals)` | Add multiple Signal objects |
| `delete_signals()` | Remove all signals |
| `signals[name]` or `signals[index]` | Access signals |

**Supported datatypes:** `bool`, `byte`, `short`, `unsigned short`, `int`, `unsigned int`, `long`, `unsigned long`, `float`, `double`

### Data Writing

| Method | Description |
|--------|-------------|
| `write(key, value, msg_id=1)` | Update + send single signal immediately |
| `update(key, value)` | Update value + mark updated (no send) |
| `write_all_data(msg_id=1)` | Send all signals now |
| `write_updated_data(msg_id=1)` | Send only updated signals now |
| `timed_write_all_data(msg_id)` | Send all if timer elapsed |
| `timed_write_updated_data(msg_id)` | Send updated if timer elapsed |
| `tick(msg_id)` | read() + timed_write_all_data() |
| `tick_updated(msg_id)` | read() + timed_write_updated_data() |

### Update Flags

| Method/Property | Description |
|--------|-------------|
| `mark_signal_updated(key)` | Mark one signal updated |
| `mark_all_signals_updated()` | Mark all updated |
| `clear_all_update_flags()` | Clear all update flags |
| `has_updated_signals` | True if any signal is updated |

### Configuration

| Property | Description |
|--------|-------------|
| `local_interval_ms` | Interval mode: `>=0` (fixed ms), `INTERVAL_OFF` (-1), `INTERVAL_CLIENT` (-2, default) |
| `timestamp_mode` | `TIMESTAMP_NONE` (0, default) or `TIMESTAMP_UNIX` (2) |
| `connected` | True if any client is connected |
| `data_clients` | Set of client IDs receiving data |

### Callbacks (Decorators)

```python
@bltcp.on_command("MY_CMD")
def handle(param1, param2):
    ...

@bltcp.on_command()  # catch-all
def handle_any(command, *params):
    ...

@bltcp.on_before_write()
def refresh():
    ...

@bltcp.on_client_connected()
def on_connect(client_id):
    ...

@bltcp.on_client_disconnected()
def on_disconnect(client_id):
    ...
```

## Protocol Compatibility

blaecktcmpy implements the same BlaeckTCP wire protocol as:
- [BlaeckTCP](https://github.com/sebaJoSt/BlaeckTCP) (Arduino C++ library)
- [blaecktcpy](https://github.com/sebaJoSt/blaecktcpy) (CPython package)

Frame format: `<BLAECK:…/BLAECK>\r\n` with B0 (symbols), B6 (devices), and D2 (data + CRC32) message types.

## License

MIT
