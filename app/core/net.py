"""This device's network addresses (shared by the desktop IDE and the sign-in code page)."""

from __future__ import annotations

import socket
from typing import List


def local_addresses() -> List[str]:
    """IPv4 addresses other devices on the network can reach, best first."""
    out = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))         # TEST-NET: nothing is sent
            address = sock.getsockname()[0]
            if not address.startswith("127."):
                out.append(address)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address not in out and not address.startswith("127."):
                out.append(address)
    except OSError:
        pass
    return out
