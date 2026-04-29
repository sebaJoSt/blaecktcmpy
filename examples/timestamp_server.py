"""Timestamp Mode — WiFi.

Demonstrates how to enable the micros timestamp mode so that each
data frame includes elapsed microseconds since start.  This does
not require a synced RTC — it works like Arduino's micros().

Connect a BlaeckTCP client to the printed IP on port 9325.
"""

import math
import time
import network
from blaecktcmpy import BlaeckTCmPy, TimestampMode

# -- WiFi Configuration --
SSID = "YOUR_WIFI_SSID"
PASSWORD = "YOUR_WIFI_PASSWORD"

EXAMPLE_VERSION = "1.0"

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
    device_name="Timestamp Example",
    device_hw_version="Arduino Giga R1 WiFi",
    device_fw_version=EXAMPLE_VERSION,
)

# Enable micros timestamp mode (microseconds since start)
bltcp.timestamp_mode = TimestampMode.MICROS

bltcp.add_signal("Sine_1", "float")

bltcp.start()

try:
    while True:
        elapsed_ms = bltcp.elapsed_ms
        bltcp.signals["Sine_1"].value = math.sin(elapsed_ms * 0.001)
        bltcp.tick()
        time.sleep_ms(1)
except KeyboardInterrupt:
    bltcp.close()
