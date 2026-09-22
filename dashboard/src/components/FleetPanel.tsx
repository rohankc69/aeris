"use client";

import type { DecisionRecord, DroneView, Snapshot } from "@/lib/types";

interface Props {
  snapshot: Snapshot | null;
  selectedDrone: string | null;
  onSelect: (id: string) => void;
  onReturn: (id: string) => void;
  onHold: (id: string) => void;
  onResume: (id: string) => void;
  onShow: (id: string) => void;
  decisions: DecisionRecord[];
}

function batteryClass(p: number): string {
  if (p <= 20) return "danger";
  if (p <= 35) return "warn";
  return "ok";
}

function flightTime(view: DroneView, startedAt: string | null): string {
  if (!view.state || !startedAt) return "–";
  const s = (new Date(view.state.timestamp).getTime() - new Date(startedAt).getTime()) / 1000;
  if (s < 0) return "0:00";
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export function FleetPanel({ snapshot, selectedDrone, onSelect, onReturn, onHold, onResume, onShow, decisions }: Props) {
  if (!snapshot) return <p className="small">No mission loaded.</p>;
  return (
    <>
      {snapshot.drones.map((view) => {
        const s = view.state;
        const selected = view.drone.drone_id === selectedDrone;
        const latest = decisions.find((d) => d.drone_id === view.drone.drone_id);
        return (
          <div
            key={view.drone.drone_id}
            className={`drone ${selected ? "selected" : ""}`}
            onClick={() => onSelect(view.drone.drone_id)}
          >
            <span className="name">{view.drone.name}</span>
            <span className={`battery ${s ? batteryClass(s.battery_percent) : "danger"}`}>
              {s ? `${Math.round(s.battery_percent)}%` : "--"}
            </span>
            <span className="status">{s ? s.status : "NO TELEMETRY"}</span>
            <span className={`link ${view.link_state}`}>{view.link_state}</span>
            {selected && s && (
              <dl className="details">
                <dt>Zone</dt>
                <dd>{s.assigned_zone_id ?? "–"}</dd>
                <dt>Coverage</dt>
                <dd>{Math.round(s.coverage_completed * 100)}%</dd>
                <dt>Altitude</dt>
                <dd>{s.position.altitude_m.toFixed(0)} m</dd>
                <dt>Speed</dt>
                <dd>{s.velocity_mps.toFixed(1)} m/s</dd>
                <dt>Remaining</dt>
                <dd>{Math.round(s.estimated_remaining_s / 60)} min</dd>
                <dt>Sensors</dt>
                <dd>
                  {s.camera_available ? "camera " : ""}
                  {s.thermal_available ? "thermal" : ""}
                </dd>
                <dt>Telemetry age</dt>
                <dd>{view.telemetry_age_s?.toFixed(1) ?? "–"} s</dd>
                <dt>Flight time</dt>
                <dd>{flightTime(view, snapshot.mission.started_at)}</dd>
                <dt>Latest decision</dt>
                <dd>
                  {latest
                    ? `${latest.final_action}${latest.safety_override ? " (safety)" : ""} · ${latest.provider}`
                    : "–"}
                </dd>
                <dt />
                <dd style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                  <button className="primary" onClick={(e) => { e.stopPropagation(); onShow(view.drone.drone_id); }}>Show me</button>
                  <button onClick={(e) => { e.stopPropagation(); onReturn(view.drone.drone_id); }}>Return</button>
                  <button onClick={(e) => { e.stopPropagation(); onHold(view.drone.drone_id); }}>Hold</button>
                  {s.status === "HOLDING" && (
                    <button onClick={(e) => { e.stopPropagation(); onResume(view.drone.drone_id); }}>Resume</button>
                  )}
                </dd>
              </dl>
            )}
          </div>
        );
      })}
    </>
  );
}
