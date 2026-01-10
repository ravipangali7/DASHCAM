"""
JTT 808/1078 Server
Handles device connections and video streaming
"""
import socket
import binascii
import threading
import os
import sys
import time
from jt808_protocol import JT808Parser, MSG_ID_REGISTER, MSG_ID_HEARTBEAT, MSG_ID_TERMINAL_AUTH, MSG_ID_VIDEO_UPLOAD, MSG_ID_LOCATION_UPLOAD, MSG_ID_TERMINAL_RESPONSE, MSG_ID_TERMINAL_LOGOUT, MSG_ID_VIDEO_REALTIME_REQUEST, MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, MSG_ID_VIDEO_LIST_QUERY
from video_streamer import stream_manager

HOST = "0.0.0.0"
JT808_PORT = int(os.environ.get('JT808_PORT', 2222))

# Global connection tracking
device_connections = {}  # device_id -> list of connections
ip_connections = {}  # device_ip -> list of connections (track by IP address)
connection_lock = threading.Lock()

class DeviceHandler:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.parser = JT808Parser()
        self.device_id = None
        self.authenticated = False
        self.video_request_sent = False  # Track if video request already sent
        self.video_request_attempts = []  # Track video request attempts with different params
        self.video_packets_received = False  # Track if we've received any video packets
        self.video_request_time = None  # Track when video request was sent
        self.buffer = bytearray()
        self.message_count = 0
        # Frame reassembly buffers for multi-packet video frames
        self.video_frame_buffers = {}  # (channel, frame_id) -> list of packets
        # Raw data capture for unparseable data
        self.raw_data_buffer = bytearray()
        self.raw_data_count = 0
        
    def handle_message(self, msg, raw_message=None):
        """Handle parsed JTT 808/1078 messages"""
        msg_id = msg['msg_id']
        phone = msg['phone']
        msg_seq = msg['msg_seq']
        body = msg['body']
        
        self.message_count += 1
        
        # Enhanced logging with hex dump for debugging
        print(f"[MSG #{self.message_count}] ID=0x{msg_id:04X}, Phone={phone}, Seq={msg_seq}, BodyLen={len(body)}")
        if raw_message and len(raw_message) <= 200:  # Only show hex for small messages
            hex_dump = binascii.hexlify(raw_message).decode()
            print(f"[HEX] {hex_dump[:100]}{'...' if len(hex_dump) > 100 else ''}")
        
        # Register device if not already registered
        if self.device_id is None:
            self.device_id = phone
            device_ip = self.addr[0] if self.addr else 'unknown'
            
            with connection_lock:
                # Track by device ID
                if phone not in device_connections:
                    device_connections[phone] = []
                device_connections[phone].append(self)
                
                # Track by IP address
                if device_ip not in ip_connections:
                    ip_connections[device_ip] = []
                ip_connections[device_ip].append(self)
                
                print(f"[CONN] Device {phone} (IP: {device_ip}) now has {len(device_connections[phone])} connection(s) by ID, {len(ip_connections[device_ip])} by IP")
                
                # Alert if multiple connections from same IP
                if len(ip_connections[device_ip]) > 1:
                    print(f"[CONN] ⚠️ Multiple connections ({len(ip_connections[device_ip])}) from IP {device_ip} - might be separate video connection!")
        
        # Handle terminal general response (0x0001)
        if msg_id == MSG_ID_TERMINAL_RESPONSE:
            response_info = self.parser.parse_terminal_response(body)
            if response_info:
                reply_id = response_info['reply_id']
                print(f"[RESPONSE] Device={phone} acknowledged message ID=0x{reply_id:04X}, "
                      f"Serial={response_info['reply_serial']}, Result={response_info['result_text']}")
                
                # If this is a response to video request (0x9101), try alternative configs if failed
                if reply_id == MSG_ID_VIDEO_REALTIME_REQUEST:
                    if response_info['result_text'] != 'Success/Confirmation':
                        print(f"[WARNING] Video request was not successful, result: {response_info['result_text']}")
                    else:
                        print(f"[INFO] Video request acknowledged successfully, waiting for video packets...")
                        # Send a keep-alive heartbeat to maintain connection
                        if self.conn:
                            try:
                                heartbeat = self.parser.build_heartbeat_response(phone, msg_seq + 1)
                                self.conn.send(heartbeat)
                                print(f"[TX] Sent keep-alive heartbeat after video acknowledgment")
                            except:
                                pass
            else:
                print(f"[RESPONSE] Failed to parse terminal response from {phone}")
                print(f"[RESPONSE] Body hex: {binascii.hexlify(body).decode()}")
            # No response needed - this IS a response message
        
        # Handle terminal logout (0x0003)
        elif msg_id == MSG_ID_TERMINAL_LOGOUT:
            print(f"[LOGOUT] Device {phone} is logging out")
            # Send logout response
            response = self.parser.build_logout_response(phone, msg_seq, 0)
            self.conn.send(response)
            print(f"[TX] Logout response sent")
        
        # Handle registration (0x0100)
        elif msg_id == MSG_ID_REGISTER:
            print(f"[+] Device registration from {phone}")
            self.device_id = phone
            response = self.parser.build_register_response(phone, msg_seq, 0)
            self.conn.send(response)
            print(f"[TX] Registration response sent")
        
        # Handle heartbeat (0x0002)
        elif msg_id == MSG_ID_HEARTBEAT:
            response = self.parser.build_heartbeat_response(phone, msg_seq)
            self.conn.send(response)
            print(f"[TX] Heartbeat response sent")
        
        # Handle authentication (0x0102)
        elif msg_id == MSG_ID_TERMINAL_AUTH:
            print(f"[+] Authentication request from {phone}")
            # Extract authentication code from body
            auth_code = body[:8] if len(body) >= 8 else b''
            # For demo, accept all authentications
            was_authenticated = self.authenticated
            self.authenticated = True
            response = self.parser.build_auth_response(phone, msg_seq, 0)
            self.conn.send(response)
            print(f"[TX] Authentication response sent")
            
            # Try sending video request with multiple configurations
            if not was_authenticated and not self.video_request_sent:
                # Try querying video list first, then request video
                # Some devices need this sequence
                try_video_list = os.environ.get('TRY_VIDEO_LIST_FIRST', 'false').lower() == 'true'
                if try_video_list:
                    threading.Thread(target=self.try_video_request, args=(phone, msg_seq, True), daemon=True).start()
                else:
                    self.try_video_request(phone, msg_seq, False)
            elif was_authenticated:
                print(f"[INFO] Device {phone} re-authenticated (video request already sent)")
        
        # Handle location data upload (0x0200)
        elif msg_id == MSG_ID_LOCATION_UPLOAD:
            location_info = self.parser.parse_location_data(body)
            if location_info:
                time_str = (f"{location_info['time']['year']:04d}-"
                           f"{location_info['time']['month']:02d}-"
                           f"{location_info['time']['day']:02d} "
                           f"{location_info['time']['hour']:02d}:"
                           f"{location_info['time']['minute']:02d}:"
                           f"{location_info['time']['second']:02d}")
                
                print(f"[LOCATION] Device={phone}, "
                      f"GPS=({location_info['latitude']:.6f}, {location_info['longitude']:.6f}), "
                      f"Speed={location_info['speed']:.1f} km/h, "
                      f"Direction={location_info['direction']}°, "
                      f"Altitude={location_info['altitude']}m, "
                      f"Time={time_str}, "
                      f"Alarm=0x{location_info['alarm_flag']:08X}, "
                      f"Status=0x{location_info['status']:08X}")
                
                # Send response
                response = self.parser.build_location_response(phone, msg_seq, 0)
                self.conn.send(response)
                print(f"[TX] Location response sent")
                
                # Try sending video request after location data (some devices need this)
                if not self.video_request_sent and self.authenticated:
                    print(f"[INFO] Trying video request after location data...")
                    threading.Thread(target=self.try_video_request_after_location, args=(phone, msg_seq), daemon=True).start()
            else:
                print(f"[LOCATION] Failed to parse location data from {phone}")
        
        # Handle video list response (0x1205 as response to 0x9205)
        elif msg_id == MSG_ID_VIDEO_UPLOAD and hasattr(self, '_video_list_query_sent') and self._video_list_query_sent:
            print(f"[VIDEO LIST] Video list response received from {phone}")
            # Parse video list and then request video
            if not self.video_request_sent:
                print(f"[INFO] Video list received, now requesting real-time video...")
                self.try_video_request(phone, msg_seq)
        
        # Handle video upload (0x1205) - JTT 1078 (stored video)
        elif msg_id == MSG_ID_VIDEO_UPLOAD:
            print(f"[VIDEO] Video data received from {phone} (0x1205)")
            video_info = self.parser.parse_video_data(body)
            if video_info:
                channel = video_info['logic_channel']
                video_data = video_info['video_data']
                
                # Add frame to stream manager
                stream_manager.add_frame(
                    phone,
                    channel,
                    video_data,
                    {
                        'latitude': video_info['latitude'],
                        'longitude': video_info['longitude'],
                        'speed': video_info['speed'],
                        'direction': video_info['direction']
                    }
                )
                
                print(f"[VIDEO] Channel={channel}, Size={len(video_data)} bytes, "
                      f"GPS=({video_info['latitude']:.6f}, {video_info['longitude']:.6f})")
        
        # Handle real-time video data (0x9201, 0x9202, 0x9206, 0x9207) - JTT 1078
        elif msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, 0x9206, 0x9207]:
            # Mark that we've received video packets
            if not self.video_packets_received:
                self.video_packets_received = True
                if self.video_request_time:
                    elapsed = time.time() - self.video_request_time
                    print(f"[VIDEO] ✓✓✓ FIRST VIDEO PACKET RECEIVED after {elapsed:.2f} seconds! ✓✓✓")
            
            print(f"[VIDEO] ✓ Real-time video data received from {phone} (0x{msg_id:04X})")
            video_info = self.parse_realtime_video_data(body, msg_id)
            if video_info:
                channel = video_info['logic_channel']
                package_type = video_info.get('package_type', 1)
                video_data = video_info['video_data']
                timestamp = video_info.get('timestamp', '')
                
                # Use timestamp as frame ID for reassembly
                frame_id = timestamp if timestamp else f"{msg_seq}_{channel}"
                frame_key = (channel, frame_id)
                
                # Handle frame reassembly for multi-packet frames
                if package_type == 0:  # Frame start
                    self.video_frame_buffers[frame_key] = [video_data]
                    print(f"[VIDEO] Frame START - Channel={channel}, FrameID={frame_id}, Size={len(video_data)} bytes")
                elif package_type == 1:  # Frame continuation
                    if frame_key in self.video_frame_buffers:
                        self.video_frame_buffers[frame_key].append(video_data)
                        print(f"[VIDEO] Frame CONTINUE - Channel={channel}, FrameID={frame_id}, PacketSize={len(video_data)} bytes")
                    else:
                        # Start new frame if we missed the start packet
                        self.video_frame_buffers[frame_key] = [video_data]
                        print(f"[VIDEO] Frame CONTINUE (missed start) - Channel={channel}, FrameID={frame_id}")
                elif package_type == 2:  # Frame end
                    if frame_key in self.video_frame_buffers:
                        self.video_frame_buffers[frame_key].append(video_data)
                        # Reassemble complete frame
                        complete_frame = b''.join(self.video_frame_buffers[frame_key])
                        del self.video_frame_buffers[frame_key]
                        print(f"[VIDEO] Frame END - Channel={channel}, FrameID={frame_id}, TotalSize={len(complete_frame)} bytes")
                        video_data = complete_frame
                    else:
                        # Frame end without start/continuation, use as single packet
                        print(f"[VIDEO] Frame END (single packet) - Channel={channel}, Size={len(video_data)} bytes")
                
                # Only add to stream manager if we have complete frame or single packet
                if package_type == 2 or (package_type == 0 and len(video_data) > 0):
                    # Add frame to stream manager
                    stream_manager.add_frame(
                        phone,
                        channel,
                        video_data,
                        {
                            'latitude': video_info.get('latitude', 0.0),
                            'longitude': video_info.get('longitude', 0.0),
                            'speed': video_info.get('speed', 0.0),
                            'direction': video_info.get('direction', 0)
                        }
                    )
                    
                    print(f"[VIDEO] ✓ Frame added to stream - Device={phone}, Channel={channel}, "
                          f"DataType={video_info.get('data_type', 'N/A')}, Size={len(video_data)} bytes")
            else:
                print(f"[VIDEO] ✗ Failed to parse video data from {phone}")
                if len(body) > 0:
                    print(f"[VIDEO] Body hex (first 50 bytes): {binascii.hexlify(body[:50]).decode()}")
        
        else:
            print(f"[?] Unknown message ID: 0x{msg_id:04X} from {phone}")
            print(f"[?] Message body length: {len(body)} bytes")
            if len(body) > 0:
                print(f"[?] Body hex (first 50 bytes): {binascii.hexlify(body[:50]).decode()}")
            # Check if this might be a video packet with wrong message ID parsing
            if len(body) >= 15:
                # Check if it looks like video data structure
                potential_channel = body[0]
                potential_data_type = body[1]
                if potential_data_type in [0, 1, 2, 3]:  # Valid data types
                    print(f"[?] WARNING: This might be a video packet! Channel={potential_channel}, DataType={potential_data_type}")
    
    def query_video_list(self, phone, msg_seq):
        """Query video list from device (0x9205)"""
        try:
            if not self.conn:
                return
            
            video_list_query = self.parser.build_video_list_query(phone, msg_seq + 1)
            self.conn.send(video_list_query)
            self._video_list_query_sent = True
            print(f"[TX] Video list query (0x9205) sent to {phone}")
        except Exception as e:
            print(f"[ERROR] Failed to send video list query: {e}")
    
    def try_video_request_after_location(self, phone, msg_seq):
        """Try sending video request after location data (delayed)"""
        time.sleep(1)  # Wait 1 second after location data
        if not self.video_request_sent:
            print(f"[INFO] Attempting video request after location data...")
            self.try_video_request(phone, msg_seq)
    
    def try_video_request(self, phone, msg_seq, try_video_list_first=False):
        """Try sending video request with different configurations"""
        try:
            # Optionally query video list first
            if try_video_list_first and not hasattr(self, '_video_list_query_sent'):
                print(f"[INFO] Querying video list first before requesting video...")
                self.query_video_list(phone, msg_seq)
                # Wait for response before sending video request
                time.sleep(2)
                if self.video_request_sent:
                    return  # Video request already sent from list response handler
            
            # Get server IP from connection (use local address)
            server_ip = self.conn.getsockname()[0] if self.conn else '0.0.0.0'
            # If bound to 0.0.0.0, try to get the actual IP the device can reach
            if server_ip == '0.0.0.0':
                server_ip = os.environ.get('VIDEO_SERVER_IP', '82.180.145.220')
            
            # Use same port as JT808 for video (or separate port if configured)
            video_port = int(os.environ.get('VIDEO_PORT', JT808_PORT))
            
            # Try multiple configurations
            configs_to_try = [
                {'channel': 1, 'data_type': 1, 'stream_type': 0, 'desc': 'Channel=1, Video only, Main stream'},
                {'channel': 0, 'data_type': 1, 'stream_type': 0, 'desc': 'Channel=0, Video only, Main stream'},
                {'channel': 1, 'data_type': 0, 'stream_type': 0, 'desc': 'Channel=1, AV, Main stream'},
                {'channel': 0, 'data_type': 0, 'stream_type': 0, 'desc': 'Channel=0, AV, Main stream'},
                {'channel': 1, 'data_type': 1, 'stream_type': 1, 'desc': 'Channel=1, Video only, Sub stream'},
            ]
            
            # Try first configuration immediately
            config = configs_to_try[0]
            try:
                video_request = self.parser.build_video_realtime_request(
                    phone=phone,
                    msg_seq=msg_seq + 1,
                    server_ip=server_ip,
                    tcp_port=video_port,
                    udp_port=video_port,
                    channel=config['channel'],
                    data_type=config['data_type'],
                    stream_type=config['stream_type']
                )
                if self.conn:
                    self.conn.send(video_request)
                self.video_request_sent = True
                self.video_request_time = time.time()
                self.video_request_attempts.append(config)
                print(f"[TX] Video streaming request sent to {phone}: IP={server_ip}, Port={video_port}, {config['desc']}")
                print(f"[TX] Request hex: {binascii.hexlify(video_request).decode()[:100]}...")
                
                # Start a thread to check if video arrives, if not try alternative configs
                threading.Thread(target=self.check_video_and_retry, args=(phone, msg_seq, server_ip, video_port, configs_to_try[1:]), daemon=True).start()
            except Exception as e:
                print(f"[ERROR] Failed to send video request: {e}")
                import traceback
                traceback.print_exc()
                
        except Exception as e:
            print(f"[ERROR] Error in try_video_request: {e}")
    
    def check_video_and_retry(self, phone, msg_seq, server_ip, video_port, alternative_configs):
        """Check if video packets arrive, if not try alternative configurations"""
        # Wait 5 seconds to see if video packets arrive
        time.sleep(5)
        
        if not self.video_packets_received and alternative_configs and self.conn:
            print(f"[RETRY] No video packets received after 5 seconds, trying alternative configuration...")
            config = alternative_configs[0]
            try:
                video_request = self.parser.build_video_realtime_request(
                    phone=phone,
                    msg_seq=msg_seq + len(self.video_request_attempts) + 1,
                    server_ip=server_ip,
                    tcp_port=video_port,
                    udp_port=video_port,
                    channel=config['channel'],
                    data_type=config['data_type'],
                    stream_type=config['stream_type']
                )
                self.conn.send(video_request)
                self.video_request_attempts.append(config)
                self.video_request_time = time.time()
                print(f"[TX] Retry video request: {config['desc']}")
            except Exception as e:
                print(f"[ERROR] Failed to send retry video request: {e}")
    
    def detect_h264_patterns(self, data):
        """Detect H.264 NAL unit start codes in raw data"""
        if len(data) < 4:
            return False
        
        # H.264 start codes: 0x00000001 or 0x000001
        h264_patterns = [
            b'\x00\x00\x00\x01',  # 4-byte start code
            b'\x00\x00\x01',      # 3-byte start code
        ]
        
        for pattern in h264_patterns:
            if pattern in data:
                return True
        
        # Also check for common H.264 NAL unit types after start codes
        for i in range(len(data) - 5):
            if data[i:i+3] == b'\x00\x00\x01' or data[i:i+4] == b'\x00\x00\x00\x01':
                # Check NAL unit type (first byte after start code)
                if i + len(pattern) < len(data):
                    nal_type = data[i + len(pattern)] & 0x1F
                    # Valid NAL unit types: 1-5 (non-IDR/IDR slices), 6 (SEI), 7 (SPS), 8 (PPS)
                    if 1 <= nal_type <= 8:
                        return True
        
        return False
    
    def detect_rtp_header(self, data):
        """Detect RTP header in UDP packet"""
        if len(data) < 12:
            return False
        
        # RTP header: V(2) P(1) X(1) CC(4) M(1) PT(7) Sequence(16) Timestamp(32) SSRC(32)
        # Version should be 2
        version = (data[0] >> 6) & 0x03
        if version == 2:
            # Check if it looks like RTP (payload type 96-127 are dynamic)
            payload_type = data[1] & 0x7F
            if 96 <= payload_type <= 127:
                return True
        
        return False
    
    def check_raw_video_data(self, data):
        """Check if raw data contains video patterns"""
        if len(data) < 10:
            return False
        
        # Check for H.264 patterns
        if self.detect_h264_patterns(data):
            print(f"[RAW VIDEO] ✓ H.264 pattern detected in raw data! Size={len(data)} bytes")
            # Try to extract NAL units
            h264_start = data.find(b'\x00\x00\x00\x01')
            if h264_start == -1:
                h264_start = data.find(b'\x00\x00\x01')
            
            if h264_start >= 0:
                print(f"[RAW VIDEO] H.264 start code found at offset {h264_start}")
                if h264_start + 5 < len(data):
                    nal_type = data[h264_start + (4 if data[h264_start:h264_start+4] == b'\x00\x00\x00\x01' else 3)] & 0x1F
                    nal_names = {1: 'Non-IDR', 5: 'IDR', 6: 'SEI', 7: 'SPS', 8: 'PPS'}
                    print(f"[RAW VIDEO] NAL unit type: {nal_type} ({nal_names.get(nal_type, 'Unknown')})")
            
            return True
        
        return False
    
    def process_raw_h264_data(self, data):
        """Process raw H.264 video data"""
        if not self.device_id:
            return
        
        # Find H.264 start codes
        start_codes = []
        i = 0
        while i < len(data) - 3:
            if data[i:i+4] == b'\x00\x00\x00\x01':
                start_codes.append((i, 4))
                i += 4
            elif data[i:i+3] == b'\x00\x00\x01':
                start_codes.append((i, 3))
                i += 3
            else:
                i += 1
        
        if len(start_codes) > 0:
            print(f"[RAW VIDEO] Found {len(start_codes)} H.264 NAL units in raw data")
            
            # Extract first complete NAL unit as a test
            if len(start_codes) >= 2:
                start_pos, start_len = start_codes[0]
                end_pos, _ = start_codes[1]
                nal_unit = data[start_pos + start_len:end_pos]
                
                if len(nal_unit) > 0:
                    # Add to stream manager as raw H.264
                    channel = 1  # Default channel
                    stream_manager.add_frame(
                        self.device_id,
                        channel,
                        nal_unit,
                        {
                            'latitude': 0.0,
                            'longitude': 0.0,
                            'speed': 0.0,
                            'direction': 0
                        }
                    )
                    print(f"[RAW VIDEO] ✓ Added raw H.264 NAL unit to stream: Device={self.device_id}, Size={len(nal_unit)} bytes")
    
    def process_rtp_packet(self, data):
        """Process RTP packet (may contain H.264 video)"""
        if len(data) < 12:
            return
        
        # RTP header is 12 bytes minimum
        rtp_header = data[:12]
        payload = data[12:]
        
        # Check if payload contains H.264
        if self.detect_h264_patterns(payload):
            print(f"[RTP VIDEO] ✓ RTP packet contains H.264 data! Payload size={len(payload)} bytes")
            if self.device_id:
                channel = 1  # Default channel
                stream_manager.add_frame(
                    self.device_id,
                    channel,
                    payload,
                    {
                        'latitude': 0.0,
                        'longitude': 0.0,
                        'speed': 0.0,
                        'direction': 0
                    }
                )
                print(f"[RTP VIDEO] ✓ Added RTP/H.264 payload to stream: Device={self.device_id}, Size={len(payload)} bytes")
    
    def parse_realtime_video_data(self, body, msg_id):
        """Parse real-time video data packets (0x9201, 0x9202, 0x9206, 0x9207)"""
        import struct
        try:
            if len(body) < 15:
                return None
            
            # Parse real-time video packet format
            logic_channel = body[0]
            data_type = body[1]  # 0=I-frame, 1=P-frame, 2=B-frame, 3=Audio
            package_type = body[2]  # 0=start, 1=continuation, 2=end
            
            # Parse timestamp (BCD format, 8 bytes: YYMMDDHHmmss)
            timestamp_bytes = body[3:11]
            timestamp_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in timestamp_bytes])
            
            # Last frame interval (2 bytes)
            last_frame_interval = struct.unpack('>H', body[11:13])[0] if len(body) >= 13 else 0
            
            # Last frame size (2 bytes)
            last_frame_size = struct.unpack('>H', body[13:15])[0] if len(body) >= 15 else 0
            
            # Video data starts at byte 15
            video_data = body[15:] if len(body) > 15 else b''
            
            return {
                'logic_channel': logic_channel,
                'data_type': data_type,
                'package_type': package_type,
                'timestamp': timestamp_str,
                'last_frame_interval': last_frame_interval,
                'last_frame_size': last_frame_size,
                'video_data': video_data,
                'message_id': msg_id
            }
        except Exception as e:
            print(f"[ERROR] Failed to parse real-time video data: {e}")
            return None
    
    def run(self):
        """Main handler loop"""
        device_ip = self.addr[0] if self.addr else 'unknown'
        print(f"[+] NEW TCP connection from {self.addr}")
        
        with connection_lock:
            # Check if this IP already has connections
            existing_connections = ip_connections.get(device_ip, [])
            total_by_id = len([c for c in device_connections.values() for _ in c])
            total_by_ip = len([c for conns in ip_connections.values() for c in conns])
            
            print(f"[CONN] Total active connections: {total_by_id} by device ID, {total_by_ip} by IP")
            
            if len(existing_connections) > 0:
                print(f"[CONN] ⚠️ IP {device_ip} already has {len(existing_connections)} connection(s) - this might be a video connection!")
                # Check if any existing connection has the same device_id
                for existing_conn in existing_connections:
                    if existing_conn.device_id:
                        print(f"[CONN] Existing connection has device_id: {existing_conn.device_id}")
        
        while True:
            try:
                data = self.conn.recv(4096)
                if not data:
                    print(f"[-] Device {self.device_id} disconnected")
                    break
                
                # Add to buffer
                self.buffer.extend(data)
                
                # Also capture raw data for analysis
                self.raw_data_buffer.extend(data)
                self.raw_data_count += len(data)
                
                # Check raw buffer for video patterns if it gets large
                if len(self.raw_data_buffer) > 1000:
                    if self.check_raw_video_data(self.raw_data_buffer):
                        # Found video in raw data - try to process it
                        print(f"[RAW VIDEO] Processing raw video data, buffer size={len(self.raw_data_buffer)}")
                        # Try to extract video frames from raw H.264 data
                        self.process_raw_h264_data(self.raw_data_buffer)
                        # Keep some buffer for next frame
                        if len(self.raw_data_buffer) > 5000:
                            self.raw_data_buffer = self.raw_data_buffer[-2000:]
                    else:
                        # No video pattern, clear old data
                        if len(self.raw_data_buffer) > 5000:
                            self.raw_data_buffer = self.raw_data_buffer[-2000:]
                
                # Try to parse complete messages
                while True:
                    # Find start flag
                    start_idx = -1
                    for i in range(len(self.buffer)):
                        if self.buffer[i] == 0x7E:
                            start_idx = i
                            break
                    
                    if start_idx == -1:
                        # No start flag found, clear buffer
                        self.buffer.clear()
                        break
                    
                    # Remove data before start flag
                    if start_idx > 0:
                        self.buffer = self.buffer[start_idx:]
                    
                    # Find end flag
                    end_idx = -1
                    for i in range(1, len(self.buffer)):
                        if self.buffer[i] == 0x7E:
                            end_idx = i
                            break
                    
                    if end_idx == -1:
                        # Incomplete message, wait for more data
                        break
                    
                    # Extract complete message
                    message = bytes(self.buffer[:end_idx + 1])
                    self.buffer = self.buffer[end_idx + 1:]
                    
                    # Parse and handle message
                    msg = self.parser.parse_message(message)
                    if msg:
                        self.handle_message(msg, raw_message=message)
                    else:
                        hex_data = binascii.hexlify(message).decode()
                        print(f"[PARSE ERROR] Message length={len(message)}, First 100 bytes: {hex_data[:100]}")
                        
                        # Check if unparseable message contains video data
                        if self.check_raw_video_data(message):
                            print(f"[PARSE ERROR] ⚠️ Unparseable message contains H.264 video data!")
                            self.process_raw_h264_data(message)
                        
                        # Try to extract message ID manually for debugging
                        if len(message) >= 3:
                            try:
                                potential_msg_id = (message[1] << 8) | message[2] if len(message) > 2 else 0
                                print(f"[PARSE ERROR] Potential message ID: 0x{potential_msg_id:04X}")
                            except:
                                pass
                        
                        # Check for RTP header if message doesn't start with 0x7E
                        if len(message) > 0 and message[0] != 0x7E:
                            if self.detect_rtp_header(message):
                                print(f"[PARSE ERROR] ⚠️ Message appears to be RTP packet!")
                                self.process_rtp_packet(message)
                
            except Exception as e:
                print(f"[ERROR] {e}")
                import traceback
                traceback.print_exc()
                break
        
        if self.conn:
            self.conn.close()
        
        # Remove from connection tracking
        device_ip = self.addr[0] if self.addr else None
        
        with connection_lock:
            # Remove from device ID tracking
            if self.device_id:
                if self.device_id in device_connections:
                    if self in device_connections[self.device_id]:
                        device_connections[self.device_id].remove(self)
                    if len(device_connections[self.device_id]) == 0:
                        del device_connections[self.device_id]
                        print(f"[CONN] Device {self.device_id} has no more connections")
            
            # Remove from IP tracking
            if device_ip and device_ip in ip_connections:
                if self in ip_connections[device_ip]:
                    ip_connections[device_ip].remove(self)
                if len(ip_connections[device_ip]) == 0:
                    del ip_connections[device_ip]
        
        print(f"[-] Connection closed for {self.addr}")

