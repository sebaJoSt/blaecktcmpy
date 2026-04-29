"""Sine Generator — Ethernet Shield with static IP.

Serves three sine signals over TCP using the Arduino Giga R1
with an Ethernet shield (W5500) and a static IP address.

Connect Loggbok (or any BlaeckTCP client) to 192.168.1.177:9325.

Wiring: Attach Ethernet shield to Giga R1, connect via cable.
"""

import math
import time
import network
from blaecktcmpy import BlaeckTCmPy

# -- Ethernet Configuration (static IP) --
IP = "192.168.1.177"
SUBNET = "255.255.255.0"
GATEWAY = "192.168.1.1"
DNS = "192.168.1.1"

EXAMPLE_VERSION = "1.0"

# -- Initialize Ethernet --
eth = network.LAN()
eth.active(True)
eth.ifconfig((IP, SUBNET, GATEWAY, DNS))

print("Waiting for Ethernet link...", end="")
while not eth.isconnected():
    time.sleep(0.5)
    print(".", end="")
print(" Link up!")
print("IP:", eth.ifconfig()[0])

# -- Create BlaeckTCmPy Server --
bltcp = BlaeckTCmPy(
    ip=IP,
    port=9325,
    device_name="Sine Generator",
    device_hw_version="Arduino Giga R1 + Ethernet",
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
