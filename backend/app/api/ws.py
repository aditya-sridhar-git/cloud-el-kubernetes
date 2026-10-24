"""Live updates for one app over a single WebSocket.

Server -> client messages: {"type": "deployment" | "build_log" | "metrics" | "event" | "pod_log", ...}
Client -> server messages: {"type": "follow_logs", "pod": "<pod name>"} / {"type": "stop_logs"}
"""
import asyncio
import logging
import threading

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import token_ok
from app.k8s import observe

log = logging.getLogger(__name__)
router = APIRouter()


class _LogFollower:
    """Streams one pod's logs on a worker thread into the connection's queue."""

    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, namespace: str) -> None:
        self.loop, self.queue, self.namespace = loop, queue, namespace
        self._resp = None
        self._stopped = threading.Event()

    def _put(self, msg: dict) -> None:
        def put():
            try:
                self.queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        self.loop.call_soon_threadsafe(put)

    def start(self, pod: str) -> None:
        threading.Thread(target=self._run, args=(pod,), daemon=True).start()

    def _run(self, pod: str) -> None:
        try:
            self._resp = observe.open_log_stream(pod, self.namespace)
            for raw in self._resp:
                if self._stopped.is_set():
                    break
                self._put({"type": "pod_log", "pod": pod, "line": raw.decode(errors="replace").rstrip("\n")})
        except Exception as e:
            if not self._stopped.is_set():
                self._put({"type": "pod_log", "pod": pod, "line": f"[log stream ended: {e}]"})

    def stop(self) -> None:
        self._stopped.set()
        if self._resp is not None:
            try:
                self._resp.close()
            except Exception:
                pass


@router.websocket("/ws/apps/{name}")
async def app_socket(ws: WebSocket, name: str, token: str = ""):
    state = ws.app.state
    if not token_ok(token, state.settings.api_token):
        await ws.close(code=4401)
        return
    await ws.accept()

    queue = state.hub.subscribe(name)
    loop = asyncio.get_running_loop()
    follower: _LogFollower | None = None

    async def send_loop():
        while True:
            await ws.send_json(await queue.get())

    async def receive_loop():
        nonlocal follower
        while True:
            msg = await ws.receive_json()
            if msg.get("type") in ("follow_logs", "stop_logs") and follower:
                follower.stop()
                follower = None
            if msg.get("type") == "follow_logs" and msg.get("pod"):
                follower = _LogFollower(loop, queue, state.settings.apps_namespace)
                follower.start(str(msg["pod"]))

    tasks = [asyncio.create_task(send_loop()), asyncio.create_task(receive_loop())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        if follower:
            follower.stop()
        state.hub.unsubscribe(name, queue)
