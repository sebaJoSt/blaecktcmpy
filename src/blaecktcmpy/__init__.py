"""blaecktcmpy - MicroPython BlaeckTCP server library."""

__version__ = "1.0.0"

from .signal import Signal as Signal, SignalList as SignalList
from .server import (
    BlaeckTCmPy as BlaeckTCmPy,
    IntervalMode as IntervalMode,
    TimestampMode as TimestampMode,
    INTERVAL_CLIENT as INTERVAL_CLIENT,
    INTERVAL_OFF as INTERVAL_OFF,
    TIMESTAMP_NONE as TIMESTAMP_NONE,
    TIMESTAMP_UNIX as TIMESTAMP_UNIX,
)
