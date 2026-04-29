"""BlaeckTCP protocol message encoding for MicroPython."""

import struct
import binascii


# Message type keys
MSG_SYMBOL_LIST = b"\xb0"
MSG_DATA = b"\xd2"
MSG_DEVICES = b"\xb6"

# Status byte values for data frames
STATUS_OK = 0x00
STATUS_UPSTREAM_LOST = 0x80
STATUS_UPSTREAM_RECONNECTED = 0x81

# MasterSlaveConfig byte values
MSC_MASTER = b"\x01"
MSC_SLAVE = b"\x02"


def build_header(msg_key, msg_id):
    """Build the common message header: MSGKEY : MSGID(4) :"""
    return msg_key + b":" + struct.pack("<I", msg_id) + b":"


def wrap_frame(content):
    """Wrap encoded content in BlaeckTCP frame markers."""
    return b"<BLAECK:" + content + b"/BLAECK>\r\n"


def build_data_frame(
    header,
    signals,
    start=0,
    end=-1,
    schema_hash=0,
    restart_flag=False,
    timestamp_mode=0,
    timestamp=None,
    only_updated=False,
    status=STATUS_OK,
    status_payload=b"\x00\x00\x00\x00",
):
    """Build a D2 data frame with CRC32 checksum."""
    if end == -1:
        end = len(signals) - 1

    flag_byte = b"\x01" if restart_flag else b"\x00"
    hash_bytes = struct.pack("<H", schema_hash)

    if timestamp is not None and timestamp_mode != 0:
        mode_byte = bytes([timestamp_mode])
        meta = (
            flag_byte
            + b":"
            + hash_bytes
            + b":"
            + mode_byte
            + struct.pack("<Q", timestamp)
            + b":"
        )
    else:
        meta = flag_byte + b":" + hash_bytes + b":" + b"\x00" + b":"

    payload = bytearray()
    for idx in range(start, end + 1):
        sig = signals[idx]
        if only_updated and not sig.updated:
            continue
        payload += struct.pack("<H", idx) + sig.to_bytes()
        if only_updated:
            sig.updated = False

    frame_no_crc = (
        header + meta + bytes(payload) + bytes([status]) + status_payload
    )
    crc = struct.pack("<I", binascii.crc32(frame_no_crc) & 0xFFFFFFFF)
    return frame_no_crc + crc


def build_symbol_payload(signals, master_slave_config=b"\x00", slave_id=b"\x00"):
    """Build the symbol-list payload for simple server mode."""
    result = bytearray()
    for sig in signals:
        result += (
            master_slave_config
            + slave_id
            + sig.signal_name.encode()
            + b"\0"
            + sig.get_dtype_byte()
        )
    return bytes(result)


def encode_device_entry(msc, slave_id, name, hw, fw, lib_ver, lib_name, restarted, device_type, parent):
    """Encode a single B6 device entry."""
    return (
        msc
        + slave_id
        + name
        + b"\0"
        + hw
        + b"\0"
        + fw
        + b"\0"
        + lib_ver
        + b"\0"
        + lib_name
        + b"\0"
        + restarted
        + b"\0"
        + device_type
        + b"\0"
        + parent
        + b"\0"
    )


def build_client_trailer(client_id, data_clients, client_meta):
    """Build B6 client trailer: ClientNo, DataEnabled, ClientName, ClientType."""
    meta = client_meta.get(client_id, {})
    return (
        str(client_id).encode()
        + b"\0"
        + (b"1" if client_id in data_clients else b"0")
        + b"\0"
        + meta.get("name", "").encode()
        + b"\0"
        + meta.get("type", "unknown").encode()
        + b"\0"
    )


def compute_schema_hash(pairs):
    """Compute CRC16-CCITT schema hash from (name, datatype_code) pairs.

    Uses CRC-CCITT with init=0 (same as binascii.crc_hqx on CPython).
    Compatible with BlaeckTCP's schema hash algorithm.
    """
    data = bytearray()
    for name, code in pairs:
        data += name.encode()
        data.append(code)
    # CRC-CCITT with init=0 (polynomial 0x1021)
    crc = 0x0000
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc
