"""
Video Handler
Receives video packets, reassembles frames, and decodes video streams.
"""

import asyncio
import logging
import cv2
import numpy as np
from typing import Dict, Optional, Callable, List
from collections import defaultdict
import time
from jtt1078_parser import JTT1078Parser


logger = logging.getLogger(__name__)


class VideoFrame:
    """Represents a video frame"""
    
    def __init__(self, logic_channel: int, data_type: int, timestamp: str):
        self.logic_channel = logic_channel
        self.data_type = data_type
        self.timestamp = timestamp
        self.packets: List[bytes] = []
        self.complete = False
        self.frame_data: Optional[bytes] = None
    
    def add_packet(self, video_data: bytes, package_type: int):
        """Add a packet to the frame"""
        self.packets.append((package_type, video_data))
        
        # Check if frame is complete
        if package_type == 2:  # Frame end
            # Reassemble frame
            self.packets.sort(key=lambda x: x[0])  # Sort by package type
            self.frame_data = b''.join([p[1] for p in self.packets])
            self.complete = True
    
    def get_frame_data(self) -> Optional[bytes]:
        """Get complete frame data"""
        return self.frame_data


class VideoHandler:
    """Handles video packet processing and decoding"""
    
    def __init__(self, frame_callback: Optional[Callable] = None):
        self.frame_callback = frame_callback
        self.parser = JTT1078Parser()
        self.frame_buffers: Dict[tuple, VideoFrame] = {}  # (device_id, logic_channel, frame_id) -> Frame
        self.decoders: Dict[tuple, cv2.VideoCapture] = {}  # (device_id, logic_channel) -> Decoder
        self.frame_counters: Dict[tuple, int] = defaultdict(int)
        self.last_frame_time: Dict[tuple, float] = {}
    
    def _get_decoder_key(self, device_id: str, logic_channel: int) -> tuple:
        """Get decoder key"""
        return (device_id, logic_channel)
    
    def _get_frame_key(self, device_id: str, logic_channel: int, 
                      frame_id: int) -> tuple:
        """Get frame buffer key"""
        return (device_id, logic_channel, frame_id)
    
    def process_video_packet(self, device_id: str, video_info: Dict, 
                           video_data: bytes) -> Optional[np.ndarray]:
        """
        Process a video packet and return decoded frame if complete
        Returns numpy array (frame) or None
        """
        logic_channel = video_info['logic_channel']
        data_type = video_info['data_type']
        package_type = video_info['package_type']
        timestamp = video_info['timestamp']
        
        # Use timestamp as frame ID (simplified - in real implementation might need better frame tracking)
        frame_id = hash(timestamp) % 1000000
        
        frame_key = self._get_frame_key(device_id, logic_channel, frame_id)
        
        # Get or create frame buffer
        if frame_key not in self.frame_buffers:
            self.frame_buffers[frame_key] = VideoFrame(
                logic_channel, data_type, timestamp
            )
        
        frame = self.frame_buffers[frame_key]
        frame.add_packet(video_data, package_type)
        
        # If frame is complete, decode it
        if frame.complete:
            decoded_frame = self._decode_frame(
                device_id, logic_channel, frame.frame_data, data_type
            )
            
            # Clean up old frame buffers (keep last 10 frames per channel)
            self._cleanup_old_frames(device_id, logic_channel)
            
            return decoded_frame
        
        return None
    
    def _decode_frame(self, device_id: str, logic_channel: int, 
                     frame_data: bytes, data_type: int) -> Optional[np.ndarray]:
        """
        Decode video frame data
        Returns numpy array (BGR image) or None
        """
        decoder_key = self._get_decoder_key(device_id, logic_channel)
        
        try:
            # For H.264 data
            if data_type in [0, 1, 2]:  # Video frame types
                # Try to decode using OpenCV
                # Create a temporary file or use in-memory decoding
                
                # Method 1: Use OpenCV VideoCapture with in-memory data
                # Note: OpenCV doesn't directly support in-memory H.264, so we'll use a workaround
                
                # For now, try to decode as raw H.264
                # In production, you might need to use ffmpeg or other libraries
                
                # Create a decoder if it doesn't exist
                if decoder_key not in self.decoders:
                    # For H.264, we'll use a different approach
                    # Since OpenCV VideoCapture doesn't work well with raw H.264 streams,
                    # we'll use cv2.imdecode for JPEG frames or create a proper H.264 decoder
                    pass
                
                # Try to decode as JPEG first (some devices send JPEG)
                try:
                    nparr = np.frombuffer(frame_data, np.uint8)
                    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                    if img is not None:
                        return img
                except Exception:
                    pass
                
                # For H.264, we need a proper decoder
                # This is a simplified version - in production, use ffmpeg-python or similar
                # For now, return None and log
                logger.warning(f"H.264 decoding not fully implemented for {device_id}:{logic_channel}")
                return None
            
            # For audio data (data_type == 3), skip for now
            elif data_type == 3:
                return None
            
        except Exception as e:
            logger.error(f"Error decoding frame: {e}")
            return None
    
    def _cleanup_old_frames(self, device_id: str, logic_channel: int):
        """Clean up old frame buffers"""
        keys_to_remove = []
        for key in list(self.frame_buffers.keys()):
            if key[0] == device_id and key[1] == logic_channel:
                keys_to_remove.append(key)
        
        # Keep only the most recent frames
        if len(keys_to_remove) > 10:
            keys_to_remove.sort()
            for key in keys_to_remove[:-10]:
                del self.frame_buffers[key]
    
    def process_video_message(self, device_id: str, parsed_message: Dict) -> Optional[np.ndarray]:
        """
        Process a complete video message and return decoded frame
        """
        try:
            body = parsed_message['body']
            video_info = self.parser.parse_video_data(body)
            
            if not video_info:
                return None
            
            video_data = video_info['video_data']
            decoded_frame = self.process_video_packet(device_id, video_info, video_data)
            
            if decoded_frame is not None and self.frame_callback:
                self.frame_callback(device_id, video_info['logic_channel'], decoded_frame)
            
            return decoded_frame
        
        except Exception as e:
            logger.error(f"Error processing video message: {e}")
            return None
    
    def encode_frame_to_jpeg(self, frame: np.ndarray, quality: int = 85) -> bytes:
        """Encode frame to JPEG for web streaming"""
        try:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, quality]
            result, encoded = cv2.imencode('.jpg', frame, encode_params)
            if result:
                return encoded.tobytes()
        except Exception as e:
            logger.error(f"Error encoding frame to JPEG: {e}")
        return None
    
    def cleanup_device(self, device_id: str):
        """Clean up resources for a device"""
        keys_to_remove = []
        for key in list(self.frame_buffers.keys()):
            if key[0] == device_id:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            del self.frame_buffers[key]
        
        decoder_keys_to_remove = []
        for key in list(self.decoders.keys()):
            if key[0] == device_id:
                decoder = self.decoders[key]
                decoder.release()
                decoder_keys_to_remove.append(key)
        
        for key in decoder_keys_to_remove:
            del self.decoders[key]
