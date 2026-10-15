"""
TeslaCam SEI Metadata & Stream Encryption Inspector
Extracts and parses embedded Supplemental Enhancement Information (SEI) vehicle telemetry
and detects MP4 stream encryption (CENC / encv) from Tesla Dashcam and Sentry video files.

Zero external native dependencies required; pure Python implementation compatible with
firmware 2025.44.25+ embedded dashcam.proto payloads.
"""

import os
import struct
from typing import Any, Dict, List, Optional, Tuple


def _decode_varint(data: bytes, offset: int) -> Tuple[int, int]:
    """Decodes a protobuf varint from data starting at offset."""
    res = 0
    shift = 0
    while offset < len(data):
        b = data[offset]
        offset += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            return res, offset
        shift += 7
    return res, offset


def _parse_protobuf(data: bytes) -> Dict[int, Any]:
    """Parses raw protobuf wire format into a field-number mapped dict."""
    offset = 0
    fields: Dict[int, Any] = {}
    while offset < len(data):
        try:
            key, offset = _decode_varint(data, offset)
        except Exception:
            break
        field_num = key >> 3
        wire_type = key & 0x07
        if field_num == 0 or offset > len(data):
            break

        if wire_type == 0:  # Varint
            val, offset = _decode_varint(data, offset)
            fields[field_num] = val
        elif wire_type == 1:  # 64-bit fixed / double
            if offset + 8 > len(data):
                break
            fields[field_num] = data[offset:offset + 8]
            offset += 8
        elif wire_type == 2:  # Length-delimited
            length, offset = _decode_varint(data, offset)
            if offset + length > len(data):
                break
            fields[field_num] = data[offset:offset + length]
            offset += length
        elif wire_type == 5:  # 32-bit fixed / float
            if offset + 4 > len(data):
                break
            fields[field_num] = data[offset:offset + 4]
            offset += 4
        else:
            break
    return fields


def _decode_sei_telemetry(payload: bytes) -> Optional[Dict[str, Any]]:
    """Decodes Tesla SeiMetadata protobuf fields into a friendly dict."""
    fields = _parse_protobuf(payload)
    if not fields or 1 not in fields:
        return None

    telemetry: Dict[str, Any] = {
        "version": fields.get(1, 1),
    }

    # Gear enum: 0=PARK, 1=DRIVE, 2=REVERSE, 3=NEUTRAL
    gear_map = {0: "PARK", 1: "DRIVE", 2: "REVERSE", 3: "NEUTRAL"}
    if 2 in fields and isinstance(fields[2], int):
        telemetry["gear"] = gear_map.get(fields[2], f"UNKNOWN({fields[2]})")

    if 3 in fields and isinstance(fields[3], int):
        telemetry["frame_seq_no"] = fields[3]

    # Speed (mps -> km/h and mph)
    if 4 in fields:
        raw = fields[4]
        if isinstance(raw, bytes) and len(raw) == 4:
            mps = struct.unpack("<f", raw)[0]
            telemetry["speed_mps"] = round(mps, 2)
            telemetry["speed_kmh"] = round(mps * 3.6, 1)
            telemetry["speed_mph"] = round(mps * 2.23694, 1)

    # Accelerator pedal (0-100%)
    if 5 in fields:
        raw = fields[5]
        if isinstance(raw, bytes) and len(raw) == 4:
            telemetry["accelerator_pct"] = round(struct.unpack("<f", raw)[0], 1)

    # Steering wheel angle (degrees)
    if 6 in fields:
        raw = fields[6]
        if isinstance(raw, bytes) and len(raw) == 4:
            telemetry["steering_deg"] = round(struct.unpack("<f", raw)[0], 1)

    if 7 in fields:
        telemetry["blinker_left"] = bool(fields[7])
    if 8 in fields:
        telemetry["blinker_right"] = bool(fields[8])
    if 9 in fields:
        telemetry["brake_applied"] = bool(fields[9])

    # Autopilot / FSD state
    ap_map = {0: "Unavailable", 1: "Available", 2: "Standby", 3: "Active / Engaged", 4: "Hands-on Warning", 5: "Abort"}
    if 10 in fields:
        raw = fields[10]
        if isinstance(raw, int):
            telemetry["autopilot_state"] = ap_map.get(raw, f"State({raw})")
            telemetry["autopilot_active"] = (raw == 3)

    # GPS coordinates
    if 11 in fields:
        raw = fields[11]
        if isinstance(raw, bytes):
            if len(raw) == 8:
                telemetry["latitude"] = round(struct.unpack("<d", raw)[0], 6)
            elif len(raw) == 4:
                telemetry["latitude"] = round(struct.unpack("<f", raw)[0], 6)
    if 12 in fields:
        raw = fields[12]
        if isinstance(raw, bytes):
            if len(raw) == 8:
                telemetry["longitude"] = round(struct.unpack("<d", raw)[0], 6)
            elif len(raw) == 4:
                telemetry["longitude"] = round(struct.unpack("<f", raw)[0], 6)

    if 13 in fields:
        raw = fields[13]
        if isinstance(raw, bytes) and len(raw) == 4:
            telemetry["heading_deg"] = round(struct.unpack("<f", raw)[0], 1)

    return telemetry


