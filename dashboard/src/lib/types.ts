// Wire types mirroring the backend's snapshot payload. Keep in sync with
// backend/aeris/api/runtime.py::snapshot_payload and the domain enums.

export type MissionStatus =
  | "CREATED"
  | "ACTIVE"
  | "PAUSED"
  | "PERSON_LOCATED"
  | "COMPLETED"
  | "ABORTED";

export type DroneStatus =
  | "IDLE"
  | "SEARCHING"
  | "INVESTIGATING"
  | "HOLDING"
  | "RETURNING"
  | "LANDED"
  | "UNAVAILABLE";

export type LinkState = "CONNECTED" | "DEGRADED" | "STALE" | "LOST";

export type ZoneStatus =
  | "UNSEARCHED"
  | "ASSIGNED"
  | "SEARCHING"
  | "PARTIAL"
  | "COMPLETE"
  | "REQUIRES_RECHECK";

export interface GeoPoint {
  latitude: number;
  longitude: number;
  altitude_m: number;
}

export interface GeoPolygon {
  vertices: GeoPoint[];
}

export interface Mission {
  mission_id: string;
  name: string;
  status: MissionStatus;
  base_position: GeoPoint;
  search_area: { polygon: GeoPolygon; search_altitude_m: number };
  started_at: string | null;
}

export interface DroneCapability {
  camera: boolean;
  thermal: boolean;
  cruise_speed_mps: number;
}

export interface DroneState {
  drone_id: string;
  timestamp: string;
  position: GeoPoint;
  heading_deg: number;
  velocity_mps: number;
  battery_percent: number;
  estimated_remaining_s: number;
  connection_quality: number;
  link_state: LinkState;
  status: DroneStatus;
  assigned_zone_id: string | null;
  coverage_completed: number;
  camera_available: boolean;
  thermal_available: boolean;
}

export interface DroneView {
  drone: { drone_id: string; name: string; capability: DroneCapability };
  state: DroneState | null;
  telemetry_age_s: number | null;
  link_state: LinkState;
}

export interface Zone {
  zone_id: string;
  polygon: GeoPolygon;
  priority: number;
  coverage: number;
  assigned_drone_id: string | null;
  status: ZoneStatus;
}

export interface Detection {
  detection_id: string;
  position: GeoPoint;
  zone_id: string | null;
  triage: string | null;
  investigating_drone_id: string | null;
  observations: { source: string; confidence: number; drone_id: string; timestamp: string }[];
  first_observed_at: string;
  last_observed_at: string;
}

export interface Candidate {
  candidate_id: string;
  detection_id: string;
  escalated_at: string;
  position: GeoPoint;
  confirmed: boolean | null;
}

export interface Snapshot {
  taken_at: string;
  snapshot_hash: string;
  mission: Mission;
  coverage_fraction: number;
  emergency_stop_active: boolean;
  drones: DroneView[];
  zones: Zone[];
  detections: Detection[];
  candidates: Candidate[];
}

export interface MissionSummary {
  mission_id: string;
  name: string;
  status: MissionStatus;
  scenario: string;
  time_scale: number;
  running: boolean;
  elapsed_s: number;
  coverage_fraction: number;
  zone_count: number;
  drone_count: number;
  emergency_stop_active: boolean;
  decision_count: number;
  safety_event_count: number;
}

export interface DomainEvent {
  event_id: string;
  event_type: string;
  timestamp: string;
  mission_id: string;
  [key: string]: unknown;
}

export type StreamMessage =
  | { kind: "snapshot"; data: Snapshot }
  | { kind: "event"; data: DomainEvent };

export interface DecisionRecord {
  decision_id: string;
  timestamp: string;
  mission_id: string;
  drone_id: string | null;
  decision_type: string;
  provider: string;
  model: string | null;
  input_state: Record<string, unknown>;
  input_state_hash: string;
  selected_value: string;
  probabilities: Record<string, number>;
  score: number | null;
  latency_ms: number | null;
  input_tokens: number | null;
  estimated_cost_usd: number | null;
  policy_value: string | null;
  policy_reason: string | null;
  safety_override: boolean;
  safety_event_id: string | null;
  final_action: string;
  fallback_reason: string | null;
}

export interface SafetyEvent {
  event_id: string;
  timestamp: string;
  drone_id: string | null;
  rule: string;
  proposed_action: string;
  safe_alternative: string;
  reason: string;
}
