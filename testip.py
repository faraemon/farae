import socket

def get_ip_address():
    # Connect to an external host (Google DNS here) just to get the right IP on the outgoing interface
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))  # This does NOT send packets, just picks the right interface
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'  # fallback in case of failure
    finally:
        s.close()
    return ip

ip_address = get_ip_address()
print(f'Debug: Server IP address is {ip_address}')