def _parse_sei_rbsp(sei_bytes: bytes) -> List[bytes]:
    """Parses SEI NAL unit RBSP (removing emulation prevention bytes) and extracts payload(s)."""
    # Remove emulation prevention 0x03 bytes: 00 00 03 -> 00 00
    rbsp = bytearray()
    i = 0
    while i < len(sei_bytes):
        if i + 2 < len(sei_bytes) and sei_bytes[i] == 0 and sei_bytes[i + 1] == 0 and sei_bytes[i + 2] == 3:
            rbsp.append(0)
            rbsp.append(0)
            i += 3
        else:
            rbsp.append(sei_bytes[i])
            i += 1

    payloads = []
    idx = 0
    while idx < len(rbsp):
        payload_type = 0
        while idx < len(rbsp) and rbsp[idx] == 0xFF:
            payload_type += 255
            idx += 1
        if idx >= len(rbsp):
            break
        payload_type += rbsp[idx]
        idx += 1

        payload_size = 0
        while idx < len(rbsp) and rbsp[idx] == 0xFF:
            payload_size += 255
            idx += 1
        if idx >= len(rbsp):
            break
        payload_size += rbsp[idx]
        idx += 1

        if idx + payload_size > len(rbsp):
            break
        payload_data = bytes(rbsp[idx:idx + payload_size])
        payloads.append((payload_type, payload_data))
        idx += payload_size

    result = []
    for ptype, pdata in payloads:
        # ITU-T T.35 user data payload
        if ptype == 4:
            # Skip T.35 header bytes (usually country code 1-byte, provider code 2-bytes)
            if len(pdata) > 4:
                result.append(pdata[4:])
            else:
                result.append(pdata)
        elif ptype == 5:
            # Unregistered user data (skip 16-byte UUID)
            if len(pdata) > 16:
                result.append(pdata[16:])
        else:
            result.append(pdata)

    return result


