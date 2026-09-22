"use client";

import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import type { DecisionRecord, DomainEvent, SafetyEvent, Snapshot, StreamMessage } from "./types";

const MAX_EVENTS = 200;
const TRAIL_POINTS = 600;
const HIDDEN_EVENTS = new Set(["TelemetryReceived", "ZoneCoverageUpdated"]);

export function useMissionStream(missionId: string | null) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [trails, setTrails] = useState<Record<string, [number, number][]>>({});
  const [connected, setConnected] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const trailMission = useRef<string | null>(null);

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
        setTrails((prev) => {
          // Start fresh trails when the stream switches to a different mission.
          const fresh = trailMission.current !== message.data.mission.mission_id;
          trailMission.current = message.data.mission.mission_id;
          const next = fresh ? {} : { ...prev };
          for (const d of message.data.drones) {
            if (!d.state) continue;
            const p: [number, number] = [d.state.position.longitude, d.state.position.latitude];
            const trail = next[d.drone.drone_id] ?? [];
            const last = trail[trail.length - 1];
            if (!last || last[0] !== p[0] || last[1] !== p[1]) {
              next[d.drone.drone_id] = [...trail, p].slice(-TRAIL_POINTS);
            }
          }
          return next;
        });
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

  return { snapshot, events, decisions, safetyEvents, trails, connected };
}