def handle_udp_video_packet(data, addr, port=None):
    """Handle UDP video packets with enhanced analysis"""
    try:
        # Enhanced UDP logging with size analysis
        packet_size = len(data)
        print(f"[UDP] Received {packet_size} bytes from {addr} on port {port or 'default'}")
        
        # Analyze packet size (video packets are typically larger)
        if packet_size > 500:
            print(f"[UDP] ⚠️ Large packet ({packet_size} bytes) - likely video data!")
        
        # Show hex dump for small packets or first bytes of large packets
        if packet_size <= 100:
            hex_dump = binascii.hexlify(data).decode()
            print(f"[UDP HEX] {hex_dump}")
        else:
            hex_dump = binascii.hexlify(data[:100]).decode()
            print(f"[UDP HEX] First 100 bytes: {hex_dump}...")
        
        # Check for raw H.264 patterns
        handler = DeviceHandler(None, addr)
        if handler.detect_h264_patterns(data):
            print(f"[UDP] ✓✓✓ H.264 pattern detected in UDP packet! ✓✓✓")
            handler.process_raw_h264_data(data)
            return
        
        # Check for RTP header
        if handler.detect_rtp_header(data):
            print(f"[UDP] ✓✓✓ RTP header detected in UDP packet! ✓✓✓")
            handler.process_rtp_packet(data)
            return
        
        # Try to parse as JTT808 message
        parser = JT808Parser()
        msg = parser.parse_message(data)
        if msg:
            msg_id = msg['msg_id']
            phone = msg.get('phone', 'Unknown')
            
            print(f"[UDP] Parsed message ID=0x{msg_id:04X} from {phone} at {addr}")
            
            # Handle real-time video data on UDP
            if msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, 0x9206, 0x9207]:
                print(f"[UDP VIDEO] ✓ Real-time video data from {phone} at {addr} (0x{msg_id:04X})")
                
                video_info = handler.parse_realtime_video_data(msg['body'], msg_id)
                
                if video_info:
                    channel = video_info['logic_channel']
                    video_data = video_info['video_data']
                    
                    # Add frame to stream manager
                    stream_manager.add_frame(
                        phone,
                        channel,
                        video_data,
                        {
                            'latitude': video_info.get('latitude', 0.0),
                            'longitude': video_info.get('longitude', 0.0),
                            'speed': video_info.get('speed', 0.0),
                            'direction': video_info.get('direction', 0)
                        }
                    )
                    
                    print(f"[UDP VIDEO] ✓ Channel={channel}, DataType={video_info.get('data_type', 'N/A')}, "
                          f"PackageType={video_info.get('package_type', 'N/A')}, Size={len(video_data)} bytes")
                else:
                    print(f"[UDP VIDEO] ✗ Failed to parse video data")
            else:
                print(f"[UDP] Message ID=0x{msg_id:04X} from {addr} (not video data)")
        else:
            print(f"[UDP] Failed to parse message from {addr}")
            print(f"[UDP] First 50 bytes: {binascii.hexlify(data[:50]).decode()}")
            print(f"[UDP] ⚠️ Unparseable UDP packet - might be raw video data!")
            
            # Try to process as raw video anyway
            if packet_size > 100:  # Large packets are likely video
                print(f"[UDP] Attempting to process as raw video data...")
                handler.process_raw_h264_data(data)
    except Exception as e:
        print(f"[ERROR] Error handling UDP packet from {addr}: {e}")
        import traceback
        traceback.print_exc()

