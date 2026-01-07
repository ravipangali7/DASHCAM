"""
Main application entry point for JTT1078 dashcam live streaming.
"""

import asyncio
import logging
import signal
import sys
from typing import Dict
from device_manager import DeviceManager
from video_handler import VideoHandler
from stream_server import StreamServer
from jtt1078_parser import MessageType
import config


# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT
)
logger = logging.getLogger(__name__)


class DashcamApplication:
    """Main application class"""
    
    def __init__(self):
        self.device_manager = DeviceManager(
            host=config.DEVICE_TCP_HOST,
            tcp_port=config.DEVICE_TCP_PORT,
            udp_port=config.DEVICE_UDP_PORT
        )
        self.video_handler = VideoHandler(frame_callback=self.on_frame_decoded)
        self.stream_server = StreamServer(
            host=config.HTTP_HOST,
            http_port=config.HTTP_PORT,
            ws_port=config.WEBSOCKET_PORT
        )
        self.running = False
    
    def on_frame_decoded(self, device_id: str, channel: int, frame):
        """Callback when a frame is decoded"""
        try:
            # Encode frame to JPEG
            jpeg_data = self.video_handler.encode_frame_to_jpeg(
                frame, quality=config.VIDEO_JPEG_QUALITY
            )
            if jpeg_data:
                # Update stream server
                self.stream_server.update_frame(device_id, channel, jpeg_data)
        except Exception as e:
            logger.error(f"Error in frame callback: {e}")
    
    async def handle_video_message(self, connection, parsed_message: Dict):
        """Handle video upload messages (9201, 9202, 9206, 9207)"""
        try:
            device_id = connection.device_id
            decoded_frame = self.video_handler.process_video_message(
                device_id, parsed_message
            )
            
            if decoded_frame is None:
                # Frame not complete yet, or decoding failed
                pass
            
        except Exception as e:
            logger.error(f"Error handling video message: {e}")
    
    async def handle_video_request(self, connection, parsed_message: Dict):
        """Handle real-time video upload request (9101)"""
        try:
            # Device is requesting to start video upload
            # Respond with acknowledgment
            device_id = connection.device_id
            logger.info(f"Video upload request from {device_id}")
            
            # Send response (1005 - Terminal response)
            response_body = bytes([0x00, 0x00])  # Success
            await connection.send_message(
                MessageType.TERMINAL_RESPONSE,
                response_body
            )
        
        except Exception as e:
            logger.error(f"Error handling video request: {e}")
    
    async def handle_video_control(self, connection, parsed_message: Dict):
        """Handle video upload control (9205)"""
        try:
            device_id = connection.device_id
            body = parsed_message['body']
            
            if len(body) >= 1:
                control_type = body[0]
                logger.info(f"Video control from {device_id}: {control_type}")
            
        except Exception as e:
            logger.error(f"Error handling video control: {e}")
    
    def setup_handlers(self):
        """Setup message handlers"""
        # Video upload request
        self.device_manager.register_handler(
            MessageType.REAL_TIME_VIDEO_UPLOAD_REQUEST,
            self.handle_video_request
        )
        
        # Video upload messages
        self.device_manager.register_handler(
            MessageType.REAL_TIME_VIDEO_UPLOAD_AVI_1,
            self.handle_video_message
        )
        self.device_manager.register_handler(
            MessageType.REAL_TIME_VIDEO_UPLOAD_AVI_2,
            self.handle_video_message
        )
        self.device_manager.register_handler(
            MessageType.REAL_TIME_VIDEO_UPLOAD_H264_1,
            self.handle_video_message
        )
        self.device_manager.register_handler(
            MessageType.REAL_TIME_VIDEO_UPLOAD_H264_2,
            self.handle_video_message
        )
        
        # Video control
        self.device_manager.register_handler(
            MessageType.VIDEO_UPLOAD_CONTROL,
            self.handle_video_control
        )
    
    async def start(self):
        """Start the application"""
        logger.info("Starting JTT1078 Dashcam Streaming Application...")
        
        self.setup_handlers()
        self.running = True
        
        # Start all servers
        await asyncio.gather(
            self.device_manager.start(),
            self.stream_server.start(),
            return_exceptions=True
        )
    
    async def stop(self):
        """Stop the application"""
        logger.info("Stopping application...")
        self.running = False
        
        await self.device_manager.stop()
        await self.stream_server.stop()
        
        logger.info("Application stopped")


async def main():
    """Main entry point"""
    app = DashcamApplication()
    
    # Setup signal handlers for graceful shutdown
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal")
        asyncio.create_task(app.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        await app.start()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Application error: {e}", exc_info=True)
    finally:
        await app.stop()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application terminated by user")
        sys.exit(0)
