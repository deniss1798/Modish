"""Bounded retries of the SSH greeting, before credentials or commands are sent."""
import socket
import time

def ready_socket():
    last=None
    for attempt in range(3):
        connection=None
        try:
            connection=socket.create_connection(('194.87.118.236',22),timeout=8)
            connection.settimeout(8)
            if not connection.recv(256,socket.MSG_PEEK).startswith(b'SSH-'):
                raise ConnectionError('SSH greeting unavailable')
            return connection
        except (OSError,ConnectionError) as exc:
            last=exc
            if connection is not None: connection.close()
            if attempt<2: time.sleep(1)
    raise ConnectionError('SSH greeting unavailable after three connection attempts') from last