def start_udp_server(port=None):
    """Start UDP server for video packets on specified port"""
    if port is None:
        port = JT808_PORT
    
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Increase buffer size for video packets
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB buffer
    
    try:
        udp_socket.bind((HOST, port))
        print(f"[*] UDP server listening on {HOST}:{port} for video packets")
    except OSError as e:
        if e.errno == 98:
            print(f"[WARNING] UDP port {port} already in use, skipping UDP server")
            return
        else:
            raise
    
    while True:
        try:
            data, addr = udp_socket.recvfrom(65507)  # Max UDP packet size
            handle_udp_video_packet(data, addr, port)
        except Exception as e:
            print(f"[ERROR] UDP server error on port {port}: {e}")
            import traceback
            traceback.print_exc()

def start_udp_servers():
    """Start multiple UDP servers on different ports"""
    ports_to_try = [
        JT808_PORT,
        JT808_PORT + 1,  # Try next port
        int(os.environ.get('VIDEO_UDP_PORT', JT808_PORT + 10)),  # Custom video UDP port
    ]
    
    # Remove duplicates
    ports_to_try = list(dict.fromkeys(ports_to_try))
    
    for port in ports_to_try:
        try:
            threading.Thread(target=start_udp_server, args=(port,), daemon=True).start()
            time.sleep(0.1)  # Small delay between starts
        except Exception as e:
            print(f"[WARNING] Failed to start UDP server on port {port}: {e}")

