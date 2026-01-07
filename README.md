# JTT 808/1078 Live Streaming Server

A Python implementation of JTT 808 GPS tracking and JTT 1078 video streaming protocol server with web interface.

## Features

- JTT 808 protocol support (registration, heartbeat, authentication)
- JTT 1078 video streaming support
- Real-time video streaming to web interface
- GPS data display
- Multi-device support
- Web-based dashboard

## Installation

1. Ensure Python 3.7+ is installed
2. No external dependencies required for basic functionality

## Usage

### Start the Server

```bash
python web_server.py
```

This will start:
- JTT 808/1078 server on port 2222 (for device connections)
- Web server on port 8080 (for web interface)

### Access the Web Interface

Open your browser and navigate to:
```
http://localhost:8080
```

### Connect a Device

Configure your JTT 808/1078 compatible device to connect to:
- Host: Your server IP address
- Port: 2222

## Protocol Support

### JTT 808 Messages
- 0x0100: Device Registration
- 0x8100: Registration Response
- 0x0002: Heartbeat
- 0x8002: Heartbeat Response
- 0x0102: Terminal Authentication
- 0x8001: Authentication Response

### JTT 1078 Messages
- 0x1205: Video Upload (Video Data)

## File Structure

- `server.py` - JTT 808/1078 protocol server
- `jt808_protocol.py` - Protocol parser and message builder
- `video_streamer.py` - Video stream management
- `web_server.py` - Web server and HTTP API
- `index.html` - Web dashboard interface

## Notes

- Video data is expected in H.264 format
- The web interface attempts to display video frames
- For production use, consider adding video transcoding (H.264 to WebM/MP4)
- GPS coordinates are displayed when available in video messages

## Troubleshooting

1. **No streams showing**: Check that devices are connected to port 2222
2. **Video not displaying**: Browser may not support raw H.264. Consider transcoding.
3. **Connection issues**: Ensure firewall allows ports 2222 and 8080
