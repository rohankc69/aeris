"use client";

import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import type { DecisionRecord, DomainEvent, SafetyEvent, Snapshot, StreamMessage } from "./types";

const MAX_EVENTS = 200;
const HIDDEN_EVENTS = new Set(["TelemetryReceived", "ZoneCoverageUpdated"]);

export function useMissionStream(missionId: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!missionId) return;
    let cancelled = false;

    api.events(missionId).then((history) => {
      if (!cancelled) setEvents(history.slice(-MAX_EVENTS).reverse());
    });
    const refreshDecisions = () => {
      api.decisions(missionId).then((rows) => {
        if (!cancelled) setDecisions(rows.slice(-MAX_EVENTS).reverse());
      });
      api.safetyEvents(missionId).then((rows) => {
        if (!cancelled) setSafetyEvents(rows.reverse());
      });
    };
    refreshDecisions();

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
        if (
          message.data.event_type === "DecisionCompleted" ||
          message.data.event_type === "SafetyOverrideTriggered"
        ) {
          refreshDecisions();
        }
      }
    };
    return () => {
      cancelled = true;
      ws.close();
      socket.current = null;
    };
  }, [missionId]);

  return { snapshot, events, decisions, safetyEvents, connected };
}
