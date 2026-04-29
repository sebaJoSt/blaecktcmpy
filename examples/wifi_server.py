"""Sine Generator — WiFi.

Serves three sine signals over TCP using the Arduino Giga R1's
built-in WiFi. Works with the stock MicroPython firmware (no
custom build required).

Connect Loggbok (or any BlaeckTCP client) to the printed IP on port 9325.
"""

import math
import time
import network
from blaecktcmpy import BlaeckTCmPy

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
    device_name="Sine Generator",
    device_hw_version="Arduino Giga R1 WiFi",
    device_fw_version=EXAMPLE_VERSION,
)

for i in range(1, 4):
    bltcp.add_signal("Sine_{}".format(i), "float")

bltcp.start()

try:
    while True:
        elapsed_ms = bltcp.elapsed_ms
        value = math.sin(elapsed_ms * 0.001)
        for s in bltcp.signals:
            s.value = value
        bltcp.tick()
        time.sleep_ms(1)
except KeyboardInterrupt:
    bltcp.close()
