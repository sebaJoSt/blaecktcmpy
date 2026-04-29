"""Sine Generator — serves three sine signals over TCP.

The simplest blaecktcmpy example for Arduino Giga R1.
Connect Loggbok (or any BlaeckTCP client) to the device's
IP on port 9325 to see live data.

Wiring: None required (uses onboard WiFi)
"""

import math
import time
import network
from blaecktcmpy import BlaeckTCmPy

# -- WiFi Configuration --
SSID = "YOUR_SSID"
PASSWORD = "YOUR_PASSWORD"
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
    device_name="Sine Generator",
    device_hw_version="Arduino Giga R1 WiFi",
    device_fw_version=EXAMPLE_VERSION,
)

for i in range(1, 4):
    bltcp.add_signal("Sine_{}".format(i), "float")

bltcp.start()

try:
    while True:
        elapsed_ms = (time.time() - bltcp.start_time) * 1000
        value = math.sin(elapsed_ms * 0.001)
        for s in bltcp.signals:
            s.value = value
        bltcp.tick()
except KeyboardInterrupt:
    bltcp.close()

