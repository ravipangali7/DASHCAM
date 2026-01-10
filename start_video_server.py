"""
Standalone Video File Server
Starts only the web server for video file playback (no JTT808 server required)
"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import web server components
from http.server import HTTPServer
import json
import urllib.parse
from pathlib import Path
from datetime import datetime

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

class VideoFileHandler:
    def __init__(self):
        pass
    
    def do_GET(self):
        """Handle GET requests"""
        # Parse path and query string
        parsed_path = urllib.parse.urlparse(self.path)
        path = parsed_path.path
        query = parsed_path.query
        
        # Log request
        print(f"[HTTP] GET {self.path} from {self.client_address[0]}")
        
        # Normalize path
        if path != '/' and path.endswith('/'):
            path = path.rstrip('/')
        
        # Remove query string and normalize path
        base_path = path.split('?')[0].rstrip('/') if path != '/' else path
        
        try:
            if base_path == '/' or base_path == '/index.html':
                self.serve_index()
            elif base_path == '/api/videos':
                self.list_video_files()
            elif base_path.startswith('/api/video/'):
                self.serve_video_file()
            else:
                print(f"[HTTP] 404 - Path not found: {path} (base: {base_path})")
                self.send_error(404, f"Path not found: {path}")
        except Exception as e:
            print(f"[ERROR] Error handling GET request for {path}: {e}")
            import traceback
            traceback.print_exc()
            try:
                if not self.wfile.closed:
                    self.send_error(500, f"Internal server error: {e}")
            except:
                pass
    
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
    
    def list_video_files(self):
        """API endpoint to list available video files"""
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
    
    def log_message(self, format, *args):
        """Override to reduce default HTTP server logging noise"""
        pass

# Create handler class dynamically
class VideoServerHandler(VideoFileHandler, object):
    pass

def start_server():
    """Start the video file server"""
    try:
        server = HTTPServer(('0.0.0.0', WEB_PORT), VideoServerHandler)
        print(f"[*] Video File Server listening on http://0.0.0.0:{WEB_PORT}")
        print(f"[*] Access the video player at: http://localhost:{WEB_PORT}")
        print(f"[*] Press Ctrl+C to stop the server")
        server.serve_forever()
    except OSError as e:
        if e.errno == 98 or e.errno == 10048:  # Address already in use (Linux/Windows)
            print(f"[ERROR] Port {WEB_PORT} is already in use!")
            print(f"[INFO] Another server might be running on port {WEB_PORT}")
            print(f"[INFO] Try stopping it or use a different port")
            sys.exit(1)
        else:
            print(f"[ERROR] Failed to start web server: {e}")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[*] Server stopped by user")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print("=" * 60)
    print("Video File Player Server")
    print("=" * 60)
    start_server()
