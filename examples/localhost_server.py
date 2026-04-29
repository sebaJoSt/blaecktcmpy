"""Sine Generator — serves three sine signals over TCP.

The simplest blaecktcmpy example. Run this, then connect Loggbok
(or any BlaeckTCP client) to 127.0.0.1:9325 to see live data.

Usage:
    pip install -e .
    python examples/localhost_server.py
"""

import math
import time

from blaecktcmpy import BlaeckTCmPy

EXAMPLE_VERSION = "1.0"

bltcp = BlaeckTCmPy(
    ip="127.0.0.1",
    port=9325,
    device_name="Sine Generator",
    device_hw_version="Python Script",
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
        time.sleep(0.001)
except KeyboardInterrupt:
    bltcp.close()

