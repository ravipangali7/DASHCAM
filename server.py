"""
JTT 808/1078 Server
Handles device connections and video streaming
"""
import socket
import binascii
import threading
import os
import sys
from jt808_protocol import JT808Parser, MSG_ID_REGISTER, MSG_ID_HEARTBEAT, MSG_ID_TERMINAL_AUTH, MSG_ID_VIDEO_UPLOAD, MSG_ID_LOCATION_UPLOAD, MSG_ID_TERMINAL_RESPONSE, MSG_ID_TERMINAL_LOGOUT
from video_streamer import stream_manager

HOST = "0.0.0.0"
JT808_PORT = int(os.environ.get('JT808_PORT', 2222))

class DeviceHandler:
    def __init__(self, conn, addr):
        self.conn = conn
        self.addr = addr
        self.parser = JT808Parser()
        self.device_id = None
        self.authenticated = False
        self.buffer = bytearray()
        
    def handle_message(self, msg):
        """Handle parsed JTT 808/1078 messages"""
        msg_id = msg['msg_id']
        phone = msg['phone']
        msg_seq = msg['msg_seq']
        body = msg['body']
        
        print(f"[MSG] ID=0x{msg_id:04X}, Phone={phone}, Seq={msg_seq}")
        
        # Handle terminal general response (0x0001)
        if msg_id == MSG_ID_TERMINAL_RESPONSE:
            response_info = self.parser.parse_terminal_response(body)
            if response_info:
                print(f"[RESPONSE] Device={phone} acknowledged message ID=0x{response_info['reply_id']:04X}, "
                      f"Serial={response_info['reply_serial']}, Result={response_info['result_text']}")
            else:
                print(f"[RESPONSE] Failed to parse terminal response from {phone}")
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
            self.authenticated = True
            response = self.parser.build_auth_response(phone, msg_seq, 0)
            self.conn.send(response)
            print(f"[TX] Authentication response sent")
        
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
        
        # Handle video upload (0x1205) - JTT 1078
        elif msg_id == MSG_ID_VIDEO_UPLOAD:
            print(f"[VIDEO] Video data received from {phone}")
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
        
        else:
            print(f"[?] Unknown message ID: 0x{msg_id:04X}")
    
    def run(self):
        """Main handler loop"""
        print(f"[+] Device connected from {self.addr}")
        
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
                        self.handle_message(msg)
                    else:
                        hex_data = binascii.hexlify(message).decode()
                        print(f"[PARSE ERROR] {hex_data}")
                
            except Exception as e:
                print(f"[ERROR] {e}")
                import traceback
                traceback.print_exc()
                break
        
        self.conn.close()
        print(f"[-] Connection closed for {self.addr}")

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
    
    print(f"[*] JTT 808/1078 server listening on {HOST}:{JT808_PORT}")
    
    while True:
        conn, addr = server.accept()
        handler = DeviceHandler(conn, addr)
        thread = threading.Thread(target=handler.run, daemon=True)
        thread.start()

if __name__ == "__main__":
    start_jt808_server()
