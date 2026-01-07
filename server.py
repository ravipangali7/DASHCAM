import socket
import binascii

HOST = "0.0.0.0"
PORT = 2222

def handle_client(conn, addr):
    print(f"[+] Device connected from {addr}")

    while True:
        try:
            data = conn.recv(4096)
            if not data:
                print("[-] Device disconnected")
                break

            # Print raw hex data
            hex_data = binascii.hexlify(data).decode()
            print(f"[RX] {hex_data}")

        except Exception as e:
            print(f"[ERROR] {e}")
            break

    conn.close()

def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind((HOST, PORT))
    server.listen(5)

    print(f"JT808 server listening on {HOST}:{PORT}")

    while True:
        conn, addr = server.accept()
        handle_client(conn, addr)

if __name__ == "__main__":
    start_server()
