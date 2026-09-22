"use client";

import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import type { DomainEvent, Snapshot, StreamMessage } from "./types";

const MAX_EVENTS = 200;
const HIDDEN_EVENTS = new Set(["TelemetryReceived", "ZoneCoverageUpdated"]);

export function useMissionStream(missionId: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;

    api.events(missionId).then((history) => {
      if (!cancelled) setEvents(history.slice(-MAX_EVENTS).reverse());
    });

    const ws = new WebSocket(wsUrl(missionId));
    socket.current = ws;
    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onmessage = (raw) => {
      const message = JSON.parse(raw.data as string) as StreamMessage;
      if (message.kind === "snapshot") {
        setSnapshot(message.data);
      } else if (!HIDDEN_EVENTS.has(message.data.event_type)) {
        setEvents((prev) => [message.data, ...prev].slice(0, MAX_EVENTS));
      }
    };
    return () => {
      cancelled = true;
      ws.close();
      socket.current = null;
    };
  }, [missionId]);

  return { snapshot, events, connected };
}
