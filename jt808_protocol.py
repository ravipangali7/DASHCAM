"""
JTT 808/1078 Protocol Parser
Handles parsing of JTT 808 GPS tracking and JTT 1078 video streaming protocols
"""
import struct
import binascii

# JTT 808 Message IDs
MSG_ID_REGISTER = 0x0100
MSG_ID_REGISTER_RESPONSE = 0x8100
MSG_ID_HEARTBEAT = 0x0002
MSG_ID_HEARTBEAT_RESPONSE = 0x8002
MSG_ID_TERMINAL_AUTH = 0x0102
MSG_ID_TERMINAL_AUTH_RESPONSE = 0x8001
MSG_ID_LOCATION_UPLOAD = 0x0200
MSG_ID_LOCATION_RESPONSE = 0x8003

# JTT 1078 Video Message IDs
MSG_ID_VIDEO_UPLOAD = 0x1205
MSG_ID_VIDEO_UPLOAD_INIT = 0x1206
MSG_ID_VIDEO_DATA = 0x9201
MSG_ID_VIDEO_DATA_CONTROL = 0x9202
MSG_ID_VIDEO_LIST_QUERY = 0x9205
MSG_ID_VIDEO_LIST_RESPONSE = 0x1205

# Protocol Constants
START_FLAG = 0x7E
ESCAPE_FLAG = 0x7D
ESCAPE_XOR = 0x20

