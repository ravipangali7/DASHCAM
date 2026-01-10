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
from jt808_protocol import JT808Parser, MSG_ID_REGISTER, MSG_ID_HEARTBEAT, MSG_ID_TERMINAL_AUTH, MSG_ID_VIDEO_UPLOAD, MSG_ID_LOCATION_UPLOAD, MSG_ID_TERMINAL_RESPONSE, MSG_ID_TERMINAL_LOGOUT, MSG_ID_VIDEO_REALTIME_REQUEST, MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL
from video_streamer import stream_manager

HOST = "0.0.0.0"
JT808_PORT = int(os.environ.get('JT808_PORT', 2222))

# Global connection tracking
device_connections = {}  # device_id -> list of connections
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
            with connection_lock:
                if phone not in device_connections:
                    device_connections[phone] = []
                device_connections[phone].append(self)
                print(f"[CONN] Device {phone} now has {len(device_connections[phone])} connection(s)")
        
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
                self.try_video_request(phone, msg_seq)
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
            else:
                print(f"[LOCATION] Failed to parse location data from {phone}")
        
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
    
    def try_video_request(self, phone, msg_seq):
        """Try sending video request with different configurations"""
        try:
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
        print(f"[+] NEW TCP connection from {self.addr}")
        print(f"[CONN] Total active connections: {len([c for c in device_connections.values() for _ in c])}")
        
        while True:
            try:
                data = self.conn.recv(4096)
                if not data:
                    print(f"[-] Device {self.device_id} disconnected")
                    break
                
                # Add to buffer
                self.buffer.extend(data)
                
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
                        # Try to extract message ID manually for debugging
                        if len(message) >= 3:
                            try:
                                potential_msg_id = (message[1] << 8) | message[2] if len(message) > 2 else 0
                                print(f"[PARSE ERROR] Potential message ID: 0x{potential_msg_id:04X}")
                            except:
                                pass
                
            except Exception as e:
                print(f"[ERROR] {e}")
                import traceback
                traceback.print_exc()
                break
        
        if self.conn:
            self.conn.close()
        
        # Remove from connection tracking
        if self.device_id:
            with connection_lock:
                if self.device_id in device_connections:
                    if self in device_connections[self.device_id]:
                        device_connections[self.device_id].remove(self)
                    if len(device_connections[self.device_id]) == 0:
                        del device_connections[self.device_id]
                        print(f"[CONN] Device {self.device_id} has no more connections")
        
        print(f"[-] Connection closed for {self.addr}")

def handle_udp_video_packet(data, addr):
    """Handle UDP video packets"""
    try:
        # Enhanced UDP logging
        print(f"[UDP] Received {len(data)} bytes from {addr}")
        if len(data) <= 100:
            hex_dump = binascii.hexlify(data).decode()
            print(f"[UDP HEX] {hex_dump}")
        
        parser = JT808Parser()
        msg = parser.parse_message(data)
        if msg:
            msg_id = msg['msg_id']
            phone = msg.get('phone', 'Unknown')
            
            print(f"[UDP] Parsed message ID=0x{msg_id:04X} from {phone} at {addr}")
            
            # Handle real-time video data on UDP
            if msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, 0x9206, 0x9207]:
                print(f"[UDP VIDEO] ✓ Real-time video data from {phone} at {addr} (0x{msg_id:04X})")
                
                # Create a temporary handler to parse video data
                handler = DeviceHandler(None, addr)
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
            print(f"[UDP] Failed to parse message from {addr}, first 50 bytes: {binascii.hexlify(data[:50]).decode()}")
    except Exception as e:
        print(f"[ERROR] Error handling UDP packet from {addr}: {e}")
        import traceback
        traceback.print_exc()

def start_udp_server():
    """Start UDP server for video packets"""
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        udp_socket.bind((HOST, JT808_PORT))
        print(f"[*] UDP server listening on {HOST}:{JT808_PORT} for video packets")
    except OSError as e:
        if e.errno == 98:
            print(f"[WARNING] UDP port {JT808_PORT} already in use, skipping UDP server")
            return
        else:
            raise
    
    while True:
        try:
            data, addr = udp_socket.recvfrom(4096)
            handle_udp_video_packet(data, addr)
        except Exception as e:
            print(f"[ERROR] UDP server error: {e}")

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
    
    # Start UDP server in background thread
    udp_thread = threading.Thread(target=start_udp_server, daemon=True)
    udp_thread.start()
    
    while True:
        conn, addr = server.accept()
        print(f"[CONN] New TCP connection from {addr}")
        
        # Check if this might be a video connection from an existing device
        # (Some devices open separate connections for video)
        handler = DeviceHandler(conn, addr)
        thread = threading.Thread(target=handler.run, daemon=True)
        thread.start()
        
        # Log connection count
        with connection_lock:
            total_connections = sum(len(conns) for conns in device_connections.values())
            print(f"[CONN] Total active device connections: {total_connections}")

if __name__ == "__main__":
    start_jt808_server()
