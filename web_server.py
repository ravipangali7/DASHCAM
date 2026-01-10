"""
Web Server for Video Streaming Interface
Serves HTML interface and provides video streaming via HTTP
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import sys
import os
import urllib.parse
from pathlib import Path
from datetime import datetime

# Error handling for imports (optional - only needed for live streaming)
try:
    from video_streamer import stream_manager
except ImportError as e:
    print(f"[WARNING] Failed to import video_streamer: {e}")
    print("[WARNING] Live streaming features will be disabled")
    stream_manager = None

try:
    from server import start_jt808_server, device_connections, connection_lock
except ImportError as e:
    print(f"[WARNING] Failed to import server: {e}")
    print("[WARNING] JTT808 server will not start - only video file playback available")
    start_jt808_server = None
    device_connections = {}
    connection_lock = None

WEB_PORT = 2223

# Video directory configuration
VIDEO_DIR = os.environ.get('VIDEO_DIR', './videos')
VIDEO_DIR = Path(VIDEO_DIR).resolve()

# Supported video file extensions
VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.h264', '.264'}

# Create video directory if it doesn't exist
if not VIDEO_DIR.exists():
    try:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Created video directory: {VIDEO_DIR}")
    except Exception as e:
        print(f"[WARNING] Failed to create video directory {VIDEO_DIR}: {e}")
        print(f"[WARNING] Video file serving may not work properly")
else:
    print(f"[INFO] Using video directory: {VIDEO_DIR}")

class StreamingHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        """Handle POST requests"""
        self.do_GET()  # Route to do_GET for now, check command in handler
    
    def do_GET(self):
        # Parse path and query string
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query = parsed_path.query
        
        # Log request for debugging
        print(f"[HTTP] GET {self.path} from {self.client_address[0]}")
        print(f"[HTTP] Parsed path: {path}, query: {query}")
        
        # Normalize path (remove trailing slash except for root)
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        
        # Remove query string and normalize path
        base_path = path.split('?')[0]
        if base_path != '/':
            base_path = base_path.rstrip('/')
        
        # Debug logging
        print(f"[HTTP] Original path: '{path}', Normalized base_path: '{base_path}'")
        
        try:
            # Debug: Print all route checks
            print(f"[HTTP] Checking routes for base_path: '{base_path}'")
            
            if base_path == '/' or base_path == '/index.html':
                print(f"[HTTP] Matched root/index route")
                self.serve_index()
            elif base_path == '/api/devices' or base_path.strip() == '/api/devices':
                print(f"[HTTP] ✓ Matched /api/devices route")
                self.list_devices()
            elif base_path.startswith('/api/devices/') and base_path.endswith('/videos'):
                # /api/devices/{device_id}/videos
                if self.command == 'POST':
                    self.query_device_videos()
                else:
                    self.list_device_videos()
            elif base_path.startswith('/api/devices/') and '/videos/' in base_path and '/request' in base_path:
                # /api/devices/{device_id}/videos/{video_id}/request
                self.request_device_video()
            elif base_path.startswith('/api/devices/') and '/videos/' in base_path and '/stream' in base_path:
                # /api/devices/{device_id}/videos/{video_id}/stream
                self.stream_device_video()
            elif base_path == '/api/videos':
                self.list_video_files()
            elif base_path.startswith('/api/video/'):
                self.serve_video_file()
            elif base_path == '/api/streams':
                if stream_manager:
                    self.list_streams()
                else:
                    self.send_error(503, "Live streaming not available")
            elif base_path.startswith('/api/stream/'):
                if stream_manager:
                    self.stream_video()
                else:
                    self.send_error(503, "Live streaming not available")
            elif base_path.startswith('/stream/'):
                if stream_manager:
                    self.stream_mjpeg()
                else:
                    self.send_error(503, "Live streaming not available")
            else:
                print(f"[HTTP] 404 - Path not found: {path} (base: {base_path})")
                print(f"[HTTP] Available routes: /, /index.html, /api/devices, /api/devices/..., /api/videos, /api/video/..., /api/streams, /api/stream/..., /stream/...")
                print(f"[HTTP] Debug: base_path == '/api/devices' is {base_path == '/api/devices'}")
                print(f"[HTTP] Debug: base_path type: {type(base_path)}, repr: {repr(base_path)}")
                self.send_error(404, f"Path not found: {path}")
        except Exception as e:
            print(f"[ERROR] Error handling GET request for {path}: {e}")
            import traceback
            traceback.print_exc()
            try:
                if not self.wfile.closed:
                    self.send_error(500, f"Internal server error: {e}")
            except:
                pass  # Connection may be closed
    
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
    
    def list_devices(self):
        """API endpoint to list connected devices"""
        print(f"[API] list_devices() called")
        try:
            if connection_lock is None or device_connections is None:
                print(f"[API] connection_lock or device_connections not available (connection_lock={connection_lock}, device_connections={device_connections})")
                response = json.dumps({'devices': []})
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response.encode())
                return
            
            devices = []
            with connection_lock:
                for device_id, connections in device_connections.items():
                    if connections:
                        # Get first connection for device info
                        conn = connections[0]
                        device_info = {
                            'device_id': device_id,
                            'ip_address': conn.addr[0] if conn.addr else 'unknown',
                            'connected': True,
                            'authenticated': conn.authenticated,
                            'video_list_received': conn.video_list_received if hasattr(conn, 'video_list_received') else False,
                            'stored_video_count': len(conn.stored_videos) if hasattr(conn, 'stored_videos') else 0
                        }
                        devices.append(device_info)
            
            response = json.dumps({'devices': devices})
            
            print(f"[API] /api/devices - Returning {len(devices)} device(s)")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[ERROR] Error in list_devices: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def query_device_videos(self):
        """API endpoint to query device for stored videos (POST)"""
        try:
            # Parse device_id from path: /api/devices/{device_id}/videos
            parts = self.path.split('/')
            if len(parts) < 5:
                self.send_error(400, "Invalid path format. Expected: /api/devices/{device_id}/videos")
                return
            
            device_id = urllib.parse.unquote(parts[3])
            
            if not connection_lock or not device_connections:
                self.send_error(503, "Device connections not available")
                return
            
            with connection_lock:
                if device_id not in device_connections or not device_connections[device_id]:
                    self.send_error(404, f"Device {device_id} not found")
                    return
                
                conn = device_connections[device_id][0]
                if not conn.conn:
                    self.send_error(503, f"Device {device_id} connection lost")
                    return
                
                # Send video list query to device
                conn.query_video_list(device_id, conn.message_count)
            
            response = json.dumps({
                'status': 'query_sent',
                'device_id': device_id,
                'message': 'Video list query sent to device. Videos will be available shortly.'
            })
            
            print(f"[API] POST /api/devices/{device_id}/videos - Video list query sent")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[ERROR] Error in query_device_videos: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def list_device_videos(self):
        """API endpoint to list stored videos for a device"""
        try:
            # Parse device_id from path: /api/devices/{device_id}/videos
            parts = self.path.split('/')
            if len(parts) < 5:
                self.send_error(400, "Invalid path format. Expected: /api/devices/{device_id}/videos")
                return
            
            device_id = urllib.parse.unquote(parts[3])
            
            if not connection_lock or not device_connections:
                self.send_error(503, "Device connections not available")
                return
            
            with connection_lock:
                if device_id not in device_connections or not device_connections[device_id]:
                    self.send_error(404, f"Device {device_id} not found")
                    return
                
                conn = device_connections[device_id][0]
                stored_videos = conn.stored_videos if hasattr(conn, 'stored_videos') else []
            
            # Format videos for response
            videos = []
            for video in stored_videos:
                videos.append({
                    'id': video.get('index', len(videos)),
                    'channel': video.get('channel', 0),
                    'start_time': video.get('start_time', ''),
                    'end_time': video.get('end_time', ''),
                    'alarm_type': video.get('alarm_type', 0),
                    'video_type': video.get('video_type', 0)
                })
            
            response = json.dumps({'device_id': device_id, 'videos': videos})
            
            print(f"[API] /api/devices/{device_id}/videos - Returning {len(videos)} stored video(s)")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[ERROR] Error in list_device_videos: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def request_device_video(self):
        """API endpoint to request video download from device"""
        try:
            # Parse path: /api/devices/{device_id}/videos/{video_id}/request
            parts = self.path.split('/')
            if len(parts) < 7:
                self.send_error(400, "Invalid path format. Expected: /api/devices/{device_id}/videos/{video_id}/request")
                return
            
            device_id = urllib.parse.unquote(parts[3])
            video_id = int(parts[5])
            
            if not connection_lock or not device_connections:
                self.send_error(503, "Device connections not available")
                return
            
            with connection_lock:
                if device_id not in device_connections or not device_connections[device_id]:
                    self.send_error(404, f"Device {device_id} not found")
                    return
                
                conn = device_connections[device_id][0]
                if not conn.conn:
                    self.send_error(503, f"Device {device_id} connection lost")
                    return
                
                stored_videos = conn.stored_videos if hasattr(conn, 'stored_videos') else []
            
            # Find video by ID
            video = None
            for v in stored_videos:
                if v.get('index') == video_id:
                    video = v
                    break
            
            if not video:
                self.send_error(404, f"Video {video_id} not found for device {device_id}")
                return
            
            # Request video download
            success = conn.request_video_download(device_id, conn.message_count, video)
            
            if success:
                response = json.dumps({
                    'status': 'requested',
                    'device_id': device_id,
                    'video_id': video_id,
                    'message': 'Video download request sent to device'
                })
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response.encode())
            else:
                self.send_error(500, "Failed to send video download request")
        except ValueError as e:
            self.send_error(400, f"Invalid video_id: {e}")
        except Exception as e:
            print(f"[ERROR] Error in request_device_video: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def stream_device_video(self):
        """API endpoint to stream video as it's received from device"""
        try:
            # Parse path: /api/devices/{device_id}/videos/{video_id}/stream
            parts = self.path.split('/')
            if len(parts) < 7:
                self.send_error(400, "Invalid path format. Expected: /api/devices/{device_id}/videos/{video_id}/stream")
                return
            
            device_id = urllib.parse.unquote(parts[3])
            video_id = int(parts[5])
            
            # Stream video chunks as they arrive from device
            # This is a long-lived connection that streams video data
            self.send_response(200)
            self.send_header('Content-type', 'video/mp4')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            # Get device connection
            if not connection_lock or not device_connections:
                self.wfile.write(b'Device not available')
                return
            
            with connection_lock:
                if device_id not in device_connections or not device_connections[device_id]:
                    self.wfile.write(b'Device not found')
                    return
                
                conn = device_connections[device_id][0]
            
            # Stream video chunks from download buffer
            video_key = f"{device_id}_{video_id}"
            # This is a simplified version - in production, you'd want proper chunking
            # For now, we'll use the stream manager which handles real-time streaming
            print(f"[API] Streaming video {video_id} from device {device_id}")
            # The video is already being streamed via stream_manager in server.py
            
        except Exception as e:
            print(f"[ERROR] Error in stream_device_video: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def list_video_files(self):
        """API endpoint to list available video files (local files)"""
        try:
            videos = []
            
            if not VIDEO_DIR.exists():
                print(f"[API] /api/videos - Video directory does not exist: {VIDEO_DIR}")
                response = json.dumps({'videos': []})
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(response.encode())
                return
            
            # Scan video directory for video files
            for file_path in VIDEO_DIR.iterdir():
                if file_path.is_file() and file_path.suffix.lower() in VIDEO_EXTENSIONS:
                    stat = file_path.stat()
                    videos.append({
                        'filename': file_path.name,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
            
            # Sort by modified date (newest first)
            videos.sort(key=lambda x: x['modified'], reverse=True)
            
            response = json.dumps({'videos': videos})
            
            print(f"[API] /api/videos - Returning {len(videos)} video file(s)")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(response.encode())
        except Exception as e:
            print(f"[ERROR] Error in list_video_files: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def serve_video_file(self):
        """Serve video file with range request support for seeking"""
        # Parse path to get filename
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        
        # Parse filename from path: /api/video/{filename}
        parts = path.split('/')
        if len(parts) < 4:
            self.send_error(400, "Invalid path format. Expected: /api/video/{filename}")
            return
        
        try:
            # Decode URL-encoded filename
            filename = urllib.parse.unquote(parts[3])
            
            # Security: Prevent directory traversal
            if '..' in filename or '/' in filename or '\\' in filename:
                self.send_error(400, "Invalid filename")
                return
            
            # Build full file path
            file_path = VIDEO_DIR / filename
            
            # Security: Ensure file is within video directory
            try:
                file_path = file_path.resolve()
                if not str(file_path).startswith(str(VIDEO_DIR.resolve())):
                    self.send_error(403, "Access denied")
                    return
            except:
                self.send_error(400, "Invalid file path")
                return
            
            # Check if file exists
            if not file_path.exists() or not file_path.is_file():
                print(f"[API] /api/video/{filename} - File not found")
                self.send_error(404, "Video file not found")
                return
            
            # Check if it's a video file
            if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
                self.send_error(400, "Not a video file")
                return
            
            # Get file size
            file_size = file_path.stat().st_size
            
            # Handle range requests for video seeking
            range_header = self.headers.get('Range')
            
            if range_header:
                # Parse range header (e.g., "bytes=0-1023" or "bytes=1024-")
                range_match = range_header.replace('bytes=', '').split('-')
                start = int(range_match[0]) if range_match[0] else 0
                end = int(range_match[1]) if range_match[1] and range_match[1] else file_size - 1
                
                # Validate range
                if start < 0 or end >= file_size or start > end:
                    self.send_response(416)  # Range Not Satisfiable
                    self.send_header('Content-Range', f'bytes */{file_size}')
                    self.end_headers()
                    return
                
                # Send partial content
                content_length = end - start + 1
                
                self.send_response(206)  # Partial Content
                self.send_header('Content-Type', self.get_content_type(file_path))
                self.send_header('Content-Length', str(content_length))
                self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                # Read and send the requested range
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk_size = min(8192, remaining)
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                
                print(f"[API] /api/video/{filename} - Sent range {start}-{end} ({content_length} bytes)")
            else:
                # Send entire file
                self.send_response(200)
                self.send_header('Content-Type', self.get_content_type(file_path))
                self.send_header('Content-Length', str(file_size))
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                # Read and send file
                with open(file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                
                print(f"[API] /api/video/{filename} - Sent entire file ({file_size} bytes)")
                
        except Exception as e:
            print(f"[ERROR] Error serving video file {filename}: {e}")
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Internal server error: {e}")
    
    def get_content_type(self, file_path):
        """Get content type based on file extension"""
        ext = file_path.suffix.lower()
        content_types = {
            '.mp4': 'video/mp4',
            '.avi': 'video/x-msvideo',
            '.mkv': 'video/x-matroska',
            '.mov': 'video/quicktime',
            '.wmv': 'video/x-ms-wmv',
            '.flv': 'video/x-flv',
            '.webm': 'video/webm',
            '.m4v': 'video/x-m4v',
            '.h264': 'video/h264',
            '.264': 'video/h264',
        }
        return content_types.get(ext, 'application/octet-stream')
    
    def list_streams(self):
        """API endpoint to list active streams"""
        if not stream_manager:
            self.send_error(503, "Live streaming not available")
            return
            
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
        if not stream_manager:
            self.send_error(503, "Live streaming not available")
            return
            
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
        if not stream_manager:
            self.send_error(503, "Live streaming not available")
            return
            
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
    print("[*] Starting Video File Player Web Server...")
    print("[*] Web server will listen on port 2223")
    
    # Start JTT 808 server in background thread (optional - only if available)
    if start_jt808_server:
        try:
            print("[*] Starting JTT808 server (port 2222) in background...")
            jt808_thread = threading.Thread(target=start_jt808_server, daemon=True)
            jt808_thread.start()
            print("[*] JTT808 server thread started (will listen on port 2222)")
            # Give it a moment to start
            import time
            time.sleep(0.5)
        except Exception as e:
            print(f"[WARNING] Failed to start JTT808 server thread: {e}")
            print("[WARNING] Continuing with video file playback only...")
    else:
        print("[*] JTT808 server not available - video file playback only")
    
    # Start web server in main thread
    print("[*] Starting web server on port 2223...")
    print("[*] Access the video player at: http://localhost:2223")
    start_web_server()
