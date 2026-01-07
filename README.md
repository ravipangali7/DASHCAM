# JTT1078 Dashcam Live Streaming Application

A Python and HTML application for live streaming video from dashcam devices using the JTT1078 protocol.

## Features

- **JTT1078 Protocol Support**: Full implementation of JTT1078 protocol for communication with dashcam devices
- **Real-time Video Streaming**: Live video streaming from dashcam devices to web browser
- **Multiple Device Support**: Handle multiple concurrent device connections
- **Web Interface**: Modern, responsive web interface for viewing streams
- **TCP/UDP Support**: Supports both TCP and UDP connections from devices

## Architecture

```
Dashcam Device → JTT1078 Protocol (TCP/UDP) → Python Backend → WebSocket → HTML Frontend
```

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Ensure you have the protocol documentation PDF in the `docs/` folder.

## Configuration

Edit `config.py` to customize:
- Network ports (device server, HTTP server, WebSocket server)
- Video quality settings
- Logging configuration

## Usage

1. Start the application:
```bash
python main.py
```

2. The application will start:
   - Device server on port 1078 (TCP) and 1079 (UDP)
   - HTTP server on port 8080
   - WebSocket server on port 8081

3. Open your web browser and navigate to:
```
http://localhost:8080
```

4. Connect your dashcam device to the server using the configured ports.

## File Structure

```
DASHCAM/
├── main.py                 # Main application entry point
├── index.html             # Web frontend
├── jtt1078_parser.py     # Protocol parser
├── video_handler.py       # Video processing
├── stream_server.py      # WebSocket/HTTP server
├── device_manager.py      # Device connection manager
├── config.py              # Configuration
├── requirements.txt       # Python dependencies
└── docs/                  # Protocol documentation
```

## Protocol Message Types Supported

- **9101**: Real-time video upload request
- **1005**: Terminal response
- **9201/9202**: Real-time video upload (AVI)
- **9205**: Video upload control
- **9206/9207**: Real-time video upload (H.264)

## Web Interface Features

- Real-time video display
- Connection status indicator
- Device selection
- Channel selection
- Frame rate and quality information
- Automatic reconnection

## Troubleshooting

1. **Device not connecting**: Check firewall settings and ensure ports 1078/1079 are open
2. **No video display**: Verify device is sending video data and check browser console for errors
3. **Connection drops**: Check network stability and device connection

## Notes

- The application supports H.264 and AVI video formats
- Video frames are converted to JPEG for web streaming
- Multiple devices and channels can be streamed simultaneously
- The implementation follows the JTT1078 protocol specification

## License

This implementation is provided as-is for use with JTT1078 compatible dashcam devices.
