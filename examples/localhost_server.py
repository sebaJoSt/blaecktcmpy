"""Localhost example for testing blaecktcmpy on a PC (CPython).

Run this script, then connect with Loggbok to 127.0.0.1:9325.
No hardware or WiFi required.

Usage:
    pip install -e .
    python examples/localhost_server.py
"""

import math
import time

from blaecktcmpy import BlaeckTCmPy, Signal

# -- Create BlaeckTCmPy Server --
bltcp = BlaeckTCmPy(
    ip="127.0.0.1",
    port=9325,
    device_name="PC Test",
    device_hw_version="Desktop",
    device_fw_version="1.0",
)

# -- Add Signals --
bltcp.add_signal("counter", "unsigned int", 0)
bltcp.add_signal("sine", "float", 0.0)
bltcp.add_signal("led_state", "bool", False)


# -- Register command handler --
@bltcp.on_command("SET_LED")
def handle_led(state):
    led_val = int(state) if state else 0
    bltcp.signals["led_state"].value = bool(led_val)
    print("LED set to:", led_val)


# -- Before-write callback to refresh values --
@bltcp.on_before_write()
def refresh_signals():
    counter = bltcp.signals["counter"].value
    bltcp.signals["counter"].value = (counter + 1) % 65536
    bltcp.signals["sine"].value = math.sin(time.time())


# -- Start server --
bltcp.start()

# -- Main loop --
print("Press Ctrl+C to stop")
try:
    while True:
        bltcp.tick()
        time.sleep(0.001)  # Prevent busy-loop on CPython
except KeyboardInterrupt:
    bltcp.close()
    print("Done.")
