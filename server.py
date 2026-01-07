"""
JTT 808/1078 Server
Handles device connections and video streaming
"""
import socket
import binascii
import threading
from jt808_protocol import JT808Parser, MSG_ID_REGISTER, MSG_ID_HEARTBEAT, MSG_ID_TERMINAL_AUTH, MSG_ID_VIDEO_UPLOAD
from video_streamer import stream_manager

HOST = "0.0.0.0"
JT808_PORT = 2222

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
        
        # Handle registration (0x0100)
        if msg_id == MSG_ID_REGISTER:
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
    server.bind((HOST, JT808_PORT))
    server.listen(5)
    
    print(f"[*] JTT 808/1078 server listening on {HOST}:{JT808_PORT}")
    
    while True:
        conn, addr = server.accept()
        handler = DeviceHandler(conn, addr)
        thread = threading.Thread(target=handler.run, daemon=True)
        thread.start()

if __name__ == "__main__":
    start_jt808_server()
