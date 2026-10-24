import { useCallback, useEffect, useRef, useState } from "react";
import { socketUrl } from "./api";
import type { SocketMessage } from "./types";

/** One reconnecting WebSocket per app page. `onMessage` may change between renders. */
export function useAppSocket(appName: string, onMessage: (msg: SocketMessage) => void) {
  const handler = useRef(onMessage);
  const socket = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    handler.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    let delay = 1000;

    const connect = () => {
      const ws = new WebSocket(socketUrl(`/ws/apps/${appName}`));
      socket.current = ws;
      ws.onopen = () => {
        setConnected(true);
        delay = 1000;
      };
      ws.onmessage = (e) => handler.current(JSON.parse(e.data) as SocketMessage);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          retry = window.setTimeout(connect, delay);
          delay = Math.min(delay * 2, 15000);
        }
      };
    };
    connect();

    return () => {
      closed = true;
      window.clearTimeout(retry);
      socket.current?.close();
    };
  }, [appName]);

  const send = useCallback((msg: object) => {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify(msg));
  }, []);

  return { connected, send };
}
