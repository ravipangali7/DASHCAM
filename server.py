"""
JTT 808/1078 Server
Handles device connections and video streaming

Protocol References:
- JTT 808-2013: Terminal communication protocol and data format
- JTT 1078-2016: Video communication protocol for road transport vehicles

Video Streaming Flow (JTT1078):
1. Device connects and registers (0x0100)
2. Device authenticates (0x0102)
3. Server sends video real-time request (0x9101) - Configure video transmission parameters
4. Device acknowledges (0x0001) - Video request accepted
5. Server sends video control command (0x9202) - Start video streaming
6. Device acknowledges (0x0001) - Control command accepted
7. Device sends video data (0x9201) - Actual video packets
"""
import socket
import binascii
import threading
import os
import sys
import time
import struct
from jt808_protocol import JT808Parser, MSG_ID_REGISTER, MSG_ID_HEARTBEAT, MSG_ID_TERMINAL_AUTH, MSG_ID_VIDEO_UPLOAD, MSG_ID_VIDEO_UPLOAD_INIT, MSG_ID_LOCATION_UPLOAD, MSG_ID_TERMINAL_RESPONSE, MSG_ID_TERMINAL_LOGOUT, MSG_ID_VIDEO_REALTIME_REQUEST, MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, MSG_ID_VIDEO_LIST_QUERY, MSG_ID_VIDEO_DOWNLOAD_REQUEST
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
        self.video_control_sent = False  # Track if video control command already sent
        self.video_control_time = None  # Track when video control command was sent
        self.buffer = bytearray()
        self.message_count = 0
        # Frame reassembly buffers for multi-packet video frames
        self.video_frame_buffers = {}  # (channel, frame_id) -> list of packets
        # Raw data capture for unparseable data
        self.raw_data_buffer = bytearray()
        self.raw_data_count = 0
        # Stored video list from device
        self.stored_videos = []  # List of stored videos from device
        self.video_list_received = False  # Track if video list has been received
        # Video list response buffering for fragmented messages
        self.video_list_buffer = bytearray()  # Buffer for accumulating video list data
        self.video_list_count = None  # Store the video count from first message
        self.video_list_expected_size = None  # Expected total size
        self.video_list_received_time = None  # Track when first fragment arrived
        self.video_list_buffer_timeout = 10.0  # Timeout in seconds for incomplete buffers
        # Stored video download tracking
        self.video_downloads = {}  # video_id -> download state
        self.video_download_buffers = {}  # video_id -> list of chunks
        # Video list query tracking (for cooldown)
        self._video_list_query_attempted = None  # Timestamp of last query attempt
        self._video_list_query_cooldown = 30.0  # Cooldown in seconds between queries
        self._location_message_count = 0  # Count location messages received
        self._video_list_query_in_progress = False  # Track if query is currently in progress
        self._timeout_check_thread = None  # Background thread for timeout checking
        
    def handle_message(self, msg, raw_message=None):
        """Handle parsed JTT 808/1078 messages"""
        msg_id = msg['msg_id']
        phone = msg['phone']
        msg_seq = msg['msg_seq']
        body = msg.get('body', b'')
        
        self.message_count += 1
        
        # Log all 0x1205 messages for video list debugging
        if msg_id == MSG_ID_VIDEO_UPLOAD:
            msg_attr = msg.get('msg_attr', 0)
            # Check fragmentation flag (bit 13 of message attribute)
            is_fragmented = (msg_attr & 0x2000) != 0
            packet_total = ((msg_attr >> 14) & 0x3FF) if is_fragmented else 1
            packet_number = ((msg_attr >> 10) & 0xF) if is_fragmented else 1
            
            print(f"[MSG 0x1205] Received 0x1205 message from {phone}, body_size={len(body)} bytes, seq={msg_seq}")
            print(f"[MSG 0x1205] Message attr=0x{msg_attr:04X}, fragmented={is_fragmented}, packet={packet_number}/{packet_total}")
            if len(body) > 0:
                # Show first few bytes as hex for debugging
                preview = binascii.hexlify(body[:min(20, len(body))]).decode()
                print(f"[MSG 0x1205] Body preview (first 20 bytes): {preview}")
                if len(body) >= 2:
                    # Try to interpret first 2 bytes as video count
                    try:
                        potential_count = struct.unpack('>H', body[0:2])[0]
                        print(f"[MSG 0x1205] First 2 bytes as uint16: {potential_count} (could be video count if < 1000)")
                    except:
                        pass
        
        # Enhanced logging with hex dump for debugging
        print(f"[MSG #{self.message_count}] ID=0x{msg_id:04X}, Phone={phone}, Seq={msg_seq}, BodyLen={len(body)}")
        
        # Comprehensive hex dump with byte structure
        if raw_message:
            hex_dump = binascii.hexlify(raw_message).decode()
            print(f"[HEX FULL] {hex_dump}")
            
            # Show structured byte breakdown for important messages
            if msg_id == MSG_ID_VIDEO_REALTIME_REQUEST:
                print(f"[HEX STRUCT] 0x9101 structure: [7E][ID(2)][Attr(2)][Phone(6)][Seq(2)][Body({len(body)})][Checksum(1)][7E]")
            elif msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL]:
                if len(body) >= 13:
                    print(f"[HEX STRUCT] 0x{msg_id:04X} body: [Channel(1)={body[0]:02X}][DataType(1)={body[1]:02X}][PkgType(1)={body[2]:02X}][Time(6)={binascii.hexlify(body[3:9]).decode()}][Interval(2)={binascii.hexlify(body[9:11]).decode()}][Size(2)={binascii.hexlify(body[11:13]).decode()}][Data({len(body)-13})]")
        
        if raw_message and len(raw_message) <= 200:  # Show formatted hex for small messages
            hex_dump = binascii.hexlify(raw_message).decode()
            # Format as bytes with spacing
            formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, len(hex_dump), 2)])
            print(f"[HEX FORMATTED] {formatted_hex[:150]}{'...' if len(formatted_hex) > 150 else ''}")
        
        # Register device if not already registered
        if self.device_id is None:
            self.device_id = phone
            device_ip = self.addr[0] if self.addr else 'unknown'
            
            with connection_lock:
                # Check if there are existing connections from this IP that might have device info
                existing_conns = ip_connections.get(device_ip, [])
                for existing_conn in existing_conns:
                    if existing_conn.device_id and existing_conn.device_id == phone:
                        # Same device, share video request state
                        if existing_conn.video_request_sent:
                            self.video_request_sent = True
                            self.video_request_attempts = existing_conn.video_request_attempts.copy()
                            print(f"[CONN] Sharing video request state from existing connection for {phone}")
                        break
                
                # Track by device ID
                if phone not in device_connections:
                    device_connections[phone] = []
                device_connections[phone].append(self)
                
                # Track by IP address
                if device_ip not in ip_connections:
                    ip_connections[device_ip] = []
                ip_connections[device_ip].append(self)
                
                print(f"[CONN] Device {phone} (IP: {device_ip}) now has {len(device_connections[phone])} connection(s) by ID, {len(ip_connections[device_ip])} by IP")
                
                # Set device_id if not already set (device identified from phone number in message)
                was_new_device = self.device_id is None
                if self.device_id is None:
                    self.device_id = phone
                    print(f"[CONN] Device ID set to {phone} from message")
                    
                    # Query video list after device is identified (if not already received)
                    if was_new_device and not self.video_list_received:
                        print(f"[AUTO QUERY] Device {phone} identified, will query video list after short delay...")
                        def query_after_identification():
                            time.sleep(1.5)  # Wait 1.5 seconds for device to be ready
                            if self.conn and self.device_id == phone and not self.video_list_received:
                                # Check cooldown
                                if (self._video_list_query_attempted is None or 
                                    (time.time() - self._video_list_query_attempted) >= self._video_list_query_cooldown):
                                    print(f"[AUTO QUERY] Sending video list query to identified device {phone}")
                                    self._video_list_query_attempted = time.time()
                                    self.query_video_list(phone, self.message_count)
                                else:
                                    print(f"[AUTO QUERY] Cooldown active, skipping query")
                            else:
                                print(f"[AUTO QUERY] Device state changed, skipping query")
                        
                        threading.Thread(target=query_after_identification, daemon=True).start()
                
                # Alert if multiple connections from same IP
                if len(ip_connections[device_ip]) > 1:
                    print(f"[CONN] ⚠️ Multiple connections ({len(ip_connections[device_ip])}) from IP {device_ip} - might be separate video connection!")
                    # Check if any existing connection has video packets
                    for existing_conn in existing_conns:
                        if existing_conn.video_packets_received:
                            print(f"[CONN] Existing connection from {device_ip} has received video packets - this might be a control connection")
                            break
        
        # Handle terminal general response (0x0001)
        if msg_id == MSG_ID_TERMINAL_RESPONSE:
            response_info = self.parser.parse_terminal_response(body)
            if response_info:
                reply_id = response_info['reply_id']
                print(f"[RESPONSE] Device={phone} acknowledged message ID=0x{reply_id:04X}, "
                      f"Serial={response_info['reply_serial']}, Result={response_info['result_text']}")
                
                # If this is a response to video request (0x9101), send video control command
                if reply_id == MSG_ID_VIDEO_REALTIME_REQUEST:
                    elapsed = None
                    if self.video_request_time:
                        elapsed = time.time() - self.video_request_time
                        print(f"[VIDEO FLOW] Video request response received {elapsed:.2f} seconds after request")
                    
                    if response_info['result_text'] != 'Success/Confirmation':
                        print(f"[WARNING] Video request was not successful, result: {response_info['result_text']}")
                    else:
                        print(f"[VIDEO FLOW] ✓ Video request (0x9101) acknowledged successfully")
                        print(f"[VIDEO FLOW] → Next step: Sending video control command (0x9202)...")
                        
                        # Send video control command (0x9202) to start video streaming
                        if self.conn and not self.video_control_sent:
                            # Get channel from last video request attempt
                            channel = 1  # Default channel
                            if self.video_request_attempts:
                                last_attempt = self.video_request_attempts[-1]
                                channel = last_attempt.get('channel', 1)
                                print(f"[VIDEO FLOW] Using channel={channel} from last video request attempt")
                            
                            # Send control command to start video (control_type=1: Switch code stream)
                            self.send_video_control_command(phone, msg_seq, channel, control_type=1)
                        else:
                            if not self.conn:
                                print(f"[VIDEO FLOW] ⚠️ Cannot send control command: no connection")
                            elif self.video_control_sent:
                                print(f"[VIDEO FLOW] ⚠️ Control command already sent, skipping")
                        
                        # Send a keep-alive heartbeat to maintain connection
                        if self.conn:
                            try:
                                heartbeat = self.parser.build_heartbeat_response(phone, msg_seq + 1)
                                self.conn.send(heartbeat)
                                print(f"[VIDEO FLOW] Sent keep-alive heartbeat after video acknowledgment")
                            except Exception as e:
                                print(f"[VIDEO FLOW] Failed to send heartbeat: {e}")
                
                # If this is a response to video control command (0x9202)
                elif reply_id == MSG_ID_VIDEO_DATA_CONTROL:
                    elapsed = None
                    if self.video_control_time:
                        elapsed = time.time() - self.video_control_time
                        print(f"[VIDEO FLOW] Control command response received {elapsed:.2f} seconds after command")
                    
                    if response_info['result_text'] != 'Success/Confirmation':
                        print(f"[WARNING] Video control command was not successful, result: {response_info['result_text']}")
                    else:
                        print(f"[VIDEO FLOW] ✓ Video control command (0x9202) acknowledged successfully")
                        print(f"[VIDEO FLOW] → Next step: Waiting for video data packets (0x9201)...")
                        self.video_control_time = time.time()
                        # Now device should start sending video data (0x9201)
                        print(f"[VIDEO FLOW] Monitoring for video packets on TCP connection and UDP port {JT808_PORT}")
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
        # JTT808 Protocol Format (Message Body):
        # - Bytes 0-1: Province ID (2 bytes)
        # - Bytes 2-3: City/County ID (2 bytes)
        # - Bytes 4-8: Manufacturer ID (5 bytes, ASCII)
        # - Bytes 9-28: Terminal model (20 bytes, ASCII, null-padded)
        # - Bytes 29-44: Terminal ID (16 bytes, ASCII, null-padded)
        # - Byte 45: License plate color (1 byte)
        # - Bytes 46+: License plate number (variable, ASCII)
        elif msg_id == MSG_ID_REGISTER:
            print(f"[+] Device registration from {phone}")
            was_new_device = self.device_id is None
            self.device_id = phone
            response = self.parser.build_register_response(phone, msg_seq, 0)
            self.conn.send(response)
            print(f"[TX] Registration response sent")
            
            # Query video list after registration (device is now identified)
            if was_new_device:
                print(f"[AUTO QUERY] New device {phone} registered, will query video list after short delay...")
                def query_after_registration():
                    time.sleep(2.0)  # Wait 2 seconds for device to be ready
                    if self.conn and self.device_id == phone and not self.video_list_received:
                        print(f"[AUTO QUERY] Sending video list query to newly registered device {phone}")
                        self.query_video_list(phone, self.message_count)
                    else:
                        print(f"[AUTO QUERY] Device state changed, skipping query")
                
                threading.Thread(target=query_after_registration, daemon=True).start()
        
        # Handle heartbeat (0x0002)
        elif msg_id == MSG_ID_HEARTBEAT:
            response = self.parser.build_heartbeat_response(phone, msg_seq)
            self.conn.send(response)
            print(f"[TX] Heartbeat response sent")
        
        # Handle authentication (0x0102)
        # JTT808 Protocol Format (Message Body):
        # - Bytes 0-15: Authentication code (16 bytes, ASCII, null-padded)
        # Note: Some devices send minimal body (1 byte)
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
            
            # Automatically query video list after successful authentication
            if not was_authenticated:
                print(f"[AUTO QUERY] Device {phone} authenticated, automatically querying video list...")
                # Wait a short moment for device to be ready, then query
                def auto_query_video_list():
                    time.sleep(1.0)  # Wait 1 second for device to be ready
                    if self.conn and self.authenticated:
                        print(f"[AUTO QUERY] Sending automatic video list query to {phone}")
                        self.query_video_list(phone, self.message_count)
                    else:
                        print(f"[AUTO QUERY] Connection lost or device not authenticated, skipping auto query")
                
                threading.Thread(target=auto_query_video_list, daemon=True).start()
            
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
                
                # Increment location message count
                self._location_message_count += 1
                print(f"[LOCATION] Location message count: {self._location_message_count}")
                
                # Query video list if device is active but list not received
                # This works even without authentication (some devices don't authenticate)
                can_query = (
                    self.device_id and  # Device is identified
                    not self.video_list_received and  # Haven't received video list yet
                    self.conn  # Connection is still active
                )
                
                print(f"[AUTO QUERY] Checking conditions: device_id={self.device_id}, video_list_received={self.video_list_received}, conn={bool(self.conn)}, can_query={can_query}")
                
                # Check cooldown
                query_allowed = True
                if self._video_list_query_attempted is not None:
                    elapsed = time.time() - self._video_list_query_attempted
                    if elapsed < self._video_list_query_cooldown:
                        query_allowed = False
                        print(f"[AUTO QUERY] Cooldown active: {elapsed:.1f}s since last query (need {self._video_list_query_cooldown}s)")
                
                # Query after a few location messages (device is clearly active)
                # Or if enough time has passed since last query
                if can_query and query_allowed:
                    # Query after 2-3 location messages to ensure device is active
                    if self._location_message_count >= 2:
                        print(f"[AUTO QUERY] Device {phone} is active ({self._location_message_count} location messages), querying video list...")
                        self._video_list_query_attempted = time.time()
                        
                        def query_after_delay():
                            time.sleep(0.5)  # Small delay to ensure device is ready
                            if self.conn and self.device_id:
                                print(f"[AUTO QUERY] Sending video list query to active device {phone}")
                                self.query_video_list(phone, self.message_count)
                            else:
                                print(f"[AUTO QUERY] Connection lost, skipping query")
                        
                        threading.Thread(target=query_after_delay, daemon=True).start()
                    else:
                        print(f"[AUTO QUERY] Waiting for more location messages ({self._location_message_count}/2)")
                else:
                    if not can_query:
                        print(f"[AUTO QUERY] Cannot query: device_id={self.device_id}, video_list_received={self.video_list_received}, conn={bool(self.conn)}")
                    if not query_allowed:
                        print(f"[AUTO QUERY] Query not allowed due to cooldown")
                
                # Try sending video request after location data (some devices need this)
                if not self.video_request_sent and self.authenticated:
                    print(f"[INFO] Trying video request after location data...")
                    threading.Thread(target=self.try_video_request_after_location, args=(phone, msg_seq), daemon=True).start()
            else:
                print(f"[LOCATION] Failed to parse location data from {phone}")
        
        # Handle video list response (0x1205 as response to 0x9205)
        # Try to detect video list response by structure, not just query flag
        # Note: Some devices send count-only messages (6 bytes) but may not send
        # actual video entries. The buffer logic handles this by waiting for entries
        # and timing out if they don't arrive. Protocol parameters (0xFF for all
        # channels/types, 0xFFFFFFFFFFFF for no time limits) are correct per JTT1078.
        elif msg_id == MSG_ID_VIDEO_UPLOAD:
            # #region agent log
            import json
            try:
                with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"server.py:427","message":"0x1205 message received","data":{"msg_id":hex(msg_id),"body_len":len(body),"video_list_count":self.video_list_count,"query_in_progress":self._video_list_query_in_progress,"buffer_size":len(self.video_list_buffer) if self.video_list_buffer else 0,"received_time":self.video_list_received_time},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            # Check for timeout on existing buffer
            if self.video_list_count is not None and self.video_list_received_time is not None:
                elapsed = time.time() - self.video_list_received_time
                # #region agent log
                try:
                    with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                        f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"server.py:430","message":"Timeout check","data":{"elapsed":elapsed,"timeout":self.video_list_buffer_timeout,"timed_out":elapsed > self.video_list_buffer_timeout},"timestamp":int(time.time()*1000)}) + '\n')
                except: pass
                # #endregion
                if elapsed > self.video_list_buffer_timeout:
                    print(f"[VIDEO LIST] ⚠️ Buffer timeout after {elapsed:.1f}s, expected {self.video_list_expected_size} bytes, got {len(self.video_list_buffer)} bytes")
                    print(f"[VIDEO LIST] Clearing incomplete buffer and trying to parse what we have...")
                    # Try to parse what we have
                    if len(self.video_list_buffer) >= 2:
                        video_list = self.parser.parse_video_list_response(bytes(self.video_list_buffer))
                        if video_list and 'videos' in video_list and len(video_list['videos']) > 0:
                            print(f"[VIDEO LIST] ✓ Parsed partial list: {len(video_list['videos'])} videos from incomplete buffer")
                            self.stored_videos = video_list['videos']
                            self.video_list_received = True
                    # Reset buffer and query state
                    # #region agent log
                    try:
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"server.py:445","message":"Resetting buffer on timeout","data":{"before_query_in_progress":self._video_list_query_in_progress},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    self.video_list_buffer = bytearray()
                    self.video_list_count = None
                    self.video_list_expected_size = None
                    self.video_list_received_time = None
                    self._video_list_query_in_progress = False
                    # #region agent log
                    try:
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"A","location":"server.py:450","message":"Buffer reset complete","data":{"after_query_in_progress":self._video_list_query_in_progress},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    
                    # After timeout, check if new incoming message is a count-only message
                    # This will be handled by the new count detection logic above
            
            # FIRST: Check if this is a new count-only message (even if buffer exists)
            # This handles the case where device sends a new query response while old buffer exists
            if len(body) == 6:
                try:
                    new_count = struct.unpack('>H', body[0:2])[0]
                    remaining = body[2:6]
                    if 0 < new_count <= 1000 and remaining == b'\x00\x00\x00\x00':
                        # Check if this is different from current buffer or buffer timed out
                        buffer_timed_out = False
                        if self.video_list_received_time is not None:
                            elapsed = time.time() - self.video_list_received_time
                            if elapsed > self.video_list_buffer_timeout:
                                buffer_timed_out = True
                        
                        is_new_response = (
                            self.video_list_count is None or  # No buffer exists
                            new_count != self.video_list_count or  # Different count
                            buffer_timed_out  # Buffer timed out
                        )
                        
                        if is_new_response:
                            # New query response - reset buffer
                            print(f"[VIDEO LIST BUFFER] New count message detected: {new_count} videos (resetting buffer)")
                            if self.video_list_count is not None:
                                print(f"[VIDEO LIST BUFFER] Previous buffer had count={self.video_list_count}, replacing with new count={new_count}")
                            
                            # Initialize buffer with count
                            self.video_list_count = new_count
                            self.video_list_buffer = bytearray(body[:2])  # Store just the count
                            # Calculate expected size (try 18-byte format first)
                            self.video_list_expected_size = 2 + (new_count * 18)
                            self.video_list_received_time = time.time()
                            self._video_list_query_in_progress = True
                            
                            # Start background timeout checker if not already running
                            self._start_timeout_checker()
                            
                            print(f"[VIDEO LIST BUFFER] Buffer initialized: count={new_count}, expected_size={self.video_list_expected_size} bytes")
                            print(f"[VIDEO LIST BUFFER] Waiting for {self.video_list_expected_size - 2} more bytes in subsequent messages...")
                            
                            # Acknowledge the count message
                            try:
                                response = self.parser.build_terminal_response(phone, msg_seq, MSG_ID_VIDEO_LIST_QUERY, 0)
                                self.conn.send(response)
                                print(f"[TX] Video list count message acknowledged, waiting for entries...")
                            except Exception as e:
                                print(f"[ERROR] Failed to send acknowledgment: {e}")
                            
                            return  # Don't process as continuation or video data
                except Exception as e:
                    # Not a count message, continue with normal processing
                    pass
            
            # Check if we're already buffering (continuation message)
            if self.video_list_count is not None:
                # Reset timeout timer since we're receiving data
                self.video_list_received_time = time.time()
                
                print(f"[VIDEO LIST BUFFER] Continuation message received: {len(body)} bytes")
                print(f"[VIDEO LIST BUFFER] Current buffer: {len(self.video_list_buffer)} bytes (has count), expected: {self.video_list_expected_size} bytes")
                
                # Check if this continuation message also starts with count (device might repeat it)
                # If so, skip the count bytes and only append the entries
                if len(body) >= 2:
                    try:
                        body_count = struct.unpack('>H', body[0:2])[0]
                        if body_count == self.video_list_count:
                            # This message also has the count, skip it and append rest
                            print(f"[VIDEO LIST BUFFER] Continuation message also contains count ({body_count}), skipping count bytes")
                            self.video_list_buffer.extend(body[2:])  # Skip count, append entries
                        else:
                            # No count in this message, append entire body
                            self.video_list_buffer.extend(body)
                    except:
                        # Can't parse count, just append entire body
                        self.video_list_buffer.extend(body)
                else:
                    # Body too short, append as-is
                    self.video_list_buffer.extend(body)
                
                print(f"[VIDEO LIST BUFFER] Buffer now: {len(self.video_list_buffer)} bytes")
                
                # Check if buffer is complete
                if len(self.video_list_buffer) >= self.video_list_expected_size:
                    print(f"[VIDEO LIST BUFFER] ✓ Buffer complete! Parsing video list...")
                    video_list = self.parser.parse_video_list_response(bytes(self.video_list_buffer))
                    if video_list and 'videos' in video_list:
                        print(f"[VIDEO LIST] ✓ Video list response successfully parsed from {phone}: {video_list['video_count']} videos")
                        self.stored_videos = video_list['videos']
                        self.video_list_received = True
                        
                        # Log video details
                        for video in self.stored_videos:
                            print(f"[VIDEO LIST]   Video {video['index']}: Channel={video['channel']}, "
                                  f"Time={video['start_time']} to {video['end_time']}, "
                                  f"Alarm=0x{video['alarm_type']:08X}, Type={video['video_type']}")
                        
                        # Send response acknowledgment
                        try:
                            response = self.parser.build_terminal_response(phone, msg_seq, MSG_ID_VIDEO_LIST_QUERY, 0)
                            self.conn.send(response)
                            print(f"[TX] Video list response acknowledged")
                        except Exception as e:
                            print(f"[ERROR] Failed to send video list acknowledgment: {e}")
                        
                        # Clear buffer and reset query state
                        self.video_list_buffer = bytearray()
                        self.video_list_count = None
                        self.video_list_expected_size = None
                        self.video_list_received_time = None
                        self._video_list_query_in_progress = False
                        self._stop_timeout_checker()
                        return
                    else:
                        print(f"[VIDEO LIST BUFFER] Parsing failed even with complete buffer")
                        print(f"[VIDEO LIST BUFFER] Buffer content (first 50 bytes): {binascii.hexlify(self.video_list_buffer[:50]).decode()}")
                        # Reset buffer on parse failure
                        self.video_list_buffer = bytearray()
                        self.video_list_count = None
                        self.video_list_expected_size = None
                        self.video_list_received_time = None
                        self._video_list_query_in_progress = False
                        self._stop_timeout_checker()
                else:
                    # Still waiting for more data
                    remaining = self.video_list_expected_size - len(self.video_list_buffer)
                    print(f"[VIDEO LIST BUFFER] Still waiting for {remaining} more bytes...")
                    return  # Don't process as video data yet
            
            # Check if this is a new count-only message (first fragment)
            # Device sends 6-byte message: count (2 bytes) + 4 bytes of zeros
            if len(body) == 6 and len(body) >= 2:
                try:
                    video_count = struct.unpack('>H', body[0:2])[0]
                    # Check if remaining bytes are zeros (typical pattern)
                    remaining_bytes = body[2:6]
                    if 0 < video_count <= 1000 and remaining_bytes == b'\x00\x00\x00\x00':
                        print(f"[VIDEO LIST BUFFER] Detected count-only message: {video_count} videos")
                        print(f"[VIDEO LIST BUFFER] Initializing buffer, expecting video entries in subsequent messages")
                        
                        # Initialize buffer with count
                        self.video_list_count = video_count
                        self.video_list_buffer = bytearray(body[:2])  # Store just the count
                        # Calculate expected size (try 18-byte format first)
                        self.video_list_expected_size = 2 + (video_count * 18)
                        self.video_list_received_time = time.time()
                        self._video_list_query_in_progress = True
                        
                        # Start background timeout checker
                        self._start_timeout_checker()
                        
                        print(f"[VIDEO LIST BUFFER] Buffer initialized: count={video_count}, expected_size={self.video_list_expected_size} bytes")
                        print(f"[VIDEO LIST BUFFER] Waiting for {self.video_list_expected_size - 2} more bytes in subsequent messages...")
                        
                        # Acknowledge the count message
                        try:
                            response = self.parser.build_terminal_response(phone, msg_seq, MSG_ID_VIDEO_LIST_QUERY, 0)
                            self.conn.send(response)
                            print(f"[TX] Video list count message acknowledged, waiting for entries...")
                        except Exception as e:
                            print(f"[ERROR] Failed to send acknowledgment: {e}")
                        
                        return  # Don't process as video data
                except:
                    pass
            
            # Check if this could be a complete video list response (non-fragmented)
            # Video list characteristics:
            # 1. Small body size (typically < 1000 bytes, but can be larger with many videos)
            # 2. Starts with 2-byte video count (big-endian)
            # 3. Body length should be: 2 + (video_count * entry_size)
            # 4. Entry size is typically 18 bytes (or 22 bytes with file size)
            
            is_potential_video_list = False
            detection_reason = ""
            
            if len(body) >= 2:
                # Check if body starts with a reasonable video count
                try:
                    video_count = struct.unpack('>H', body[0:2])[0]
                    # Reasonable video count: 0 to 1000
                    if 0 <= video_count <= 1000:
                        # Check if body size matches expected format
                        # Minimum: 2 bytes (count) + 0 videos = 2 bytes
                        # Maximum reasonable: 2 + (1000 * 22) = 22002 bytes
                        if len(body) >= 2:
                            # Try 18-byte format first
                            expected_size_18 = 2 + (video_count * 18)
                            # Try 22-byte format (with file size)
                            expected_size_22 = 2 + (video_count * 22)
                            
                            # Allow some tolerance (messages might have extra padding or be incomplete)
                            if (abs(len(body) - expected_size_18) <= 10 or 
                                abs(len(body) - expected_size_22) <= 10 or
                                (len(body) < 1000 and video_count == 0)):  # Empty list is small
                                is_potential_video_list = True
                                detection_reason = f"Structure matches video list: count={video_count}, body_size={len(body)}, expected_18={expected_size_18}, expected_22={expected_size_22}"
                except:
                    pass
            
            # Also check if we sent a query (but don't require it)
            query_was_sent = hasattr(self, '_video_list_query_sent') and self._video_list_query_sent
            
            if is_potential_video_list or (query_was_sent and len(body) < 1000):
                print(f"[VIDEO LIST] Detected potential video list response from {phone}")
                print(f"[VIDEO LIST]   Body size: {len(body)} bytes")
                print(f"[VIDEO LIST]   Query was sent: {query_was_sent}")
                print(f"[VIDEO LIST]   Detection reason: {detection_reason if is_potential_video_list else 'Query flag set and small body'}")
                
                # Try to parse as video list
                video_list = self.parser.parse_video_list_response(body)
                if video_list and 'videos' in video_list:
                    print(f"[VIDEO LIST] ✓ Video list response successfully parsed from {phone}: {video_list['video_count']} videos")
                    self.stored_videos = video_list['videos']
                    self.video_list_received = True
                    
                    # Log video details
                    for video in self.stored_videos:
                        print(f"[VIDEO LIST]   Video {video['index']}: Channel={video['channel']}, "
                              f"Time={video['start_time']} to {video['end_time']}, "
                              f"Alarm=0x{video['alarm_type']:08X}, Type={video['video_type']}")
                    
                    # Send response acknowledgment
                    try:
                        response = self.parser.build_terminal_response(phone, msg_seq, MSG_ID_VIDEO_LIST_QUERY, 0)
                        self.conn.send(response)
                        print(f"[TX] Video list response acknowledged")
                    except Exception as e:
                        print(f"[ERROR] Failed to send video list acknowledgment: {e}")
                    
                    return
                else:
                    print(f"[VIDEO LIST] Parsing failed - not a valid video list response")
                    if query_was_sent:
                        print(f"[VIDEO LIST] Query was sent but response doesn't match video list format")
            
            # If not a video list, treat as regular video data
            if query_was_sent:
                print(f"[VIDEO LIST] Received 0x1205 but not a video list (body_size={len(body)}), treating as video data")
            # Fall through to video upload handler
        
        # Handle video upload (0x1205) - JTT 1078 (stored video data)
        # This is actual video data being uploaded from device storage
        # Note: Video list responses are handled above, so this should only be video data
        elif msg_id == MSG_ID_VIDEO_UPLOAD:
            # This should only be reached if the message wasn't identified as a video list above
            # Log that we're treating this as video data
            print(f"[STORED VIDEO] Processing 0x1205 as video data (not video list): body_size={len(body)} bytes")
            
            # This is stored video data upload
            print(f"[STORED VIDEO] Video data received from {phone} (0x1205)")
            video_info = self.parser.parse_video_data(body)
            if video_info:
                channel = video_info['logic_channel']
                video_data = video_info['video_data']
                
                # Check if this is part of a stored video download
                video_key = f"{phone}_{channel}_{video_info.get('time', '')}"
                
                if video_key in self.video_download_buffers:
                    # Append to download buffer
                    self.video_download_buffers[video_key].append(video_data)
                    print(f"[STORED VIDEO] Chunk received: Channel={channel}, ChunkSize={len(video_data)} bytes, "
                          f"TotalChunks={len(self.video_download_buffers[video_key])}")
                else:
                    # New video download, initialize buffer
                    self.video_download_buffers[video_key] = [video_data]
                    self.video_downloads[video_key] = {
                        'device_id': phone,
                        'channel': channel,
                        'status': 'downloading',
                        'start_time': time.time()
                    }
                    print(f"[STORED VIDEO] New video download started: Channel={channel}, FirstChunk={len(video_data)} bytes")
                
                # Stream to browser in real-time via stream manager
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
                
                print(f"[STORED VIDEO] Channel={channel}, Size={len(video_data)} bytes, "
                      f"GPS=({video_info['latitude']:.6f}, {video_info['longitude']:.6f})")
        
        # Handle video upload initialization (0x1206)
        elif msg_id == MSG_ID_VIDEO_UPLOAD_INIT:
            print(f"[STORED VIDEO] Video upload initialization received from {phone} (0x1206)")
            # Device is initiating a stored video upload
            # Parse initialization message if needed
            if len(body) >= 4:
                channel = struct.unpack('>B', body[0:1])[0]
                video_type = struct.unpack('>B', body[1:2])[0]
                start_time_bytes = body[2:8] if len(body) >= 8 else body[2:]
                start_time_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in start_time_bytes[:6]])
                
                video_key = f"{phone}_{channel}_{start_time_str}"
                self.video_downloads[video_key] = {
                    'device_id': phone,
                    'channel': channel,
                    'status': 'initializing',
                    'start_time': time.time(),
                    'video_type': video_type
                }
                self.video_download_buffers[video_key] = []
                self._video_download_in_progress = True
                
                print(f"[STORED VIDEO] Upload init: Channel={channel}, VideoType={video_type}, StartTime={start_time_str}")
                
                # Send acknowledgment
                response = self.parser.build_terminal_response(phone, msg_seq, MSG_ID_VIDEO_UPLOAD_INIT, 0)
                self.conn.send(response)
                print(f"[TX] Video upload init acknowledged")
        
        # Handle real-time video data (0x9201, 0x9202, 0x9206, 0x9207) - JTT 1078
        # Note: 0x9202 can be either:
        # - Video control command (when sent TO device to start/stop video) - 4 bytes
        # - Video data message (when received FROM device with video data) - 13+ bytes
        # This handler processes 0x9202 as video data when received from device
        elif msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, 0x9206, 0x9207]:
            # Check if 0x9202 is a control command (4 bytes) or video data (13+ bytes)
            if msg_id == MSG_ID_VIDEO_DATA_CONTROL and len(body) == 4:
                print(f"[VIDEO] Received 0x9202 control command response (4 bytes) - not video data")
                # This might be a response to our control command, or device sending control back
                # Don't treat as video data
            else:
                # This is actual video data
                # Mark that we've received video packets
                if not self.video_packets_received:
                    self.video_packets_received = True
                    if self.video_request_time:
                        elapsed = time.time() - self.video_request_time
                        print(f"[VIDEO] ✓✓✓ FIRST VIDEO PACKET RECEIVED after {elapsed:.2f} seconds! ✓✓✓")
                    if self.video_control_time:
                        elapsed = time.time() - self.video_control_time
                        print(f"[VIDEO] First packet received {elapsed:.2f} seconds after control command")
                
                print(f"[VIDEO] ✓✓✓ Real-time video data received from {phone} (0x{msg_id:04X}) ✓✓✓")
                print(f"[VIDEO] Body length: {len(body)} bytes")
                
                # Show first few bytes for debugging
                if len(body) > 0:
                    hex_preview = binascii.hexlify(body[:min(20, len(body))]).decode()
                    formatted_hex = ' '.join([hex_preview[i:i+2] for i in range(0, len(hex_preview), 2)])
                    print(f"[VIDEO] First bytes: {formatted_hex}")
                
                video_info = self.parse_realtime_video_data(body, msg_id)
                if video_info:
                    channel = video_info['logic_channel']
                    package_type = video_info.get('package_type', 1)
                    video_data = video_info['video_data']
                    timestamp = video_info.get('timestamp', '')
                    data_type = video_info.get('data_type', 'N/A')
                    
                    data_type_names = {0: 'I-frame', 1: 'P-frame', 2: 'B-frame', 3: 'Audio'}
                    data_type_str = data_type_names.get(data_type, f'Unknown({data_type})')
                    
                    print(f"[VIDEO] Parsed: Channel={channel}, DataType={data_type_str}, "
                          f"PackageType={package_type}, VideoSize={len(video_data)} bytes, Timestamp={timestamp}")
                    
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
                        
                        print(f"[VIDEO] ✓✓✓ Frame added to stream - Device={phone}, Channel={channel}, "
                              f"DataType={data_type_str}, Size={len(video_data)} bytes ✓✓✓")
                else:
                    print(f"[VIDEO] ✗ Failed to parse video data from {phone}")
                    print(f"[VIDEO] Body length: {len(body)} bytes")
                    if len(body) > 0:
                        hex_preview = binascii.hexlify(body[:min(50, len(body))]).decode()
                        formatted_hex = ' '.join([hex_preview[i:i+2] for i in range(0, len(hex_preview), 2)])
                        print(f"[VIDEO] Body hex (first 50 bytes): {formatted_hex}")
        
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
    
    def send_video_control_command(self, phone, msg_seq, channel, control_type=1, data_type=0xFF, stream_type=0xFF):
        """
        Send video control command (0x9202) to start/stop video streaming
        
        JTT1078 Protocol Format (Message Body):
        - Byte 0: Control type (1 byte)
          0 = Close all channels
          1 = Switch code stream (used to start video transmission)
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
        
        Protocol Flow:
        1. Send 0x9101 (Video Real-time Request) - Configure parameters
        2. Device acknowledges 0x9101
        3. Send 0x9202 (Video Control Command) - Start streaming (control_type=1)
        4. Device acknowledges 0x9202
        5. Device starts sending 0x9201 (Video Data)
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            channel: Logical channel number
            control_type: Control type (1=Switch code stream to start video)
            data_type: Data type (0xFF=all types)
            stream_type: Stream type (0xFF=all streams)
        """
        try:
            if not self.conn:
                print(f"[ERROR] Cannot send video control command: no connection")
                return
            
            control_command = self.parser.build_video_control_command(
                phone=phone,
                msg_seq=msg_seq + 1,
                control_type=control_type,
                channel=channel,
                data_type=data_type,
                stream_type=stream_type
            )
            
            self.conn.send(control_command)
            self.video_control_sent = True
            self.video_control_time = time.time()
            
            hex_dump = binascii.hexlify(control_command).decode()
            formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, len(hex_dump), 2)])
            print(f"[TX] Video control command (0x9202) sent to {phone}: Channel={channel}, ControlType={control_type}")
            print(f"[TX HEX] Complete message: {formatted_hex}")
            print(f"[TX STRUCT] Message structure: [7E][ID=9202(2)][Attr(2)][Phone={phone}(6)][Seq(2)][Body(4)][Checksum(1)][7E]")
        except Exception as e:
            print(f"[ERROR] Failed to send video control command: {e}")
            import traceback
            traceback.print_exc()
    
    def query_video_list(self, phone, msg_seq):
        """
        Query video list from device (0x9205)
        
        Note: This method does NOT require authentication. Some devices allow
        video list queries without authentication, especially if they're already
        connected and sending location data.
        """
        try:
            print(f"[VIDEO LIST QUERY] Starting video list query for device {phone}, msg_seq={msg_seq}")
            print(f"[VIDEO LIST QUERY] Authentication status: {self.authenticated} (not required for query)")
            
            # Check if a query is already in progress
            # #region agent log
            import json
            try:
                with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"server.py:949","message":"query_video_list entry","data":{"query_in_progress":self._video_list_query_in_progress,"video_list_count":self.video_list_count,"received_time":self.video_list_received_time},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            if self._video_list_query_in_progress:
                # Check if buffer has timed out
                buffer_timed_out = False
                # Check timeout: if received_time is None or elapsed > timeout, consider it timed out
                if self.video_list_received_time is None:
                    # No timestamp means buffer was reset, consider it timed out
                    buffer_timed_out = True
                    print(f"[VIDEO LIST QUERY] Previous query has no timestamp, resetting and allowing new query")
                else:
                    elapsed = time.time() - self.video_list_received_time
                    # #region agent log
                    try:
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"server.py:985","message":"Checking timeout in query_video_list","data":{"elapsed":elapsed,"timeout":self.video_list_buffer_timeout,"timed_out":elapsed > self.video_list_buffer_timeout},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    if elapsed > self.video_list_buffer_timeout:
                        buffer_timed_out = True
                        print(f"[VIDEO LIST QUERY] Previous query timed out ({elapsed:.1f}s), resetting and allowing new query")
                
                if buffer_timed_out:
                    # Reset buffer state when timeout detected
                    # #region agent log
                    try:
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"server.py:1000","message":"Timeout detected - resetting buffer state","data":{"before_reset":{"query_in_progress":self._video_list_query_in_progress,"buffer_count":self.video_list_count,"received_time":self.video_list_received_time}},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    self.video_list_buffer = bytearray()
                    self.video_list_count = None
                    self.video_list_expected_size = None
                    self.video_list_received_time = None
                    self._video_list_query_in_progress = False
                    # #region agent log
                    try:
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"server.py:1008","message":"Buffer reset complete after timeout","data":{"after_reset":{"query_in_progress":self._video_list_query_in_progress}},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    # Stop timeout checker since buffer is cleared
                    self._stop_timeout_checker()
                else:
                    print(f"[VIDEO LIST QUERY] Query already in progress, skipping duplicate query")
                    # #region agent log
                    try:
                        elapsed = time.time() - self.video_list_received_time if self.video_list_received_time else None
                        with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                            f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"B","location":"server.py:1012","message":"Query blocked - still in progress","data":{"elapsed":elapsed,"timeout":self.video_list_buffer_timeout},"timestamp":int(time.time()*1000)}) + '\n')
                    except: pass
                    # #endregion
                    return False
            
            if not self.conn:
                print(f"[VIDEO LIST QUERY] ERROR: No connection available for device {phone}")
                return False
            
            # Check if connection is still active
            try:
                # Try to get socket info to verify connection
                self.conn.getpeername()
            except (OSError, AttributeError) as e:
                print(f"[VIDEO LIST QUERY] ERROR: Connection lost for device {phone}: {e}")
                return False
            
            # Reset buffer state for new query
            print(f"[VIDEO LIST QUERY] Resetting buffer state for new query...")
            # #region agent log
            try:
                with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"server.py:975","message":"Resetting buffer before new query","data":{"before_query_in_progress":self._video_list_query_in_progress},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            self.video_list_buffer = bytearray()
            self.video_list_count = None
            self.video_list_expected_size = None
            self.video_list_received_time = None
            self._video_list_query_in_progress = True
            # #region agent log
            try:
                with open(r'c:\Mine\Projects\DASHCAM\.cursor\debug.log', 'a') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"run1","hypothesisId":"D","location":"server.py:980","message":"Buffer reset complete, query_in_progress set","data":{"after_query_in_progress":self._video_list_query_in_progress},"timestamp":int(time.time()*1000)}) + '\n')
            except: pass
            # #endregion
            
            print(f"[VIDEO LIST QUERY] Building query message...")
            video_list_query = self.parser.build_video_list_query(phone, msg_seq + 1)
            
            if not video_list_query:
                print(f"[VIDEO LIST QUERY] ERROR: Failed to build query message")
                return False
            
            # Log hex dump of the message
            hex_dump = binascii.hexlify(video_list_query).decode()
            formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, min(len(hex_dump), 100), 2)])
            print(f"[VIDEO LIST QUERY] Sending query message ({len(video_list_query)} bytes)")
            print(f"[VIDEO LIST QUERY] Message hex (first 100 bytes): {formatted_hex}{'...' if len(hex_dump) > 100 else ''}")
            
            self.conn.send(video_list_query)
            self._video_list_query_sent = True
            self._video_list_query_time = time.time()
            
            print(f"[TX] Video list query (0x9205) sent to {phone}, message size: {len(video_list_query)} bytes")
            print(f"[VIDEO LIST QUERY] Query sent successfully, waiting for response...")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to send video list query to {phone}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _start_timeout_checker(self):
        """Start background thread to check for buffer timeouts"""
        if self._timeout_check_thread is not None and self._timeout_check_thread.is_alive():
            return  # Already running
        
        def check_timeout():
            while self._video_list_query_in_progress and self.video_list_received_time is not None:
                time.sleep(2)  # Check every 2 seconds
                if not self._video_list_query_in_progress:
                    break
                if self.video_list_received_time is None:
                    break
                elapsed = time.time() - self.video_list_received_time
                if elapsed > self.video_list_buffer_timeout:
                    print(f"[VIDEO LIST TIMEOUT] Proactive timeout detected ({elapsed:.1f}s), resetting buffer")
                    # Reset buffer state
                    self.video_list_buffer = bytearray()
                    self.video_list_count = None
                    self.video_list_expected_size = None
                    self.video_list_received_time = None
                    self._video_list_query_in_progress = False
                    break
        
        self._timeout_check_thread = threading.Thread(target=check_timeout, daemon=True)
        self._timeout_check_thread.start()
    
    def _stop_timeout_checker(self):
        """Stop the timeout checker thread"""
        self._timeout_check_thread = None
    
    def request_video_download(self, phone, msg_seq, video_info):
        """
        Request stored video download from device (0x9102)
        
        Args:
            phone: Device phone number
            msg_seq: Message sequence number
            video_info: Dictionary with video metadata (channel, start_time, end_time, alarm_type, video_type)
        """
        try:
            if not self.conn:
                print(f"[ERROR] Cannot request video download: no connection")
                return False
            
            channel = video_info.get('channel', 1)
            start_time = video_info.get('start_time', '')
            end_time = video_info.get('end_time', '')
            alarm_type = video_info.get('alarm_type', 0)
            video_type = video_info.get('video_type', 0)
            storage_type = video_info.get('storage_type', 0)
            
            if not start_time or not end_time:
                print(f"[ERROR] Start time and end time required for video download")
                return False
            
            download_request = self.parser.build_video_download_request(
                phone=phone,
                msg_seq=msg_seq + 1,
                channel=channel,
                start_time=start_time,
                end_time=end_time,
                alarm_type=alarm_type,
                video_type=video_type,
                storage_type=storage_type
            )
            
            self.conn.send(download_request)
            
            # Mark download as in progress
            video_key = f"{phone}_{channel}_{start_time}"
            self.video_downloads[video_key] = {
                'device_id': phone,
                'channel': channel,
                'status': 'requested',
                'start_time': time.time(),
                'video_info': video_info
            }
            self.video_download_buffers[video_key] = []
            self._video_download_in_progress = True
            
            print(f"[TX] Video download request (0x9102) sent to {phone}: Channel={channel}, "
                  f"Time={start_time} to {end_time}")
            return True
        except Exception as e:
            print(f"[ERROR] Failed to send video download request: {e}")
            import traceback
            traceback.print_exc()
            return False
    
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
                    print(f"[VIDEO FLOW] → Step 1: Video streaming request (0x9101) sent to {phone}")
                    print(f"[VIDEO FLOW]   Configuration: IP={server_ip}, Port={video_port}, {config['desc']}")
                    hex_dump = binascii.hexlify(video_request).decode()
                    formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, len(hex_dump), 2)])
                    print(f"[TX HEX] Complete message: {formatted_hex}")
                    print(f"[TX STRUCT] Message structure: [7E][ID=9101(2)][Attr(2)][Phone={phone}(6)][Seq(2)][Body(12)][Checksum(1)][7E]")
                    
                    # Start a thread to check if video arrives, if not try alternative configs
                    threading.Thread(target=self.check_video_and_retry, args=(phone, msg_seq, server_ip, video_port, configs_to_try[1:]), daemon=True).start()
                else:
                    print(f"[VIDEO FLOW] ✗ Cannot send video request: no connection")
            except Exception as e:
                print(f"[ERROR] Failed to send video request: {e}")
                import traceback
                traceback.print_exc()
                
        except Exception as e:
            print(f"[ERROR] Error in try_video_request: {e}")
    
    def check_video_and_retry(self, phone, msg_seq, server_ip, video_port, alternative_configs):
        """Check if video packets arrive, if not try alternative configurations"""
        # Wait 5 seconds to see if video packets arrive
        wait_time = 5
        print(f"[VIDEO FLOW] Waiting {wait_time} seconds for video packets...")
        time.sleep(wait_time)
        
        if not self.video_packets_received:
            print(f"[VIDEO FLOW] ⚠️ No video packets received after {wait_time} seconds")
            print(f"[VIDEO FLOW] Checking connection status...")
            print(f"[VIDEO FLOW] - Video request sent: {self.video_request_sent}")
            print(f"[VIDEO FLOW] - Video control sent: {self.video_control_sent}")
            print(f"[VIDEO FLOW] - Connection active: {self.conn is not None}")
            
            if alternative_configs and self.conn:
                print(f"[VIDEO FLOW] → Trying alternative configuration...")
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
                    print(f"[VIDEO FLOW] Retry video request sent: {config['desc']}")
                except Exception as e:
                    print(f"[VIDEO FLOW] ✗ Failed to send retry video request: {e}")
            else:
                if not alternative_configs:
                    print(f"[VIDEO FLOW] No more alternative configurations to try")
                if not self.conn:
                    print(f"[VIDEO FLOW] Connection lost, cannot retry")
        else:
            print(f"[VIDEO FLOW] ✓ Video packets are being received!")
    
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
    
    def validate_video_data_format(self, body, msg_id):
        """
        Validate video data message format against JTT1078 specification
        
        Note: 0x9202 can be either a control command (4 bytes) or video data (13+ bytes)
        This function only validates video data format, not control commands.
        
        Returns: (is_valid, errors_list)
        """
        errors = []
        
        # 0x9202 can be a control command (4 bytes) - skip validation for that
        if msg_id == MSG_ID_VIDEO_DATA_CONTROL and len(body) == 4:
            # This is a control command, not video data - validation not applicable
            return (True, [])
        
        # Minimum size for video data: Channel(1) + DataType(1) + PackageType(1) + Timestamp(6) + Interval(2) + Size(2) = 13 bytes
        if len(body) < 13:
            errors.append(f"Body too short: {len(body)} bytes (minimum 13)")
            return (False, errors)
        
        # Validate channel (typically 0-127)
        channel = body[0]
        if channel > 127:
            errors.append(f"Channel out of typical range: {channel}")
        
        # Validate data type (0=I-frame, 1=P-frame, 2=B-frame, 3=Audio)
        data_type = body[1]
        if data_type > 3:
            errors.append(f"Data type out of range: {data_type} (expected 0-3)")
        
        # Validate package type (0=start, 1=continuation, 2=end)
        package_type = body[2]
        if package_type > 2:
            errors.append(f"Package type out of range: {package_type} (expected 0-2)")
        
        # Validate timestamp is 6 bytes
        if len(body) < 9:
            errors.append(f"Timestamp incomplete: need 6 bytes starting at offset 3")
        
        return (len(errors) == 0, errors)
    
    def parse_realtime_video_data(self, body, msg_id):
        """
        Parse real-time video data packets (0x9201, 0x9202, 0x9206, 0x9207)
        
        JTT1078 Protocol Format:
        - Byte 0: Logical channel number (1 byte)
        - Byte 1: Data type (1 byte): 0=I-frame, 1=P-frame, 2=B-frame, 3=Audio
        - Byte 2: Package type (1 byte): 0=start, 1=continuation, 2=end
        - Bytes 3-8: Timestamp (6 bytes BCD format: YYMMDDHHmmss)
        - Bytes 9-10: Last frame interval (2 bytes, big-endian)
        - Bytes 11-12: Last frame size (2 bytes, big-endian)
        - Bytes 13+: Video data (variable length)
        """
        import struct
        try:
            # Validate message format first
            is_valid, errors = self.validate_video_data_format(body, msg_id)
            if not is_valid:
                print(f"[PROTOCOL VALIDATION] 0x{msg_id:04X} format errors: {errors}")
                if len(body) < 13:
                    return None  # Can't parse if too short
            
            # Minimum body size: 3 (header) + 6 (timestamp) + 2 (interval) + 2 (size) = 13 bytes
            if len(body) < 13:
                print(f"[PROTOCOL] Video data body too short: {len(body)} bytes (minimum 13)")
                return None
            
            # Parse real-time video packet format
            logic_channel = body[0]
            data_type = body[1]  # 0=I-frame, 1=P-frame, 2=B-frame, 3=Audio
            package_type = body[2]  # 0=start, 1=continuation, 2=end
            
            # Parse timestamp (BCD format, 6 bytes: YYMMDDHHmmss) - JTT1078 standard
            timestamp_bytes = body[3:9]  # Changed from 8 bytes to 6 bytes
            if len(timestamp_bytes) == 6:
                timestamp_str = ''.join([f'{b >> 4}{b & 0x0F}' for b in timestamp_bytes])
            else:
                timestamp_str = ''
                print(f"[PROTOCOL] Warning: Timestamp bytes incomplete: {len(timestamp_bytes)} bytes")
            
            # Last frame interval (2 bytes, big-endian)
            last_frame_interval = struct.unpack('>H', body[9:11])[0] if len(body) >= 11 else 0
            
            # Last frame size (2 bytes, big-endian)
            last_frame_size = struct.unpack('>H', body[11:13])[0] if len(body) >= 13 else 0
            
            # Video data starts at byte 13 (changed from byte 15)
            video_data = body[13:] if len(body) > 13 else b''
            
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
                        formatted_hex = ' '.join([hex_data[i:i+2] for i in range(0, len(hex_data), 2)])
                        print(f"[PARSE ERROR] Message length={len(message)} bytes")
                        print(f"[PARSE ERROR] Full hex: {formatted_hex}")
                        print(f"[PARSE ERROR] Byte structure: [Start={message[0]:02X}][...{len(message)-2} bytes...][End={message[-1]:02X}]")
                        
                        # Try to identify message structure
                        if len(message) >= 3:
                            potential_id = (message[1] << 8) | message[2] if len(message) > 2 else 0
                            print(f"[PARSE ERROR] Potential message ID: 0x{potential_id:04X}")
                            if message[0] == 0x7E and message[-1] == 0x7E:
                                print(f"[PARSE ERROR] Message has correct start/end flags (0x7E)")
                            else:
                                print(f"[PARSE ERROR] Message flags incorrect: start=0x{message[0]:02X}, end=0x{message[-1]:02X}")
                        
                        # Check if unparseable message contains video data
                        if self.check_raw_video_data(message):
                            print(f"[PARSE ERROR] ⚠️ Unparseable message contains H.264 video data!")
                            print(f"[PARSE ERROR] Attempting to process as raw video...")
                            self.process_raw_h264_data(message)
                        elif len(message) > 100:
                            # Large unparseable messages might be video
                            print(f"[PARSE ERROR] Large unparseable message ({len(message)} bytes) - checking for video patterns...")
                            if self.detect_h264_patterns(message):
                                print(f"[PARSE ERROR] ✓ H.264 pattern detected in unparseable message!")
                                self.process_raw_h264_data(message)
                            elif self.detect_rtp_header(message):
                                print(f"[PARSE ERROR] ✓ RTP header detected in unparseable message!")
                                self.process_rtp_packet(message)
                        
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
        device_ip = addr[0]
        print(f"[UDP] Received {packet_size} bytes from {addr} on port {port or 'default'}")
        
        # Try to find associated device ID from IP address
        device_id = None
        with connection_lock:
            if device_ip in ip_connections:
                for conn in ip_connections[device_ip]:
                    if conn.device_id:
                        device_id = conn.device_id
                        break
        
        if device_id:
            print(f"[UDP] Associated with device: {device_id}")
        
        # Analyze packet size (video packets are typically larger)
        if packet_size > 500:
            print(f"[UDP] ⚠️ Large packet ({packet_size} bytes) - likely video data!")
        elif packet_size > 100:
            print(f"[UDP] Medium packet ({packet_size} bytes) - possibly video data")
        
        # Show hex dump for small packets or first bytes of large packets
        if packet_size <= 100:
            hex_dump = binascii.hexlify(data).decode()
            formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, len(hex_dump), 2)])
            print(f"[UDP HEX] {formatted_hex}")
        else:
            hex_dump = binascii.hexlify(data[:100]).decode()
            formatted_hex = ' '.join([hex_dump[i:i+2] for i in range(0, len(hex_dump), 2)])
            print(f"[UDP HEX] First 100 bytes: {formatted_hex}...")
        
        # Check for raw H.264 patterns first (most common for video)
        handler = DeviceHandler(None, addr)
        if handler.detect_h264_patterns(data):
            print(f"[UDP] ✓✓✓ H.264 pattern detected in UDP packet! ✓✓✓")
            if device_id:
                # Try to process with device ID
                handler.device_id = device_id
            handler.process_raw_h264_data(data)
            return
        
        # Check for RTP header
        if handler.detect_rtp_header(data):
            print(f"[UDP] ✓✓✓ RTP header detected in UDP packet! ✓✓✓")
            if device_id:
                handler.device_id = device_id
            handler.process_rtp_packet(data)
            return
        
        # Try to parse as JTT808 message
        parser = JT808Parser()
        msg = parser.parse_message(data)
        if msg:
            msg_id = msg['msg_id']
            phone = msg.get('phone', device_id or 'Unknown')
            
            print(f"[UDP] Parsed message ID=0x{msg_id:04X} from {phone} at {addr}")
            
            # Handle real-time video data on UDP
            if msg_id in [MSG_ID_VIDEO_DATA, MSG_ID_VIDEO_DATA_CONTROL, 0x9206, 0x9207]:
                # Check if this is a control command (4 bytes) or video data (13+ bytes)
                if msg_id == MSG_ID_VIDEO_DATA_CONTROL and len(msg['body']) == 4:
                    print(f"[UDP] Received 0x9202 control command (not video data)")
                else:
                    print(f"[UDP VIDEO] ✓✓✓ Real-time video data from {phone} at {addr} (0x{msg_id:04X}) ✓✓✓")
                    
                    video_info = handler.parse_realtime_video_data(msg['body'], msg_id)
                    
                    if video_info:
                        channel = video_info['logic_channel']
                        video_data = video_info['video_data']
                        
                        print(f"[UDP VIDEO] Parsed: Channel={channel}, DataType={video_info.get('data_type', 'N/A')}, "
                              f"PackageType={video_info.get('package_type', 'N/A')}, VideoSize={len(video_data)} bytes")
                        
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
                        
                        print(f"[UDP VIDEO] ✓ Frame added to stream - Device={phone}, Channel={channel}, Size={len(video_data)} bytes")
                    else:
                        print(f"[UDP VIDEO] ✗ Failed to parse video data")
                        print(f"[UDP VIDEO] Body length: {len(msg['body'])} bytes")
                        if len(msg['body']) > 0:
                            print(f"[UDP VIDEO] First 20 bytes: {binascii.hexlify(msg['body'][:20]).decode()}")
            else:
                print(f"[UDP] Message ID=0x{msg_id:04X} from {addr} (not video data)")
        else:
            print(f"[UDP] Failed to parse as JTT808 message from {addr}")
            print(f"[UDP] First 50 bytes: {binascii.hexlify(data[:50]).decode()}")
            print(f"[UDP] ⚠️ Unparseable UDP packet - might be raw video data!")
            
            # Try to process as raw video anyway if packet is large enough
            if packet_size > 100:  # Large packets are likely video
                print(f"[UDP] Attempting to process as raw video data...")
                if device_id:
                    handler.device_id = device_id
                handler.process_raw_h264_data(data)
            elif packet_size > 20:
                # Even smaller packets might be video fragments
                print(f"[UDP] Small packet - checking for video patterns...")
                if handler.detect_h264_patterns(data):
                    print(f"[UDP] ✓ H.264 pattern found in small packet!")
                    if device_id:
                        handler.device_id = device_id
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
                        # Pre-associate device_id if we have a strong match
                        # (will be confirmed when device sends registration/auth)
                        break
        
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
