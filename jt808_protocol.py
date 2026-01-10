"""
JTT 808/1078 Protocol Parser
Handles parsing of JTT 808 GPS tracking and JTT 1078 video streaming protocols

Protocol References:
- JTT 808-2013: Terminal communication protocol and data format of the road transport vehicle satellite positioning system
- JTT 1078-2016: Video communication protocol for road transport vehicles

Message Format (JTT808):
- Start Flag: 0x7E (1 byte)
- Message Header: Message ID (2 bytes) + Message Attribute (2 bytes) + Phone Number (6 bytes) + Message Sequence (2 bytes)
- Message Body: Variable length
- Checksum: XOR of all bytes from Message ID to end of Body (1 byte)
- End Flag: 0x7E (1 byte)

Escape Rules:
- 0x7D 0x01 -> 0x7D
- 0x7D 0x02 -> 0x7E
"""
import struct
import binascii

# JTT 808 Message IDs
MSG_ID_TERMINAL_RESPONSE = 0x0001
MSG_ID_TERMINAL_LOGOUT = 0x0003
MSG_ID_REGISTER = 0x0100
MSG_ID_REGISTER_RESPONSE = 0x8100
MSG_ID_HEARTBEAT = 0x0002
MSG_ID_HEARTBEAT_RESPONSE = 0x8002
MSG_ID_TERMINAL_AUTH = 0x0102
MSG_ID_TERMINAL_AUTH_RESPONSE = 0x8001
MSG_ID_LOCATION_UPLOAD = 0x0200
MSG_ID_LOCATION_RESPONSE = 0x8003
MSG_ID_LOGOUT_RESPONSE = 0x8001  # Uses same response ID as auth (general command response)

