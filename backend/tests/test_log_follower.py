"""Stopping a log stream must not block: stop() runs on the server's event loop."""
import socket
import threading
import time

import urllib3

from app.api.ws import _LogFollower
from app.k8s import observe


class _InlineLoop:
    def call_soon_threadsafe(self, fn):
        fn()


class _Queue:
    def __init__(self):
        self.items = []
        self.received = threading.Event()

    def put_nowait(self, item):
        self.items.append(item)
        self.received.set()


def _quiet_log_server(release: threading.Event) -> int:
    """Sends one log line and then stays silent, like the log stream of an idle pod."""
    server = socket.create_server(("127.0.0.1", 0))

    def serve():
        conn, _ = server.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n6\r\nhello\n\r\n")
        release.wait(30)
        conn.close()
        server.close()

    threading.Thread(target=serve, daemon=True).start()
    return server.getsockname()[1]


def test_stop_does_not_block_on_a_quiet_stream(monkeypatch):
    release = threading.Event()
    port = _quiet_log_server(release)
    http = urllib3.PoolManager()
    monkeypatch.setattr(
        observe,
        "open_log_stream",
        lambda pod, namespace: http.request("GET", f"http://127.0.0.1:{port}/", preload_content=False),
    )
    queue = _Queue()
    follower = _LogFollower(_InlineLoop(), queue, "apps")
    follower.start("idle-pod")

    try:
        assert queue.received.wait(5), "the first log line never arrived"
        time.sleep(0.2)  # let the reader block waiting for the next line

        stopper = threading.Thread(target=follower.stop, daemon=True)
        stopper.start()
        stopper.join(3)
        assert not stopper.is_alive(), "stop() blocked, which would freeze the whole server"

        follower.join(3)
        assert not follower.is_alive(), "the reader thread kept running after stop()"
    finally:
        release.set()

    assert [m["line"] for m in queue.items] == ["hello"]  # stopping is not reported as an error


def test_stop_before_the_stream_opens_closes_it_when_it_arrives(monkeypatch):
    opening = threading.Event()
    proceed = threading.Event()

    class Stream:
        closed = False

        def __iter__(self):
            raise AssertionError("a stopped follower must not read")

        def close(self):
            self.closed = True

    stream = Stream()

    def slow_open(pod, namespace):
        opening.set()
        proceed.wait(5)
        return stream

    monkeypatch.setattr(observe, "open_log_stream", slow_open)
    follower = _LogFollower(_InlineLoop(), _Queue(), "apps")
    follower.start("pod")
    assert opening.wait(5)
    follower.stop()
    proceed.set()
    follower.join(3)
    assert not follower.is_alive()
    assert stream.closed