def inspect_mp4_clip(file_path: str, max_scan_bytes: int = 4 * 1024 * 1024) -> Dict[str, Any]:
    """
    Fast, comprehensive inspection of a TeslaCam MP4 clip.
    Scans atoms and initial NAL units to detect codec, encryption, and SEI vehicle telemetry.

    Returns dict:
      {
        "file_path": str,
        "is_valid_mp4": bool,
        "is_encrypted": bool,
        "encryption_scheme": Optional[str],
        "codec": Optional[str],
        "has_sei": bool,
        "telemetry_sample": Optional[dict],
        "status_summary": str
      }
    """
    result: Dict[str, Any] = {
        "file_path": file_path,
        "is_valid_mp4": False,
        "is_encrypted": False,
        "encryption_scheme": None,
        "codec": "Unknown",
        "has_sei": False,
        "telemetry_sample": None,
        "status_summary": "Uninspected",
    }

    if not file_path or not os.path.isfile(file_path):
        result["status_summary"] = "File not found"
        return result

    try:
        file_size = os.path.getsize(file_path)
        if file_size < 32:
            result["status_summary"] = "File too small or empty"
            return result

        with open(file_path, "rb") as fp:
            header = fp.read(min(file_size, max_scan_bytes))

        # Check for MP4 ftyp atom
        if b"ftyp" in header[:32]:
            result["is_valid_mp4"] = True

        # Check for Common Encryption (CENC / encv / sinf / schm)
        if b"encv" in header or b"enca" in header or b"sinf" in header:
            result["is_encrypted"] = True
            if b"cenc" in header:
                result["encryption_scheme"] = "CENC (AES-CTR)"
            elif b"cbcs" in header:
                result["encryption_scheme"] = "CBCS (AES-CBC)"
            else:
                result["encryption_scheme"] = "Standard ISO CENC (encv)"
            result["codec"] = "Encrypted Track (encv)"
            result["status_summary"] = f"🔒 Encrypted ({result['encryption_scheme']})"
            return result

        # Detect video codec from atom sample descriptions
        if b"hvc1" in header or b"hev1" in header:
            result["codec"] = "H.265 / HEVC"
        elif b"avc1" in header or b"avc3" in header:
            result["codec"] = "H.264 / AVC"
        elif b"mp4v" in header:
            result["codec"] = "MPEG-4"

        # Scan for SEI NAL units and decode telemetry
        # Method 1: Scan for Annex B / 4-byte NAL units
        # Method 2: Direct pattern matching for Tesla Protobuf header (\x08\x01 = version 1)
        found_telemetry = None

        # Scan for NAL start sequences or SEI packets in buffer
        idx = 0
        buf_len = len(header)
        while idx < buf_len - 16:
            # Check for Annex B start code 00 00 01 or 00 00 00 01
            nal_start = -1
            header_offset = 0
            if header[idx:idx + 3] == b"\x00\x00\x01":
                nal_start = idx + 3
                header_offset = 3
            elif header[idx:idx + 4] == b"\x00\x00\x00\x01":
                nal_start = idx + 4
                header_offset = 4

            if nal_start != -1 and nal_start < buf_len:
                nal_byte = header[nal_start]
                nal_type_h264 = nal_byte & 0x1F
                nal_type_h265 = (nal_byte >> 1) & 0x3F

                # Check if SEI
                if nal_type_h264 == 6 or nal_type_h265 in (39, 40):
                    # Slice SEI payload up to next start code or 512 bytes
                    sei_slice = header[nal_start + 1: min(buf_len, nal_start + 512)]
                    payloads = _parse_sei_rbsp(sei_slice)
                    for pl in payloads:
                        telem = _decode_sei_telemetry(pl)
                        if telem:
                            found_telemetry = telem
                            break
                    if found_telemetry:
                        break

            # Fast direct scan for Tesla protobuf header pattern (\x08\x01\x10 or \x08\x01\x12)
            if header[idx:idx + 2] == b"\x08\x01" and idx > 4:
                # Potential candidate protobuf message
                candidate = header[idx: min(buf_len, idx + 256)]
                telem = _decode_sei_telemetry(candidate)
                if telem and ("speed_mps" in telem or "gear" in telem or "latitude" in telem):
                    found_telemetry = telem
                    break

            idx += 1

        if found_telemetry:
            result["has_sei"] = True
            result["telemetry_sample"] = found_telemetry
            speed_info = f"{found_telemetry.get('speed_kmh', 0)} km/h"
            gear_info = f"Gear: {found_telemetry.get('gear', 'N/A')}"
            ap_info = f"AP: {found_telemetry.get('autopilot_state', 'Off')}"
            result["status_summary"] = f"✔ SEI Telemetry ({speed_info}, {gear_info}, {ap_info})"
        else:
            result["status_summary"] = f"Standard Stream ({result['codec']}, No SEI)"

    except Exception as e:
        result["status_summary"] = f"Inspection error: {e}"

    return result


def format_stream_banner(info: Dict[str, Any]) -> str:
    """Formats a concise 1-line stream info banner for CLI presentation."""
    if not info:
        return "🎬 Video Stream: Unchecked"

    if info.get("is_encrypted"):
        scheme = info.get("encryption_scheme") or "CENC"
        return f"🔒 Protection: Encrypted Stream ({scheme}) — requires vehicle decryption tools"

    codec = info.get("codec", "H.264")
    if info.get("has_sei"):
        sample = info.get("telemetry_sample", {})
        details = []
        if "speed_kmh" in sample:
            details.append(f"Speed: {sample['speed_kmh']} km/h")
        if "gear" in sample:
            details.append(f"Gear: {sample['gear']}")
        if "autopilot_state" in sample:
            details.append(f"AP: {sample['autopilot_state']}")
        if "latitude" in sample and "longitude" in sample:
            details.append(f"GPS: {sample['latitude']:.4f}, {sample['longitude']:.4f}")

        summary = ", ".join(details) if details else "Active"
        return f"🎬 Stream: {codec} (Unencrypted) | 📊 SEI Telemetry: Available ({summary})"
    else:
        return f"🎬 Stream: {codec} (Unencrypted) | 📊 SEI Telemetry: Not Present (Firmware < 2025.44.25 or parked)"
