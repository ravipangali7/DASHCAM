"""
Video Stream Manager
Manages video streams from multiple devices and provides streaming to web clients
"""
import threading
import queue
import time
from collections import defaultdict

class VideoStreamManager:
    def __init__(self):
        self.streams = defaultdict(lambda: {
            'frames': queue.Queue(maxsize=30),
            'last_update': time.time(),
            'device_info': {}
        })
        self.lock = threading.Lock()
    
    def add_frame(self, device_id, channel, frame_data, metadata=None):
        """Add a video frame from device"""
        stream_key = f"{device_id}_{channel}"
        
        with self.lock:
            stream = self.streams[stream_key]
            stream['last_update'] = time.time()
            if metadata:
                stream['device_info'].update(metadata)
            
            # Add frame to queue (drop old frames if queue is full)
            try:
                stream['frames'].put_nowait((frame_data, time.time()))
            except queue.Full:
                # Remove oldest frame
                try:
                    stream['frames'].get_nowait()
                    stream['frames'].put_nowait((frame_data, time.time()))
                except queue.Empty:
                    pass
    
    def get_frame(self, device_id, channel):
        """Get latest frame for a stream"""
        stream_key = f"{device_id}_{channel}"
        
        with self.lock:
            if stream_key not in self.streams:
                return None
            
            stream = self.streams[stream_key]
            
            # Check if stream is still active (within 30 seconds)
            if time.time() - stream['last_update'] > 30:
                return None
            
            try:
                frame_data, timestamp = stream['frames'].get_nowait()
                return frame_data
            except queue.Empty:
                return None
    
    def get_active_streams(self):
        """Get list of active streams"""
        active = []
        current_time = time.time()
        
        with self.lock:
            for stream_key, stream in self.streams.items():
                if current_time - stream['last_update'] < 30:
                    device_id, channel = stream_key.rsplit('_', 1)
                    active.append({
                        'device_id': device_id,
                        'channel': int(channel),
                        'info': stream['device_info']
                    })
        
        return active
    
    def cleanup_old_streams(self):
        """Remove inactive streams"""
        current_time = time.time()
        
        with self.lock:
            to_remove = []
            for stream_key, stream in self.streams.items():
                if current_time - stream['last_update'] > 60:
                    to_remove.append(stream_key)
            
            for key in to_remove:
                del self.streams[key]

# Global stream manager instance
stream_manager = VideoStreamManager()