def start_jt808_server():
    """Start JTT 808/1078 server"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server.bind((HOST, JT808_PORT))
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"[ERROR] Port {JT808_PORT} is already in use!")
            print(f"[INFO] To find what's using the port, run: sudo netstat -tulnp | grep {JT808_PORT}")
            print(f"[INFO] Or use a different port: JT808_PORT=2224 python server.py")
            print(f"[INFO] Or use web_server.py which manages both servers: python web_server.py")
            sys.exit(1)
        else:
            raise
    
    server.listen(5)
    
    print(f"[*] JTT 808/1078 TCP server listening on {HOST}:{JT808_PORT}")
    
    # Start multiple UDP servers on different ports
    print(f"[*] Starting UDP servers on multiple ports...")
    start_udp_servers()
    
    while True:
        conn, addr = server.accept()
        device_ip = addr[0]
        print(f"[CONN] New TCP connection from {addr}")
        
        # Check if this might be a video connection from an existing device
        with connection_lock:
            existing_connections = ip_connections.get(device_ip, [])
            if len(existing_connections) > 0:
                print(f"[CONN] ⚠️ IP {device_ip} already has {len(existing_connections)} connection(s) - this might be a video-only connection!")
                # Try to find device_id from existing connections
                for existing_conn in existing_connections:
                    if existing_conn.device_id:
                        print(f"[CONN] Existing connection has device_id: {existing_conn.device_id}, will try to associate new connection")
        
        # (Some devices open separate connections for video)
        handler = DeviceHandler(conn, addr)
        thread = threading.Thread(target=handler.run, daemon=True)
        thread.start()
        
        # Log connection count
        with connection_lock:
            total_by_id = sum(len(conns) for conns in device_connections.values())
            total_by_ip = sum(len(conns) for conns in ip_connections.values())
            print(f"[CONN] Total active connections: {total_by_id} by device ID, {total_by_ip} by IP")

if __name__ == "__main__":
    start_jt808_server()