class JT808Parser:
    def __init__(self):
        self.buffer = bytearray()
        
    def escape_decode(self, data):
        """Decode escaped data (0x7D 0x01 -> 0x7D, 0x7D 0x02 -> 0x7E)"""
        result = bytearray()
        i = 0
        while i < len(data):
            if data[i] == ESCAPE_FLAG and i + 1 < len(data):
                if data[i + 1] == 0x01:
                    result.append(ESCAPE_FLAG)
                    i += 2
                elif data[i + 1] == 0x02:
                    result.append(START_FLAG)
                    i += 2
                else:
                    result.append(data[i])
                    i += 1
            else:
                result.append(data[i])
                i += 1
        return bytes(result)
    
    def escape_encode(self, data):
        """Encode data with escape sequences"""
        result = bytearray()
        for byte in data:
            if byte == ESCAPE_FLAG:
                result.append(ESCAPE_FLAG)
                result.append(0x01)
            elif byte == START_FLAG:
                result.append(ESCAPE_FLAG)
                result.append(0x02)
            else:
                result.append(byte)
        return bytes(result)
    
    def calculate_checksum(self, data):
        """Calculate XOR checksum"""
        checksum = 0
        for byte in data:
            checksum ^= byte
        return checksum
    
    def parse_message(self, data):
        """Parse JTT 808 message"""
        if len(data) < 12:  # Minimum message size
            return None
            
        if data[0] != START_FLAG or data[-1] != START_FLAG:
            return None
        
        # Extract message body (between start flags)
        body = data[1:-1]
        body = self.escape_decode(body)
        
        if len(body) < 11:
            return None
        
        # Extract checksum (last byte before end flag)
        received_checksum = body[-1]
        message_data = body[:-1]
        
        # Verify checksum
        calculated_checksum = self.calculate_checksum(message_data)
        if received_checksum != calculated_checksum:
            print(f"[WARNING] Checksum mismatch: received={received_checksum:02X}, calculated={calculated_checksum:02X}")
            # Continue anyway for debugging
        
        # Parse message header
        msg_id = struct.unpack('>H', message_data[0:2])[0]
        msg_attr = struct.unpack('>H', message_data[2:4])[0]
        phone = message_data[4:10].decode('ascii', errors='ignore')
        msg_seq = struct.unpack('>H', message_data[10:12])[0]
        
        # Extract body
        msg_body = message_data[12:] if len(message_data) > 12 else b''
        
        return {
            'msg_id': msg_id,
            'msg_attr': msg_attr,
            'phone': phone,
            'msg_seq': msg_seq,
            'body': msg_body,
            'raw': data
        }
    
    def build_response(self, msg_id, phone, msg_seq, body=b''):
        """Build JTT 808 response message"""
        # Build message header
        header = struct.pack('>H', msg_id)  # Message ID
        header += struct.pack('>H', len(body))  # Message attribute (body length)
        header += phone.encode('ascii').ljust(6, b'\x00')[:6]  # Phone number
        header += struct.pack('>H', msg_seq)  # Message sequence
        
        # Combine header and body
        message_data = header + body
        
        # Calculate checksum
        checksum = self.calculate_checksum(message_data)
        message_data += bytes([checksum])
        
        # Escape encode
        escaped = self.escape_encode(message_data)
        
        # Add start flags
        packet = bytes([START_FLAG]) + escaped + bytes([START_FLAG])
        
        return packet
    
    def build_register_response(self, phone, msg_seq, result_code=0):
        """Build registration response (0x8100)"""
        body = struct.pack('>H', result_code)  # Result code (0=success)
        body += b'\x00\x00'  # Authentication code (empty)
        return self.build_response(MSG_ID_REGISTER_RESPONSE, phone, msg_seq, body)
    
    def build_heartbeat_response(self, phone, msg_seq):
        """Build heartbeat response (0x8002)"""
        return self.build_response(MSG_ID_HEARTBEAT_RESPONSE, phone, msg_seq)
    
    def build_auth_response(self, phone, msg_seq, result_code=0):
        """Build authentication response (0x8001)"""
        body = struct.pack('>B', result_code)  # Result code
        return self.build_response(MSG_ID_TERMINAL_AUTH_RESPONSE, phone, msg_seq, body)
    
    def parse_location_data(self, body):
        """Parse JTT 808 location data upload message (0x0200)"""
        if len(body) < 28:  # Minimum size: 4+4+4+4+2+2+2+6 = 28 bytes
            return None
        
        # Parse location data message (0x0200)
        alarm_flag = struct.unpack('>I', body[0:4])[0]
        status = struct.unpack('>I', body[4:8])[0]
        
        # Latitude and Longitude are signed integers
        latitude_raw = struct.unpack('>i', body[8:12])[0]
        longitude_raw = struct.unpack('>i', body[12:16])[0]
        latitude = latitude_raw / 1000000.0
        longitude = longitude_raw / 1000000.0
        
        altitude = struct.unpack('>H', body[16:18])[0]
        speed = struct.unpack('>H', body[18:20])[0] / 10.0  # km/h
        direction = struct.unpack('>H', body[20:22])[0]  # degrees 0-359
        time_bcd = body[22:28]  # BCD format: YYMMDDHHmmss
        
        # Parse BCD time
        time_str = binascii.hexlify(time_bcd).decode()
        year = int(time_str[0:2])
        month = int(time_str[2:4])
        day = int(time_str[4:6])
        hour = int(time_str[6:8])
        minute = int(time_str[8:10])
        second = int(time_str[10:12])
        # Convert 2-digit year to 4-digit (assuming 2000-2099)
        year = 2000 + year if year < 100 else year
        
        # Additional information (optional, variable length)
        additional_info = body[28:] if len(body) > 28 else b''
        
        return {
            'alarm_flag': alarm_flag,
            'status': status,
            'latitude': latitude,
            'longitude': longitude,
            'altitude': altitude,
            'speed': speed,
            'direction': direction,
            'time': {
                'year': year,
                'month': month,
                'day': day,
                'hour': hour,
                'minute': minute,
                'second': second,
                'raw': time_bcd
            },
            'additional_info': additional_info
        }
    
    def build_location_response(self, phone, msg_seq, result_code=0):
        """Build location data upload response (0x8003)"""
        body = struct.pack('>B', result_code)  # Result code (0=success)
        return self.build_response(MSG_ID_LOCATION_RESPONSE, phone, msg_seq, body)
    
    def parse_video_data(self, body):
        """Parse JTT 1078 video data message"""
        if len(body) < 36:
            return None
        
        # Parse video upload message (0x1205)
        logic_channel = struct.unpack('>B', body[0:1])[0]
        data_type = struct.unpack('>B', body[1:2])[0]  # 0=AV, 1=Video, 2=Audio, 3=Video+Audio
        stream_type = struct.unpack('>B', body[2:3])[0]  # 0=Main, 1=Sub
        codec_type = struct.unpack('>B', body[3:4])[0]  # 0=H.264
        
        # GPS data (28 bytes)
        alarm_flag = struct.unpack('>I', body[4:8])[0]
        status = struct.unpack('>I', body[8:12])[0]
        latitude = struct.unpack('>I', body[12:16])[0] / 1000000.0
        longitude = struct.unpack('>I', body[16:20])[0] / 1000000.0
        altitude = struct.unpack('>H', body[20:22])[0]
        speed = struct.unpack('>H', body[22:24])[0] / 10.0
        direction = struct.unpack('>H', body[24:26])[0]
        time = body[26:32]  # BCD time format
        
        # Video data
        video_data = body[36:]
        
        return {
            'logic_channel': logic_channel,
            'data_type': data_type,
            'stream_type': stream_type,
            'codec_type': codec_type,
            'latitude': latitude,
            'longitude': longitude,
            'altitude': altitude,
            'speed': speed,
            'direction': direction,
            'video_data': video_data
        }
