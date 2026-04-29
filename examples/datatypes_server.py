"""Datatype Test — WiFi.

Exercises all supported BlaeckTCP datatypes with min/max/special values.
Useful for verifying protocol encoding — connect a BlaeckTCP client and check
that all values display correctly.

Connect a BlaeckTCP client to the printed IP on port 9325.
"""

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
    device_name="Datatype Test",
    device_hw_version="Arduino Giga R1 WiFi",
    device_fw_version=EXAMPLE_VERSION,
)

# Add signals exercising all supported datatypes at min/max/special values
bltcp.add_signal("Bool_false", "bool", False)
bltcp.add_signal("Bool_true", "bool", True)
bltcp.add_signal("Byte_min", "byte", 0)
bltcp.add_signal("Byte_max", "byte", 255)
bltcp.add_signal("Short_min", "short", -32768)
bltcp.add_signal("Short_max", "short", 32767)
bltcp.add_signal("UShort_min", "unsigned short", 0)
bltcp.add_signal("UShort_max", "unsigned short", 65535)
bltcp.add_signal("Int_min", "int", -2147483648)
bltcp.add_signal("Int_max", "int", 2147483647)
bltcp.add_signal("UInt_min", "unsigned int", 0)
bltcp.add_signal("UInt_max", "unsigned int", 4294967295)
bltcp.add_signal("Long_min", "long", -2147483648)
bltcp.add_signal("Long_max", "long", 2147483647)
bltcp.add_signal("ULong_min", "unsigned long", 0)
bltcp.add_signal("ULong_max", "unsigned long", 4294967295)
bltcp.add_signal("Float_min", "float", -3.4028235e38)
bltcp.add_signal("Float_max", "float", 3.4028235e38)
bltcp.add_signal("Float_NaN", "float", float("nan"))
bltcp.add_signal("Float_Inf", "float", float("inf"))
bltcp.add_signal("Float_NegInf", "float", float("-inf"))
bltcp.add_signal("Double_min", "double", -3.4028235e38)
bltcp.add_signal("Double_max", "double", 3.4028235e38)
bltcp.add_signal("Double_NaN", "double", float("nan"))
bltcp.add_signal("Double_Inf", "double", float("inf"))
bltcp.add_signal("Double_NegInf", "double", float("-inf"))

bltcp.start()

try:
    while True:
        bltcp.tick()
        time.sleep_ms(1)
except KeyboardInterrupt:
    bltcp.close()
