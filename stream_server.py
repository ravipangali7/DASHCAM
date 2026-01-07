"""
Stream Server
WebSocket and HTTP server for video streaming to web clients.
"""

import asyncio
import logging
import json
import base64
from typing import Dict, Set
from aiohttp import web
import websockets
from websockets.server import serve


logger = logging.getLogger(__name__)


def add_cors_headers(response: web.Response) -> web.Response:
    """Add CORS headers to response"""
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Max-Age'] = '3600'
    return response


class StreamServer:
    """WebSocket and HTTP server for video streaming"""
    
    def __init__(self, host: str = '0.0.0.0', http_port: int = 8080, 
                 ws_port: int = 8081):
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.frame_buffers: Dict[str, bytes] = {}  # device_id:channel -> JPEG frame
        self.app = None
        self.ws_server = None
        self.running = False
        self.broadcast_queue = asyncio.Queue()
        self.broadcast_task = None
    
    def update_frame(self, device_id: str, channel: int, jpeg_data: bytes):
        """Update frame buffer for a device/channel"""
        key = f"{device_id}:{channel}"
        self.frame_buffers[key] = jpeg_data
        
        # Queue message for broadcasting
        if self.clients:
            message = {
                'type': 'frame',
                'device_id': device_id,
                'channel': channel,
                'data': base64.b64encode(jpeg_data).decode('utf-8')
            }
            try:
                self.broadcast_queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Broadcast queue full, dropping frame")
    
    async def _broadcast_worker(self):
        """Worker task that broadcasts queued messages"""
        while self.running:
            try:
                message = await asyncio.wait_for(
                    self.broadcast_queue.get(),
                    timeout=1.0
                )
                message_json = json.dumps(message)
                
                # Send to all clients
                disconnected = set()
                for client in self.clients:
                    try:
                        await client.send(message_json)
                    except Exception as e:
                        logger.error(f"Error sending to client: {e}")
                        disconnected.add(client)
                
                # Remove disconnected clients
                self.clients -= disconnected
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in broadcast worker: {e}")
    
    
    async def websocket_handler(self, websocket: websockets.WebSocketServerProtocol, path: str):
        """Handle WebSocket connections"""
        client_addr = websocket.remote_address
        logger.info(f"WebSocket client connected: {client_addr}")
        self.clients.add(websocket)
        
        try:
            # Send initial connection message
            await websocket.send(json.dumps({
                'type': 'connected',
                'message': 'Connected to dashcam stream server'
            }))
            
            # Send available streams
            available_streams = list(self.frame_buffers.keys())
            await websocket.send(json.dumps({
                'type': 'streams',
                'streams': available_streams
            }))
            
            # Keep connection alive and handle messages
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')
                    
                    if msg_type == 'subscribe':
                        # Client wants to subscribe to a stream
                        device_id = data.get('device_id')
                        channel = data.get('channel', 0)
                        logger.info(f"Client {client_addr} subscribed to {device_id}:{channel}")
                    
                    elif msg_type == 'ping':
                        # Respond to ping
                        await websocket.send(json.dumps({'type': 'pong'}))
                
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON from client {client_addr}")
                except Exception as e:
                    logger.error(f"Error handling message from {client_addr}: {e}")
        
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"WebSocket client disconnected: {client_addr}")
        except Exception as e:
            logger.error(f"Error in WebSocket handler: {e}")
        finally:
            self.clients.discard(websocket)
    
    async def http_handler(self, request: web.Request):
        """Handle HTTP requests"""
        # Handle OPTIONS preflight requests
        if request.method == 'OPTIONS':
            response = web.Response()
            return add_cors_headers(response)
        
        path = request.path
        
        if path == '/' or path == '/index.html':
            # Serve index.html
            try:
                with open('index.html', 'r', encoding='utf-8') as f:
                    content = f.read()
                response = web.Response(text=content, content_type='text/html')
                return add_cors_headers(response)
            except FileNotFoundError:
                response = web.Response(text="index.html not found", status=404)
                return add_cors_headers(response)
        
        elif path == '/api/streams':
            # API endpoint to get available streams
            streams = [
                {
                    'key': key,
                    'device_id': key.split(':')[0],
                    'channel': int(key.split(':')[1]) if ':' in key else 0
                }
                for key in self.frame_buffers.keys()
            ]
            response = web.json_response({'streams': streams})
            return add_cors_headers(response)
        
        elif path.startswith('/api/frame/'):
            # API endpoint to get latest frame
            stream_key = path.replace('/api/frame/', '')
            if stream_key in self.frame_buffers:
                jpeg_data = self.frame_buffers[stream_key]
                response = web.Response(
                    body=jpeg_data,
                    content_type='image/jpeg'
                )
                return add_cors_headers(response)
            response = web.Response(text="Stream not found", status=404)
            return add_cors_headers(response)
        
        elif path == '/api/status':
            # API endpoint for server status
            response = web.json_response({
                'status': 'running',
                'clients': len(self.clients),
                'streams': len(self.frame_buffers)
            })
            return add_cors_headers(response)
        
        else:
            response = web.Response(text="Not found", status=404)
            return add_cors_headers(response)
    
    async def start_http_server(self):
        """Start HTTP server"""
        self.app = web.Application()
        # Add OPTIONS handler for all routes
        self.app.router.add_options('/{path:.*}', self.http_handler)
        self.app.router.add_get('/{path:.*}', self.http_handler)
        self.app.router.add_post('/{path:.*}', self.http_handler)
        
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.http_port)
        await site.start()
        logger.info(f"HTTP server started on {self.host}:{self.http_port}")
    
    async def start_websocket_server(self):
        """Start WebSocket server"""
        # Start broadcast worker
        self.broadcast_task = asyncio.create_task(self._broadcast_worker())
        
        async with serve(
            self.websocket_handler,
            self.host,
            self.ws_port
        ) as self.ws_server:
            logger.info(f"WebSocket server started on {self.host}:{self.ws_port}")
            # Keep running
            await asyncio.Future()  # Run forever
    
    async def start(self):
        """Start both HTTP and WebSocket servers"""
        self.running = True
        await asyncio.gather(
            self.start_http_server(),
            self.start_websocket_server()
        )
    
    async def stop(self):
        """Stop all servers"""
        self.running = False
        
        # Stop broadcast worker
        if self.broadcast_task:
            self.broadcast_task.cancel()
            try:
                await self.broadcast_task
            except asyncio.CancelledError:
                pass
        
        # Close all WebSocket connections
        for client in list(self.clients):
            try:
                await client.close()
            except Exception:
                pass
        self.clients.clear()
        
        # Stop HTTP server
        if self.app:
            await self.app.shutdown()
            await self.app.cleanup()
        
        # Stop WebSocket server
        # The server is managed by async context manager, so it will close automatically
        self.ws_server = None