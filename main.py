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
    
    async def handle_registration(self, connection, parsed_message: Dict):
        """Handle terminal registration (0x0100)"""
        try:
            device_id = connection.device_id
            logger.info(f"Terminal registration from {device_id}")
            
            # Parse registration body
            body = parsed_message['body']
            if len(body) >= 2:
                province_id = body[0]
                city_id = body[1]
                manufacturer_id = body[2:7] if len(body) >= 7 else b''
                terminal_model = body[7:27] if len(body) >= 27 else b''
                terminal_id = body[27:43] if len(body) >= 43 else b''
                license_plate_color = body[43] if len(body) >= 44 else 0
                license_plate = body[44:] if len(body) > 44 else b''
                
                logger.info(f"Registration details - Province: {province_id}, City: {city_id}, "
                          f"Manufacturer: {manufacturer_id.hex() if manufacturer_id else 'N/A'}, "
                          f"Model: {terminal_model.decode('utf-8', errors='ignore') if terminal_model else 'N/A'}")
            
            # Send registration response (0x8100)
            # Response: Result (1 byte: 0=success, 1=vehicle already registered, 2=vehicle not in database, 
            #                   3=terminal already registered, 4=terminal not in database)
            # Auth code (16 bytes, only if result=0)
            result = 0  # Success
            auth_code = b'1234567890123456'  # 16 bytes auth code
            
            response_body = bytes([result]) + auth_code
            await connection.send_message(
                MessageType.TERMINAL_REGISTRATION_RESPONSE,
                response_body,
                message_serial=parsed_message['header']['message_serial']
            )
            logger.info(f"Sent registration response to {device_id} (result: {result})")
            
        except Exception as e:
            logger.error(f"Error handling registration: {e}", exc_info=True)
    
    async def handle_authentication(self, connection, parsed_message: Dict):
        """Handle terminal authentication (0x0102)"""
        try:
            device_id = connection.device_id
            logger.info(f"Terminal authentication from {device_id}")
            
            # Parse auth code from body
            body = parsed_message['body']
            auth_code = body[:16] if len(body) >= 16 else b''
            logger.info(f"Auth code received: {auth_code.hex()}")
            
            # Send authentication response (0x8001 - same as heartbeat response)
            response_body = bytes([0x00])  # Success
            await connection.send_message(
                MessageType.TERMINAL_HEARTBEAT_RESPONSE,
                response_body,
                message_serial=parsed_message['header']['message_serial']
            )
            logger.info(f"Sent authentication response to {device_id}")
            
        except Exception as e:
            logger.error(f"Error handling authentication: {e}", exc_info=True)
    
    async def handle_heartbeat(self, connection, parsed_message: Dict):
        """Handle terminal heartbeat (0x0002)"""
        try:
            device_id = connection.device_id
            logger.debug(f"Heartbeat from {device_id}")
            
            # Send heartbeat response (0x8001)
            response_body = bytes([0x00])  # Success
            await connection.send_message(
                MessageType.TERMINAL_HEARTBEAT_RESPONSE,
                response_body,
                message_serial=parsed_message['header']['message_serial']
            )
            
        except Exception as e:
            logger.error(f"Error handling heartbeat: {e}")
    
    def setup_handlers(self):
        """Setup message handlers"""
        # JTT808 Base Protocol Handlers
        self.device_manager.register_handler(
            MessageType.TERMINAL_REGISTRATION,
            self.handle_registration
        )
        self.device_manager.register_handler(
            MessageType.TERMINAL_AUTHENTICATION,
            self.handle_authentication
        )
        self.device_manager.register_handler(
            MessageType.TERMINAL_HEARTBEAT,
            self.handle_heartbeat
        )
        
        # JTT1078 Video Protocol Handlers
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
