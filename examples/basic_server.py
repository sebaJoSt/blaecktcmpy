"""Basic BlaeckTCmPy server example for Arduino Giga R1.

Streams a counter and a sine wave to any connected BlaeckTCP client
(e.g. Loggbok).

Wiring: None required (uses onboard WiFi)
"""

import math
import time
import network
from blaecktcmpy import BlaeckTCmPy, Signal

# -- WiFi Configuration --
SSID = "YOUR_SSID"
PASSWORD = "YOUR_PASSWORD"

# -- Connect to WiFi --
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)

print("Connecting to WiFi...", end="")
while not wlan.isconnected():
    time.sleep(0.5)
    print(".", end="")
print(" Connected!")
print("IP:", wlan.ifconfig()[0])

# -- Create BlaeckTCmPy Server --
bltcp = BlaeckTCmPy(
    ip=wlan.ifconfig()[0],
    port=9325,
    device_name="Giga R1",
    device_hw_version="Arduino Giga R1 WiFi",
    device_fw_version="1.0",
)

# -- Add Signals --
bltcp.add_signal("counter", "unsigned int", 0)
bltcp.add_signal("sine", "float", 0.0)
bltcp.add_signal("led_state", "bool", False)

# -- Set interval (or leave as INTERVAL_CLIENT for client control) --
# bltcp.local_interval_ms = 100  # Fixed 100ms interval


# -- Register command handler --
@bltcp.on_command("SET_LED")
def handle_led(state):
    led_val = int(state) if state else 0
    bltcp.signals["led_state"].value = bool(led_val)
    print("LED set to:", led_val)


# -- Before-write callback to refresh sensor values --
@bltcp.on_before_write()
def refresh_signals():
    counter = bltcp.signals["counter"].value
    bltcp.signals["counter"].value = (counter + 1) % 65536
    bltcp.signals["sine"].value = math.sin(time.ticks_ms() / 1000.0)


# -- Start server --
bltcp.start()

# -- Main loop --
try:
    while True:
        bltcp.tick()
except KeyboardInterrupt:
    bltcp.close()
    print("Done.")
