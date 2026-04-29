"""Signal dataclass for BlaeckTCP typed data."""

import struct


DATATYPE_TO_CODE: "dict[str, int]" = {
    "bool": 0,
    "byte": 1,
    "short": 2,
    "unsigned short": 3,
    "int": 6,
    "unsigned int": 7,
    "long": 6,
    "unsigned long": 7,
    "float": 8,
    "double": 9,
}

DATATYPE_SIZES: "dict[str, int]" = {
    "bool": 1,
    "byte": 1,
    "short": 2,
    "unsigned short": 2,
    "int": 4,
    "unsigned int": 4,
    "long": 4,
    "unsigned long": 4,
    "float": 4,
    "double": 8,
}

# struct format strings for each datatype (little-endian)
_STRUCT_FORMATS: "dict[str, str]" = {
    "bool": "<B",
    "byte": "<B",
    "short": "<h",
    "unsigned short": "<H",
    "int": "<i",
    "unsigned int": "<I",
    "long": "<i",
    "unsigned long": "<I",
    "float": "<f",
    "double": "<d",
}

SIGNED_TYPES: "set[str]" = {"short", "int", "long"}
FLOAT_TYPES: "set[str]" = {"float", "double"}


class Signal:
    """Represents a BlaeckTCP signal with typed data."""

    signal_name: str
    datatype: str
    updated: bool

    def __init__(self, signal_name: str, datatype: str, value: "int | float" = 0, updated: bool = False) -> None:
        self.signal_name = signal_name
        self.datatype = datatype
        self.updated = updated
        if datatype not in DATATYPE_TO_CODE:
            raise ValueError("Invalid datatype: " + datatype)
        self._value: "int | float | bool" = self._normalize_value(value)

    @property
    def value(self) -> "int | float | bool":
        return self._value

    @value.setter
    def value(self, value: "int | float") -> None:
        self._value = self._normalize_value(value)

    def _normalize_value(self, value: "int | float") -> "int | float | bool":
        if self.datatype in FLOAT_TYPES:
            return float(value)

        if isinstance(value, bool):
            normalized = int(value)
        elif isinstance(value, int):
            normalized = value
        elif isinstance(value, float):
            if value != int(value):
                raise ValueError(
                    "Invalid value for {} signal '{}': {}".format(
                        self.datatype, self.signal_name, value
                    )
                )
            normalized = int(value)
        else:
            raise ValueError(
                "Invalid value for {} signal '{}': {}".format(
                    self.datatype, self.signal_name, value
                )
            )

        min_val, max_val = self._integer_range()
        if not (min_val <= normalized <= max_val):
            raise ValueError(
                "Value {} out of range for {} signal '{}' [{}, {}]".format(
                    normalized, self.datatype, self.signal_name, min_val, max_val
                )
            )

        if self.datatype == "bool":
            return bool(normalized)
        return normalized

    def _integer_range(self) -> "tuple[int, int]":
        if self.datatype == "bool":
            return 0, 1
        bits = DATATYPE_SIZES[self.datatype] * 8
        if self.datatype in SIGNED_TYPES:
            return -(1 << (bits - 1)), (1 << (bits - 1)) - 1
        return 0, (1 << bits) - 1

    def to_bytes(self) -> bytes:
        """Convert signal value to bytes using struct.pack."""
        fmt = _STRUCT_FORMATS[self.datatype]
        if self.datatype == "bool":
            return struct.pack(fmt, 1 if self._value else 0)
        return struct.pack(fmt, self._value)

    def get_dtype_byte(self) -> bytes:
        """Get the datatype code as a single byte."""
        return bytes([DATATYPE_TO_CODE[self.datatype]])

    def __repr__(self) -> str:
        return "{}: {} = {}".format(self.signal_name, self.datatype, self._value)


class SignalList:
    """A list of signals with name-based access.

    Supports indexing by integer or signal name.
    """

    def __init__(self) -> None:
        self._signals: "list[Signal]" = []
        self._name_cache: "dict[str, int] | None" = None

    def _invalidate_cache(self) -> None:
        self._name_cache = None

    def _ensure_cache(self) -> None:
        if self._name_cache is None:
            self._name_cache = {}
            for i, sig in enumerate(self._signals):
                self._name_cache[sig.signal_name] = i

    def __len__(self) -> int:
        return len(self._signals)

    def __getitem__(self, key: "str | int") -> "Signal":
        if isinstance(key, str):
            self._ensure_cache()
            assert self._name_cache is not None
            idx = self._name_cache.get(key)
            if idx is None:
                raise KeyError("No signal named '{}'".format(key))
            return self._signals[idx]
        return self._signals[key]

    def __setitem__(self, key: int, value: "Signal") -> None:
        self._signals[key] = value
        self._invalidate_cache()

    def __delitem__(self, key: "int | slice") -> None:
        if isinstance(key, slice):
            del self._signals[key]
        else:
            del self._signals[key]
        self._invalidate_cache()

    def __iter__(self):
        return iter(self._signals)

    def append(self, item: "Signal") -> None:
        self._signals.append(item)
        self._invalidate_cache()

    def extend(self, items: "list[Signal]") -> None:
        self._signals.extend(items)
        self._invalidate_cache()

    def insert(self, index: int, item: "Signal") -> None:
        self._signals.insert(index, item)
        self._invalidate_cache()

    def remove(self, item: "Signal") -> None:
        self._signals.remove(item)
        self._invalidate_cache()

    def pop(self, index: int = -1) -> "Signal":
        result = self._signals.pop(index)
        self._invalidate_cache()
        return result

    def clear(self) -> None:
        self._signals.clear()
        self._invalidate_cache()

    def index_of(self, name: str) -> "int | None":
        """Return the index of a signal by name, or None if not found."""
        self._ensure_cache()
        assert self._name_cache is not None
        return self._name_cache.get(name)

