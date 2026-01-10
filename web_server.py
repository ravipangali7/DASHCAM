"""
Web Server for Video Streaming Interface
Serves HTML interface and provides video streaming via HTTP
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import sys
import os

# Error handling for imports
try:
    from video_streamer import stream_manager
except ImportError as e:
    print(f"[ERROR] Failed to import video_streamer: {e}")
    print("[ERROR] Make sure video_streamer.py exists in the same directory")
    sys.exit(1)

try:
    from server import start_jt808_server
except ImportError as e:
    print(f"[ERROR] Failed to import server: {e}")
    print("[ERROR] Make sure server.py exists in the same directory")
    sys.exit(1)

WEB_PORT = 2223

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Log request for debugging (can be disabled if too verbose)
        print(f"[HTTP] GET {self.path} from {self.client_address[0]}")
        
        if self.path == '/' or self.path == '/index.html':
            self.serve_index()
        elif self.path == '/api/streams':
            self.list_streams()
        elif self.path.startswith('/api/stream/'):
            self.stream_video()
        elif self.path.startswith('/stream/'):
            self.stream_mjpeg()
        else:
            print(f"[HTTP] 404 - Path not found: {self.path}")
            self.send_error(404)
    
    def serve_index(self):
        """Serve the main HTML page"""
        try:
            with open('index.html', 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(404, "index.html not found")
    
    def list_streams(self):
        """API endpoint to list active streams"""
        try:
            streams = stream_manager.get_active_streams()
            response = json.dumps({'streams': streams})
            
            print(f"[API] /api/streams - Returning {len(streams)} active stream(s)")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[ERROR] Error in list_streams: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def stream_video(self):
        """Stream video data (H.264 NAL units)"""
        # Parse device and channel from path: /api/stream/{device_id}/{channel}
        # Path structure: /api/stream/{device_id}/{channel}
        # After split('/'): ['', 'api', 'stream', '{device_id}', '{channel}']
        parts = self.path.split('/')
        if len(parts) < 5:
            self.send_error(400, "Invalid path format. Expected: /api/stream/{device_id}/{channel}")
            return
        
        try:
            device_id = parts[3]
            channel = int(parts[4])
        except (ValueError, IndexError) as e:
            self.send_error(400, f"Invalid device_id or channel: {e}")
            return
        
        try:
            frame = stream_manager.get_frame(device_id, channel)
            
            if frame:
                print(f"[API] /api/stream/{device_id}/{channel} - Sending frame ({len(frame)} bytes)")
                self.send_response(200)
                self.send_header('Content-type', 'application/octet-stream')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(frame)
            else:
                print(f"[API] /api/stream/{device_id}/{channel} - No video data available")
                self.send_error(404, "No video data available")
        except Exception as e:
            print(f"[ERROR] Error in stream_video for {device_id}/{channel}: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def stream_mjpeg(self):
        """Stream MJPEG (for browsers that support it)"""
        # Parse device and channel from path: /stream/{device_id}/{channel}
        # Path structure: /stream/{device_id}/{channel}
        # After split('/'): ['', 'stream', '{device_id}', '{channel}']
        parts = self.path.split('/')
        if len(parts) < 4:
            self.send_error(400, "Invalid path format. Expected: /stream/{device_id}/{channel}")
            return
        
        try:
            device_id = parts[2]
            channel = int(parts[3])
        except (ValueError, IndexError) as e:
            self.send_error(400, f"Invalid device_id or channel: {e}")
            return
        
        self.send_response(200)
        self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=--jpgboundary')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        
        # Stream frames
        while True:
            frame = stream_manager.get_frame(device_id, channel)
            if frame:
                try:
                    self.wfile.write(b'--jpgboundary\r\n')
                    self.wfile.write(b'Content-Type: image/jpeg\r\n')
                    self.wfile.write(f'Content-Length: {len(frame)}\r\n\r\n'.encode())
                    self.wfile.write(frame)
                    self.wfile.write(b'\r\n')
                    self.wfile.flush()
                except:
                    break
            else:
                import time
                time.sleep(0.1)
    
    def log_message(self, format, *args):
        """Override to reduce default HTTP server logging noise"""
        # We handle our own logging above, so we suppress the default verbose logging
        pass

def start_web_server():
    """Start web server"""
    try:
        server = HTTPServer(('0.0.0.0', WEB_PORT), StreamingHandler)
        print(f"[*] Web server listening on http://0.0.0.0:{WEB_PORT}")
        print(f"[*] Access the dashboard at: http://localhost:{WEB_PORT} or http://82.180.145.220:{WEB_PORT}")
        server.serve_forever()
    except OSError as e:
        if e.errno == 98:  # Address already in use
            print(f"[ERROR] Port {WEB_PORT} is already in use!")
            print(f"[INFO] To find what's using the port, run: sudo netstat -tulnp | grep {WEB_PORT}")
            print(f"[INFO] Or kill the process using: sudo lsof -ti:{WEB_PORT} | xargs sudo kill -9")
            sys.exit(1)
        else:
            print(f"[ERROR] Failed to start web server: {e}")
            sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Unexpected error starting web server: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print("[*] Starting JTT 808/1078 Video Streaming Server...")
    print("[*] This will start both the JTT808 server (port 2222) and Web server (port 2223)")
    
    # Start JTT 808 server in background thread
    try:
        jt808_thread = threading.Thread(target=start_jt808_server, daemon=True)
        jt808_thread.start()
        print("[*] JTT808 server thread started (will listen on port 2222)")
        # Give it a moment to start
        import time
        time.sleep(0.5)
    except Exception as e:
        print(f"[ERROR] Failed to start JTT808 server thread: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    # Start web server in main thread
    print("[*] Starting web server...")
    start_web_server()
