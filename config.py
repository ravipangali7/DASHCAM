"""
Configuration settings for the JTT1078 dashcam streaming application.
"""

# Network Configuration
DEVICE_TCP_HOST = '0.0.0.0'
DEVICE_TCP_PORT = 2222
DEVICE_UDP_HOST = '0.0.0.0'
DEVICE_UDP_PORT = 2221

# Web Server Configuration
HTTP_HOST = '0.0.0.0'
HTTP_PORT = 2223
WEBSOCKET_HOST = '0.0.0.0'
WEBSOCKET_PORT = 2224

# Video Settings
VIDEO_JPEG_QUALITY = 85
VIDEO_FRAME_RATE = 25
VIDEO_MAX_BUFFER_SIZE = 10  # Maximum frames to buffer per channel

# Protocol Settings
PROTOCOL_VERSION = 0x01
MESSAGE_TIMEOUT = 30  # seconds

# Logging Configuration
LOG_LEVEL = 'DEBUG'  # Changed to DEBUG to see all connection and message details
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Device Settings
MAX_DEVICE_CONNECTIONS = 100
DEVICE_CONNECTION_TIMEOUT = 300  # seconds
