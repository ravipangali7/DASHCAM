"""
JTT1078 Protocol Parser
Handles parsing of JTT1078 protocol messages from dashcam devices.
"""

import struct
from typing import Optional, Dict, Tuple
from enum import IntEnum


class MessageType(IntEnum):
    """JTT1078 Message Types"""
    TERMINAL_RESPONSE = 0x1005
    REAL_TIME_VIDEO_UPLOAD_REQUEST = 0x9101
    REAL_TIME_VIDEO_UPLOAD_AVI_1 = 0x9201
    REAL_TIME_VIDEO_UPLOAD_AVI_2 = 0x9202
    VIDEO_UPLOAD_CONTROL = 0x9205
    REAL_TIME_VIDEO_UPLOAD_H264_1 = 0x9206
    REAL_TIME_VIDEO_UPLOAD_H264_2 = 0x9207


class JTT1078Parser:
    """Parser for JTT1078 protocol messages"""
    
    START_FLAG = 0x7E
    END_FLAG = 0x7E
    
    def __init__(self):
        self.buffer = bytearray()
    
    def calculate_checksum(self, data: bytes) -> int:
        """Calculate BCC checksum (XOR of all bytes)"""
        checksum = 0
        for byte in data:
            checksum ^= byte
        return checksum
    
    def escape_data(self, data: bytes) -> bytes:
        """Escape special bytes in data (0x7E -> 0x7D 0x02, 0x7D -> 0x7D 0x01)"""
        escaped = bytearray()
        for byte in data:
            if byte == 0x7E:
                escaped.extend([0x7D, 0x02])
            elif byte == 0x7D:
                escaped.extend([0x7D, 0x01])
            else:
                escaped.append(byte)
        return bytes(escaped)
    
    def unescape_data(self, data: bytes) -> bytes:
        """Unescape special bytes in data"""
        unescaped = bytearray()
        i = 0
        while i < len(data):
            if data[i] == 0x7D:
                if i + 1 < len(data):
                    if data[i + 1] == 0x02:
                        unescaped.append(0x7E)
                        i += 2
                    elif data[i + 1] == 0x01:
                        unescaped.append(0x7D)
                        i += 2
                    else:
                        unescaped.append(data[i])
                        i += 1
                else:
                    unescaped.append(data[i])
                    i += 1
            else:
                unescaped.append(data[i])
                i += 1
        return bytes(unescaped)
    
    def parse_message_header(self, data: bytes) -> Optional[Dict]:
        """
        Parse JTT1078 message header
        Header structure (16 bytes):
        - Start flag: 1 byte (0x7E)
        - Message ID: 2 bytes
        - Message body properties: 2 bytes
        - Protocol version: 1 byte
        - Terminal phone number: 6 bytes (BCD)
        - Message serial number: 2 bytes
        - Message package items: 1 byte (if has subpackage)
        - Message body length: 2 bytes (if has subpackage)
        """
        if len(data) < 16:
            return None
        
        if data[0] != self.START_FLAG:
            return None
        
        message_id = struct.unpack('>H', data[1:3])[0]
        body_props = struct.unpack('>H', data[3:5])[0]
        
        # Extract flags from body properties
        has_subpackage = (body_props >> 13) & 0x01
        encryption = (body_props >> 10) & 0x07
        body_length = body_props & 0x3FF
        
        protocol_version = data[5]
        terminal_phone = data[6:12].hex()  # BCD format
        message_serial = struct.unpack('>H', data[12:14])[0]
        
        header_length = 16 if has_subpackage else 13
        
        if len(data) < header_length:
            return None
        
        package_items = None
        if has_subpackage:
            package_items = data[14]
            body_length = struct.unpack('>H', data[15:17])[0]
            header_length = 17
        
        return {
            'message_id': message_id,
            'body_properties': body_props,
            'has_subpackage': has_subpackage,
            'encryption': encryption,
            'body_length': body_length,
            'protocol_version': protocol_version,
            'terminal_phone': terminal_phone,
            'message_serial': message_serial,
            'package_items': package_items,
            'header_length': header_length
        }
    
    def parse_message(self, data: bytes) -> Optional[Dict]:
        """
        Parse complete JTT1078 message
        Returns dict with header and body, or None if invalid
        """
        if len(data) < 13:
            return None
        
        # Find start flag
        start_idx = -1
        for i in range(len(data)):
            if data[i] == self.START_FLAG:
                start_idx = i
                break
        
        if start_idx == -1:
            return None
        
        # Find end flag
        end_idx = -1
        for i in range(len(data) - 1, start_idx, -1):
            if data[i] == self.END_FLAG:
                end_idx = i
                break
        
        if end_idx == -1:
            return None
        
        # Extract message (excluding start and end flags)
        message_data = data[start_idx + 1:end_idx]
        
        # Unescape data
        unescaped_data = self.unescape_data(message_data)
        
        if len(unescaped_data) < 12:
            return None
        
        # Parse header
        header = self.parse_message_header(bytes([self.START_FLAG]) + unescaped_data)
        if not header:
            return None
        
        # Extract body and checksum
        header_len = header['header_length'] - 1  # Exclude start flag
        body_length = header['body_length']
        
        if len(unescaped_data) < header_len + body_length + 1:
            return None  # Incomplete message
        
        body = unescaped_data[header_len:header_len + body_length]
        checksum_byte = unescaped_data[header_len + body_length]
        
        # Verify checksum
        data_for_checksum = unescaped_data[:header_len + body_length]
        calculated_checksum = self.calculate_checksum(data_for_checksum)
        
        if calculated_checksum != checksum_byte:
            return None  # Invalid checksum
        
        return {
            'header': header,
            'body': body,
            'checksum': checksum_byte,
            'valid': True
        }
    
    def build_message(self, message_id: int, terminal_phone: str, 
                     body: bytes, message_serial: int = 0,
                     has_subpackage: bool = False, package_items: int = None) -> bytes:
        """
        Build a JTT1078 protocol message
        """
        # Build header
        body_length = len(body)
        body_props = body_length & 0x3FF
        if has_subpackage:
            body_props |= (1 << 13)
        
        header = bytearray()
        header.append(self.START_FLAG)
        header.extend(struct.pack('>H', message_id))
        header.extend(struct.pack('>H', body_props))
        header.append(0x01)  # Protocol version
        
        # Terminal phone (BCD, 6 bytes, pad with 0xF)
        phone_bytes = bytearray(6)
        phone_hex = terminal_phone.replace(' ', '').replace('-', '')
        for i in range(min(12, len(phone_hex))):
            if i % 2 == 0:
                phone_bytes[i // 2] = int(phone_hex[i], 16) << 4
            else:
                phone_bytes[i // 2] |= int(phone_hex[i], 16)
        # Fill remaining with 0xF
        for i in range(len(phone_hex) // 2, 6):
            phone_bytes[i] = 0xFF
        header.extend(phone_bytes)
        
        header.extend(struct.pack('>H', message_serial))
        
        if has_subpackage and package_items is not None:
            header.append(package_items)
            header.extend(struct.pack('>H', body_length))
        
        # Calculate checksum
        message_data = bytes(header[1:]) + body  # Exclude start flag for checksum
        checksum = self.calculate_checksum(message_data)
        
        # Escape data
        escaped_data = self.escape_data(message_data + bytes([checksum]))
        
        # Build final message
        return bytes([self.START_FLAG]) + escaped_data + bytes([self.END_FLAG])
    
    def parse_video_data(self, body: bytes) -> Optional[Dict]:
        """
        Parse video data from message body (9201, 9202, 9206, 9207)
        Body structure:
        - Logic channel number: 1 byte
        - Data type: 1 byte (0: video I frame, 1: video P frame, 2: video B frame, 3: audio frame)
        - Package type: 1 byte (0: frame start, 1: frame continuation, 2: frame end)
        - Timestamp: 8 bytes (BCD format: YYMMDDHHmmss)
        - Last frame interval: 2 bytes
        - Last frame size: 2 bytes
        - Video data: variable length
        """
        if len(body) < 15:
            return None
        
        logic_channel = body[0]
        data_type = body[1]
        package_type = body[2]
        
        # Parse timestamp (BCD, 8 bytes)
        timestamp_bytes = body[3:11]
        timestamp_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in timestamp_bytes])
        
        last_frame_interval = struct.unpack('>H', body[11:13])[0]
        last_frame_size = struct.unpack('>H', body[13:15])[0]
        video_data = body[15:]
        
        return {
            'logic_channel': logic_channel,
            'data_type': data_type,
            'package_type': package_type,
            'timestamp': timestamp_str,
            'last_frame_interval': last_frame_interval,
            'last_frame_size': last_frame_size,
            'video_data': video_data
        }