# JTT 1078 Video Message IDs
MSG_ID_VIDEO_REALTIME_REQUEST = 0x9101  # Real-time Audio and Video Transmission Request
MSG_ID_VIDEO_DOWNLOAD_REQUEST = 0x9102  # Stored Video Download Request
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
    
    def validate_message_format(self, msg_id, body):
        """
        Validate message format against JTT808/JTT1078 specification
        
        Returns: (is_valid, errors_list)
        """
        errors = []
        
        if msg_id == MSG_ID_VIDEO_REALTIME_REQUEST:
            # 0x9101: IP_len(1) + IP(4) + TCP_port(2) + UDP_port(2) + Channel(1) + DataType(1) + StreamType(1) = 12 bytes
            if len(body) < 12:
                errors.append(f"0x9101 body too short: {len(body)} bytes (expected 12)")
            elif len(body) > 12:
                errors.append(f"0x9101 body too long: {len(body)} bytes (expected 12)")
            else:
                # Validate IP length
                ip_length = body[0]
                if ip_length != 4:
                    errors.append(f"0x9101 IP length invalid: {ip_length} (expected 4)")
                # Validate ports are in valid range (check bytes, not values)
                # Validate channel, data_type, stream_type ranges
                if len(body) >= 10:
                    channel = body[9]
                    if channel > 127:  # Typically 0-127
                        errors.append(f"0x9101 Channel out of range: {channel}")
                if len(body) >= 11:
                    data_type = body[10]
                    if data_type > 2:
                        errors.append(f"0x9101 Data type out of range: {data_type} (expected 0-2)")
                if len(body) >= 12:
                    stream_type = body[11]
                    if stream_type > 1:
                        errors.append(f"0x9101 Stream type out of range: {stream_type} (expected 0-1)")
        
        elif msg_id == MSG_ID_VIDEO_DATA:
            # 0x9201: Channel(1) + DataType(1) + PackageType(1) + Timestamp(6) + Interval(2) + Size(2) = 13 bytes minimum
            if len(body) < 13:
                errors.append(f"0x{msg_id:04X} body too short: {len(body)} bytes (minimum 13)")
        elif msg_id == MSG_ID_VIDEO_DATA_CONTROL:
            # 0x9202 can be either:
            # - Control command (when sent TO device): 4 bytes [ControlType(1)][Channel(1)][DataType(1)][StreamType(1)]
            # - Video data (when received FROM device): 13+ bytes [Channel(1)][DataType(1)][PackageType(1)][Timestamp(6)][Interval(2)][Size(2)][Data...]
            if len(body) == 4:
                # Valid control command format
                pass
            elif len(body) >= 13:
                # Valid video data format
                pass
            else:
                # Invalid length (between 5-12 bytes is invalid)
                errors.append(f"0x{msg_id:04X} body length invalid: {len(body)} bytes (expected 4 for control command or 13+ for video data)")
        
        return (len(errors) == 0, errors)
    
    def build_response(self, msg_id, phone, msg_seq, body=b''):
        """Build JTT 808 response message"""
        # Validate message format before building
        is_valid, errors = self.validate_message_format(msg_id, body)
        if not is_valid:
            print(f"[PROTOCOL VALIDATION] Warnings for 0x{msg_id:04X}: {errors}")
        
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
        """
        Build registration response (0x8100)
        
        JTT808 Protocol Format (Message Body):
        - Bytes 0-1: Result code (2 bytes, big-endian): 0=success, 1=failure
        - Bytes 2-17: Authentication code (16 bytes, ASCII, null-padded)
        """
        body = struct.pack('>H', result_code)  # Result code (0=success)
        body += b'\x00\x00'  # Authentication code (empty)
        return self.build_response(MSG_ID_REGISTER_RESPONSE, phone, msg_seq, body)
    
    def build_heartbeat_response(self, phone, msg_seq):
        """
        Build heartbeat response (0x8002)
        
        JTT808 Protocol Format:
        - Message body is empty (0 bytes)
        """
        return self.build_response(MSG_ID_HEARTBEAT_RESPONSE, phone, msg_seq)
    
    def build_auth_response(self, phone, msg_seq, result_code=0):
        """
        Build authentication response (0x8001)
        
        JTT808 Protocol Format (Message Body):
        - Byte 0: Result code (1 byte): 0=success, 1=failure, 2=invalid, 3=not supported
        """
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
    
    def parse_terminal_response(self, body):
        """Parse terminal general response message (0x0001)"""
        if len(body) < 5:  # Minimum size: 2+2+1 = 5 bytes
            return None
        
        # Parse terminal response message (0x0001)
        reply_serial = struct.unpack('>H', body[0:2])[0]
        reply_id = struct.unpack('>H', body[2:4])[0]
        result = struct.unpack('>B', body[4:5])[0]
        
        # Result code meanings
        result_meanings = {
            0: "Success/Confirmation",
            1: "Failure",
            2: "Message Error",
            3: "Not Supported"
        }
        
        return {
            'reply_serial': reply_serial,
            'reply_id': reply_id,
            'result': result,
            'result_text': result_meanings.get(result, f"Unknown ({result})")
        }
    
    def build_location_response(self, phone, msg_seq, result_code=0):
        """Build location data upload response (0x8003)"""
        body = struct.pack('>B', result_code)  # Result code (0=success)
        return self.build_response(MSG_ID_LOCATION_RESPONSE, phone, msg_seq, body)
    
    def build_logout_response(self, phone, msg_seq, result_code=0):
        """Build terminal logout response (0x8001)"""
        body = struct.pack('>B', result_code)  # Result code (0=success)
        return self.build_response(MSG_ID_LOGOUT_RESPONSE, phone, msg_seq, body)
    
    def build_terminal_response(self, phone, msg_seq, reply_id, result_code=0):
        """
        Build terminal general response (0x0001)
        
        JTT808 Protocol Format (Message Body):
        - Bytes 0-1: Reply serial number (2 bytes, big-endian)
        - Bytes 2-3: Reply message ID (2 bytes, big-endian)
        - Byte 4: Result code (1 byte): 0=success, 1=failure, 2=message error, 3=not supported
        """
        body = struct.pack('>H', msg_seq)  # Reply serial (use current message sequence)
        body += struct.pack('>H', reply_id)  # Reply message ID
        body += struct.pack('>B', result_code)  # Result code
        return self.build_response(MSG_ID_TERMINAL_RESPONSE, phone, msg_seq, body)
    
    def build_video_realtime_request(self, phone, msg_seq, server_ip, tcp_port, udp_port, 
                                     channel=1, data_type=1, stream_type=0):
        """
        Build real-time audio and video transmission request (0x9101)
        
        JTT1078 Protocol Format (Message Body):
        - Byte 0: IP address length (1 byte, typically 4 for IPv4)
        - Bytes 1-4: IP address (4 bytes for IPv4)
        - Bytes 5-6: TCP port (2 bytes, big-endian)
        - Bytes 7-8: UDP port (2 bytes, big-endian)
        - Byte 9: Logical channel number (1 byte)
        - Byte 10: Data type (1 byte): 0=AV, 1=Video only, 2=Audio only
        - Byte 11: Stream type (1 byte): 0=Main stream, 1=Sub stream
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            server_ip: Server IP address (string, e.g., "192.168.1.100")
            tcp_port: TCP port for video channel (int)
            udp_port: UDP port for video channel (int)
            channel: Logical channel number (1 byte, default=1)
            data_type: Data type (1 byte): 0=AV, 1=Video only, 2=Audio only (default=1)
            stream_type: Stream type (1 byte): 0=Main stream, 1=Sub stream (default=0)
        """
        import binascii
        
        # Parse IP address to bytes
        ip_parts = server_ip.split('.')
        if len(ip_parts) != 4:
            raise ValueError(f"Invalid IPv4 address: {server_ip}")
        ip_bytes = bytes([int(part) for part in ip_parts])
        ip_length = len(ip_bytes)
        
        # Validate field sizes
        if ip_length != 4:
            raise ValueError(f"IP address length must be 4 bytes for IPv4, got {ip_length}")
        if tcp_port < 0 or tcp_port > 65535:
            raise ValueError(f"TCP port must be 0-65535, got {tcp_port}")
        if udp_port < 0 or udp_port > 65535:
            raise ValueError(f"UDP port must be 0-65535, got {udp_port}")
        if channel < 0 or channel > 255:
            raise ValueError(f"Channel must be 0-255, got {channel}")
        if data_type < 0 or data_type > 2:
            raise ValueError(f"Data type must be 0-2, got {data_type}")
        if stream_type < 0 or stream_type > 1:
            raise ValueError(f"Stream type must be 0-1, got {stream_type}")
        
        # Build message body with detailed logging
        body = bytearray()
        
        # Byte 0: IP address length
        body.extend(struct.pack('>B', ip_length))
        print(f"[PROTOCOL 0x9101] Field 0: IP length = {ip_length} bytes")
        
        # Bytes 1-4: IP address
        body.extend(ip_bytes)
        print(f"[PROTOCOL 0x9101] Field 1: IP address = {server_ip} ({binascii.hexlify(ip_bytes).decode()})")
        
        # Bytes 5-6: TCP port (big-endian)
        tcp_port_bytes = struct.pack('>H', tcp_port)
        body.extend(tcp_port_bytes)
        print(f"[PROTOCOL 0x9101] Field 2: TCP port = {tcp_port} (0x{binascii.hexlify(tcp_port_bytes).decode()})")
        
        # Bytes 7-8: UDP port (big-endian)
        udp_port_bytes = struct.pack('>H', udp_port)
        body.extend(udp_port_bytes)
        print(f"[PROTOCOL 0x9101] Field 3: UDP port = {udp_port} (0x{binascii.hexlify(udp_port_bytes).decode()})")
        
        # Byte 9: Logical channel number
        body.extend(struct.pack('>B', channel))
        print(f"[PROTOCOL 0x9101] Field 4: Channel = {channel} (0x{channel:02X})")
        
        # Byte 10: Data type
        body.extend(struct.pack('>B', data_type))
        data_type_names = {0: 'AV', 1: 'Video only', 2: 'Audio only'}
        print(f"[PROTOCOL 0x9101] Field 5: Data type = {data_type} ({data_type_names.get(data_type, 'Unknown')})")
        
        # Byte 11: Stream type
        body.extend(struct.pack('>B', stream_type))
        stream_type_names = {0: 'Main stream', 1: 'Sub stream'}
        print(f"[PROTOCOL 0x9101] Field 6: Stream type = {stream_type} ({stream_type_names.get(stream_type, 'Unknown')})")
        
        # Log complete body structure
        body_bytes = bytes(body)
        print(f"[PROTOCOL 0x9101] Complete body: {len(body_bytes)} bytes, hex: {binascii.hexlify(body_bytes).decode()}")
        print(f"[PROTOCOL 0x9101] Body structure: [IP_len(1)][IP(4)][TCP_port(2)][UDP_port(2)][Channel(1)][DataType(1)][StreamType(1)]")
        
        return self.build_response(MSG_ID_VIDEO_REALTIME_REQUEST, phone, msg_seq, body_bytes)
    
    def build_video_list_query(self, phone, msg_seq, channel=0xFF, video_type=0xFF, start_time=None, end_time=None):
        """
        Build video list query (0x9205)
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            channel: Logical channel number (0xFF = all channels)
            video_type: Video type (0xFF = all types)
            start_time: Start time (BCD format: YYMMDDHHmmss, None = no limit)
            end_time: End time (BCD format: YYMMDDHHmmss, None = no limit)
        """
        body = struct.pack('>B', channel)  # Channel number
        body += struct.pack('>B', video_type)  # Video type
        
        # Time range (optional, 6 bytes each)
        if start_time:
            body += start_time
        else:
            body += b'\xFF' * 6  # No start time limit
        
        if end_time:
            body += end_time
        else:
            body += b'\xFF' * 6  # No end time limit
        
        return self.build_response(MSG_ID_VIDEO_LIST_QUERY, phone, msg_seq, body)
    
    def build_video_download_request(self, phone, msg_seq, channel, start_time, end_time, 
                                     alarm_type=0, video_type=0, storage_type=0):
        """
        Build stored video download request (0x9102)
        
        JTT1078 Protocol Format (Message Body):
        - Byte 0: Channel number (1 byte)
        - Bytes 1-6: Start time (6 bytes BCD format: YYMMDDHHmmss)
        - Bytes 7-12: End time (6 bytes BCD format: YYMMDDHHmmss)
        - Bytes 13-16: Alarm type (4 bytes, big-endian)
        - Byte 17: Video type (1 byte)
        - Byte 18: Storage type (1 byte)
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            channel: Logical channel number
            start_time: Start time string (YYMMDDHHmmss) or BCD bytes
            end_time: End time string (YYMMDDHHmmss) or BCD bytes
            alarm_type: Alarm type (4 bytes, default=0)
            video_type: Video type (1 byte, default=0)
            storage_type: Storage type (1 byte, default=0)
        """
        import binascii
        
        body = bytearray()
        
        # Byte 0: Channel number
        body.extend(struct.pack('>B', channel))
        print(f"[PROTOCOL 0x9102] Field 0: Channel = {channel} (0x{channel:02X})")
        
        # Bytes 1-6: Start time (BCD format: YYMMDDHHmmss)
        if isinstance(start_time, str):
            # Convert YYMMDDHHmmss string to BCD bytes
            if len(start_time) == 12:
                # Convert each pair of digits to BCD byte
                start_time_bytes = bytes([int(start_time[i]) * 16 + int(start_time[i+1]) for i in range(0, 12, 2)])
            else:
                raise ValueError(f"Start time string must be 12 digits (YYMMDDHHmmss), got: {start_time}")
        else:
            start_time_bytes = start_time[:6] if len(start_time) >= 6 else start_time + b'\x00' * (6 - len(start_time))
        
        body.extend(start_time_bytes)
        print(f"[PROTOCOL 0x9102] Field 1: Start time = {start_time_bytes.hex()}")
        
        # Bytes 7-12: End time (BCD format: YYMMDDHHmmss)
        if isinstance(end_time, str):
            # Convert YYMMDDHHmmss string to BCD bytes
            if len(end_time) == 12:
                # Convert each pair of digits to BCD byte
                end_time_bytes = bytes([int(end_time[i]) * 16 + int(end_time[i+1]) for i in range(0, 12, 2)])
            else:
                raise ValueError(f"End time string must be 12 digits (YYMMDDHHmmss), got: {end_time}")
        else:
            end_time_bytes = end_time[:6] if len(end_time) >= 6 else end_time + b'\x00' * (6 - len(end_time))
        
        body.extend(end_time_bytes)
        print(f"[PROTOCOL 0x9102] Field 2: End time = {end_time_bytes.hex()}")
        
        # Bytes 13-16: Alarm type (4 bytes, big-endian)
        body.extend(struct.pack('>I', alarm_type))
        print(f"[PROTOCOL 0x9102] Field 3: Alarm type = {alarm_type} (0x{alarm_type:08X})")
        
        # Byte 17: Video type
        body.extend(struct.pack('>B', video_type))
        print(f"[PROTOCOL 0x9102] Field 4: Video type = {video_type} (0x{video_type:02X})")
        
        # Byte 18: Storage type
        body.extend(struct.pack('>B', storage_type))
        print(f"[PROTOCOL 0x9102] Field 5: Storage type = {storage_type} (0x{storage_type:02X})")
        
        # Log complete body structure
        body_bytes = bytes(body)
        print(f"[PROTOCOL 0x9102] Complete body: {len(body_bytes)} bytes, hex: {binascii.hexlify(body_bytes).decode()}")
        print(f"[PROTOCOL 0x9102] Body structure: [Channel(1)][StartTime(6)][EndTime(6)][AlarmType(4)][VideoType(1)][StorageType(1)]")
        
        return self.build_response(MSG_ID_VIDEO_DOWNLOAD_REQUEST, phone, msg_seq, body_bytes)
    
    def build_video_control_command(self, phone, msg_seq, control_type, channel, data_type=0xFF, stream_type=0xFF):
        """
        Build video control command (0x9202)
        
        JTT1078 Protocol Format (Message Body):
        - Byte 0: Control type (1 byte)
          0 = Close all channels
          1 = Switch code stream (switch between different code streams)
          2 = Switch main/sub stream
          3 = Switch bitrate
          4 = Update keyframe interval
          5 = Add designated terminal
          6 = Delete designated terminal
        - Byte 1: Channel number (1 byte)
        - Byte 2: Data type (1 byte, 0xFF = all types)
          0 = AV, 1 = Video only, 2 = Audio only
        - Byte 3: Stream type (1 byte, 0xFF = all streams)
          0 = Main stream, 1 = Sub stream
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            control_type: Control type (0-6)
            channel: Logical channel number
            data_type: Data type (0xFF = all types)
            stream_type: Stream type (0xFF = all streams)
        """
        import binascii
        
        # Validate parameters
        if control_type < 0 or control_type > 6:
            raise ValueError(f"Control type must be 0-6, got {control_type}")
        if channel < 0 or channel > 255:
            raise ValueError(f"Channel must be 0-255, got {channel}")
        
        # Build message body with detailed logging
        body = bytearray()
        
        # Byte 0: Control type
        body.extend(struct.pack('>B', control_type))
        control_type_names = {
            0: 'Close all channels',
            1: 'Switch code stream',
            2: 'Switch main/sub stream',
            3: 'Switch bitrate',
            4: 'Update keyframe interval',
            5: 'Add designated terminal',
            6: 'Delete designated terminal'
        }
        print(f"[PROTOCOL 0x9202] Field 0: Control type = {control_type} ({control_type_names.get(control_type, 'Unknown')})")
        
        # Byte 1: Channel number
        body.extend(struct.pack('>B', channel))
        print(f"[PROTOCOL 0x9202] Field 1: Channel = {channel} (0x{channel:02X})")
        
        # Byte 2: Data type
        body.extend(struct.pack('>B', data_type))
        if data_type == 0xFF:
            print(f"[PROTOCOL 0x9202] Field 2: Data type = 0xFF (All types)")
        else:
            data_type_names = {0: 'AV', 1: 'Video only', 2: 'Audio only'}
            print(f"[PROTOCOL 0x9202] Field 2: Data type = {data_type} ({data_type_names.get(data_type, 'Unknown')})")
        
        # Byte 3: Stream type
        body.extend(struct.pack('>B', stream_type))
        if stream_type == 0xFF:
            print(f"[PROTOCOL 0x9202] Field 3: Stream type = 0xFF (All streams)")
        else:
            stream_type_names = {0: 'Main stream', 1: 'Sub stream'}
            print(f"[PROTOCOL 0x9202] Field 3: Stream type = {stream_type} ({stream_type_names.get(stream_type, 'Unknown')})")
        
        # Log complete body structure
        body_bytes = bytes(body)
        print(f"[PROTOCOL 0x9202] Complete body: {len(body_bytes)} bytes, hex: {binascii.hexlify(body_bytes).decode()}")
        print(f"[PROTOCOL 0x9202] Body structure: [ControlType(1)][Channel(1)][DataType(1)][StreamType(1)]")
        
        return self.build_response(MSG_ID_VIDEO_DATA_CONTROL, phone, msg_seq, body_bytes)
    
    def parse_video_list_response(self, body):
        """
        Parse JTT 1078 video list response (0x1205 as response to 0x9205)
        
        JTT1078 Protocol Format (Message Body):
        - Bytes 0-1: Video count (2 bytes, big-endian)
        - For each video (18 bytes):
          - Byte 0: Channel number (1 byte)
          - Bytes 1-6: Start time (6 bytes BCD: YYMMDDHHmmss)
          - Bytes 7-12: End time (6 bytes BCD: YYMMDDHHmmss)
          - Bytes 13-16: Alarm type (4 bytes, big-endian)
          - Byte 17: Video type (1 byte)
          - Bytes 18-21: File size (4 bytes, big-endian) - wait, that's 22 bytes per video
        Actually, per JTT1078 standard:
        - Video count: 2 bytes
        - Each video entry: 18 bytes
          - Channel: 1 byte
          - Start time: 6 bytes BCD
          - End time: 6 bytes BCD
          - Alarm type: 4 bytes
          - Video type: 1 byte
        Total per video: 18 bytes
        
        Returns: List of video dictionaries or None if parsing fails
        """
        if len(body) < 2:
            return None
        
        try:
            # Parse video count
            video_count = struct.unpack('>H', body[0:2])[0]
            
            # Each video entry is 18 bytes
            videos = []
            offset = 2
            
            for i in range(video_count):
                if offset + 18 > len(body):
                    print(f"[PROTOCOL] Warning: Incomplete video list, expected {video_count} videos but only {len(videos)} complete")
                    break
                
                # Parse video entry
                channel = struct.unpack('>B', body[offset:offset+1])[0]
                
                # Parse start time (BCD: YYMMDDHHmmss)
                start_time_bytes = body[offset+1:offset+7]
                start_time_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in start_time_bytes])
                
                # Parse end time (BCD: YYMMDDHHmmss)
                end_time_bytes = body[offset+7:offset+13]
                end_time_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in end_time_bytes])
                
                # Parse alarm type
                alarm_type = struct.unpack('>I', body[offset+13:offset+17])[0]
                
                # Parse video type
                video_type = struct.unpack('>B', body[offset+17:offset+18])[0]
                
                videos.append({
                    'channel': channel,
                    'start_time': start_time_str,
                    'end_time': end_time_str,
                    'alarm_type': alarm_type,
                    'video_type': video_type,
                    'index': i
                })
                
                offset += 18
            
            print(f"[PROTOCOL] Parsed video list: {len(videos)} videos")
            return {
                'video_count': len(videos),
                'videos': videos
            }
        except Exception as e:
            print(f"[ERROR] Failed to parse video list response: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def parse_video_data(self, body):
        """Parse JTT 1078 video data message (0x1205 video upload)"""
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
