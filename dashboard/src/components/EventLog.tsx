import type { DomainEvent } from "@/lib/types";

function describe(e: DomainEvent): string {
  const d = e as Record<string, unknown>;
  switch (e.event_type) {
    case "ZoneAssigned":
      return `${d.drone_id} → zone ${d.zone_id}${Number(d.start_fraction) > 0 ? ` (resume ${Math.round(Number(d.start_fraction) * 100)}%)` : ""}`;
    case "ZoneStatusChanged":
      return `zone ${d.zone_id} ${d.previous} → ${d.current}`;
    case "ZoneReassignmentRequested":
      return `zone ${d.zone_id} released by ${d.previous_drone_id}: ${d.reason}`;
    case "DroneReturning":
      return `${d.drone_id} RTB: ${d.reason}`;
    case "DroneLinkStateChanged":
      return `${d.drone_id} link ${d.previous} → ${d.current}`;
    case "DroneDisconnected":
      return `${d.drone_id} disconnected (${Number(d.telemetry_age_s).toFixed(0)}s)`;
    case "OperatorActionReceived":
      return `operator: ${d.action_type}${d.target_id ? ` ${d.target_id}` : ""}`;
    case "MissionCompleted":
      return `mission ${d.final_status}`;
    default:
      return e.event_type;
  }
}

export function EventLog({ events }: { events: DomainEvent[] }) {
  return (
    <ul className="events">
      {events.map((e) => (
        <li key={e.event_id}>
          <span className="time">{new Date(e.timestamp).toISOString().slice(11, 19)}</span>
          <span>{describe(e)}</span>
        </li>
      ))}
    </ul>
  );
}
