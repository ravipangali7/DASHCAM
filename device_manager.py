"""
Device Connection Manager
Manages TCP/UDP connections from dashcam devices and routes messages.
"""

import asyncio
import logging
from typing import Dict, Optional, Callable
from jtt1078_parser import JTT1078Parser, MessageType


logger = logging.getLogger(__name__)


class DeviceConnection:
    """Represents a connected device"""
    
    def __init__(self, device_id: str, reader: asyncio.StreamReader, 
                 writer: asyncio.StreamWriter, address: tuple):
        self.device_id = device_id
        self.reader = reader
        self.writer = writer
        self.address = address
        self.connected = True
        self.parser = JTT1078Parser()
        self.buffer = bytearray()
    
    async def send_message(self, message_id: int, body: bytes, 
                          message_serial: int = 0) -> bool:
        """Send a message to the device"""
        if not self.connected:
            return False
        
        try:
            terminal_phone = self.device_id
            message = self.parser.build_message(
                message_id, terminal_phone, body, message_serial
            )
            self.writer.write(message)
            await self.writer.drain()
            return True
        except Exception as e:
            logger.error(f"Error sending message to {self.device_id}: {e}")
            self.connected = False
            return False
    
    async def close(self):
        """Close the connection"""
        self.connected = False
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass


class DeviceManager:
    """Manages device connections and message routing"""
    
    def __init__(self, host: str = '0.0.0.0', tcp_port: int = 1078, 
                 udp_port: int = 1079):
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.devices: Dict[str, DeviceConnection] = {}
        self.message_handlers: Dict[int, Callable] = {}
        self.parser = JTT1078Parser()
        self.running = False
    
    def register_handler(self, message_type: int, handler: Callable):
        """Register a message handler for a specific message type"""
        self.message_handlers[message_type] = handler
    
    async def handle_tcp_connection(self, reader: asyncio.StreamReader,
                                   writer: asyncio.StreamWriter):
        """Handle a new TCP connection from a device"""
        address = writer.get_extra_info('peername')
        logger.info(f"New TCP connection from {address}")
        
        device_id = None
        connection = None
        
        try:
            buffer = bytearray()
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                
                buffer.extend(data)
                
                # Try to parse messages from buffer
                while len(buffer) > 0:
                    parsed = self.parser.parse_message(bytes(buffer))
                    if parsed:
                        # Extract message from buffer
                        start_idx = buffer.find(0x7E)
                        if start_idx == -1:
                            buffer.clear()
                            break
                        
                        end_idx = -1
                        for i in range(start_idx + 1, len(buffer)):
                            if buffer[i] == 0x7E:
                                end_idx = i
                                break
                        
                        if end_idx == -1:
                            break  # Wait for more data
                        
                        # Remove processed message from buffer
                        buffer = buffer[end_idx + 1:]
                        
                        # Process message
                        message_id = parsed['header']['message_id']
                        terminal_phone = parsed['header']['terminal_phone']
                        
                        # Register device if not already registered
                        if device_id is None:
                            device_id = terminal_phone
                            connection = DeviceConnection(
                                device_id, reader, writer, address
                            )
                            self.devices[device_id] = connection
                            logger.info(f"Device registered: {device_id}")
                        
                        # Call registered handler
                        if message_id in self.message_handlers:
                            try:
                                await self.message_handlers[message_id](
                                    connection, parsed
                                )
                            except Exception as e:
                                logger.error(f"Error handling message {message_id}: {e}")
                    else:
                        # No complete message found, check if we have start flag
                        if 0x7E not in buffer:
                            buffer.clear()
                        break
        
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in TCP connection handler: {e}")
        finally:
            if device_id and device_id in self.devices:
                del self.devices[device_id]
                logger.info(f"Device disconnected: {device_id}")
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    
    async def handle_udp_packet(self, data: bytes, addr: tuple):
        """Handle a UDP packet from a device"""
        try:
            parsed = self.parser.parse_message(data)
            if parsed:
                message_id = parsed['header']['message_id']
                terminal_phone = parsed['header']['terminal_phone']
                
                # For UDP, create a temporary connection object
                # In real implementation, you might want to maintain UDP connections differently
                if message_id in self.message_handlers:
                    # Create a mock connection for UDP
                    connection = DeviceConnection(
                        terminal_phone, None, None, addr
                    )
                    await self.message_handlers[message_id](connection, parsed)
        except Exception as e:
            logger.error(f"Error handling UDP packet: {e}")
    
    async def start_tcp_server(self):
        """Start TCP server"""
        server = await asyncio.start_server(
            self.handle_tcp_connection,
            self.host,
            self.tcp_port
        )
        logger.info(f"TCP server started on {self.host}:{self.tcp_port}")
        self.running = True
        
        async with server:
            await server.serve_forever()
    
    async def start_udp_server(self):
        """Start UDP server"""
        sock = await asyncio.get_event_loop().create_datagram_endpoint(
            lambda: UDPProtocol(self),
            local_addr=(self.host, self.udp_port)
        )
        logger.info(f"UDP server started on {self.host}:{self.udp_port}")
        
        # Keep running
        while self.running:
            await asyncio.sleep(1)
    
    async def start(self):
        """Start both TCP and UDP servers"""
        # Start servers in parallel
        tcp_task = asyncio.create_task(self.start_tcp_server())
        udp_task = asyncio.create_task(self.start_udp_server())
        
        # Wait for both (they run forever)
        await asyncio.gather(tcp_task, udp_task, return_exceptions=True)
    
    def get_device(self, device_id: str) -> Optional[DeviceConnection]:
        """Get a device connection by ID"""
        return self.devices.get(device_id)
    
    def get_all_devices(self) -> Dict[str, DeviceConnection]:
        """Get all connected devices"""
        return self.devices.copy()
    
    async def stop(self):
        """Stop all servers and close connections"""
        self.running = False
        for device in list(self.devices.values()):
            await device.close()
        self.devices.clear()


class UDPProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler"""
    
    def __init__(self, device_manager: DeviceManager):
        self.device_manager = device_manager
    
    def datagram_received(self, data: bytes, addr: tuple):
        """Handle received UDP datagram"""
        asyncio.create_task(self.device_manager.handle_udp_packet(data, addr))
