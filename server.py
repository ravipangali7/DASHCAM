import socket
import ssl
import binascii

HOST = "0.0.0.0"
PORT = 2222

# Path to your TLS certificate and private key (self-signed is fine for testing)
CERT_FILE = "server.crt"
KEY_FILE = "server.key"

def handle_client(conn, addr):
    print(f"[+] Device connected from {addr}")

    try:
        # Wrap the raw socket with TLS
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=CERT_FILE, keyfile=KEY_FILE)

        tls_conn = context.wrap_socket(conn, server_side=True)

        print(f"[+] TLS handshake completed with {addr}")

        # Receive data securely
        while True:
            data = tls_conn.recv(4096)
            if not data:
                print(f"[-] Device disconnected")
                break

            # Print hex + readable info
            hex_data = binascii.hexlify(data).decode()
            print(f"[RX HEX] {hex_data}")
            print(f"[RX RAW] {data.decode(errors='ignore')}")  # decode safely

    except ssl.SSLError as e:
        print(f"[TLS ERROR] {e}")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        conn.close()


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(5)

    print(f"JT808 TLS server listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        handle_client(conn, addr)


if __name__ == "__main__":
    start_server()
