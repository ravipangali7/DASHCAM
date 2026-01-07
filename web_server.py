"""
Web Server for Video Streaming Interface
Serves HTML interface and provides video streaming via HTTP
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
from video_streamer import stream_manager
from server import start_jt808_server

WEB_PORT = 2223

class StreamingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.serve_index()
        elif self.path == '/api/streams':
            self.list_streams()
        elif self.path.startswith('/api/stream/'):
            self.stream_video()
        elif self.path.startswith('/stream/'):
            self.stream_mjpeg()
        else:
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
        streams = stream_manager.get_active_streams()
        response = json.dumps({'streams': streams})
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(response.encode())
    
    def stream_video(self):
        """Stream video data (H.264 NAL units)"""
        # Parse device and channel from path: /api/stream/{device_id}/{channel}
        parts = self.path.split('/')
        if len(parts) < 4:
            self.send_error(400)
            return
        
        device_id = parts[3]
        channel = int(parts[4]) if len(parts) > 4 else 0
        
        frame = stream_manager.get_frame(device_id, channel)
        
        if frame:
            self.send_response(200)
            self.send_header('Content-type', 'application/octet-stream')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(frame)
        else:
            self.send_error(404, "No video data available")
    
    def stream_mjpeg(self):
        """Stream MJPEG (for browsers that support it)"""
        parts = self.path.split('/')
        if len(parts) < 3:
            self.send_error(400)
            return
        
        device_id = parts[2]
        channel = int(parts[3]) if len(parts) > 3 else 0
        
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
        """Override to reduce logging noise"""
        pass

def start_web_server():
    """Start web server"""
    server = HTTPServer(('0.0.0.0', WEB_PORT), StreamingHandler)
    print(f"[*] Web server listening on http://0.0.0.0:{WEB_PORT}")
    server.serve_forever()

if __name__ == "__main__":
    # Start JTT 808 server in background thread
    jt808_thread = threading.Thread(target=start_jt808_server, daemon=True)
    jt808_thread.start()
    
    # Start web server in main thread
    start_web_server()
