import type { DecisionRecord, DomainEvent, MissionSummary, SafetyEvent, Snapshot } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_AERIS_API_URL ?? "http://localhost:8000";

export function wsUrl(missionId: string): string {
  const base = API_URL.replace(/^http/, "ws");
  return `${base}/api/v1/missions/${missionId}/ws`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export const api = {
  scenarios: () => request<string[]>("/api/v1/scenarios"),
  missions: () => request<MissionSummary[]>("/api/v1/missions"),
  mission: (id: string) =>
    request<Snapshot & { summary: MissionSummary }>(`/api/v1/missions/${id}`),
  events: (id: string) => request<DomainEvent[]>(`/api/v1/missions/${id}/events`),
  decisions: (id: string) => request<DecisionRecord[]>(`/api/v1/missions/${id}/decisions`),
  safetyEvents: (id: string) => request<SafetyEvent[]>(`/api/v1/missions/${id}/safety-events`),
  createMission: (scenario: string, timeScale: number) =>
    request<MissionSummary>("/api/v1/missions", {
      method: "POST",
      body: JSON.stringify({ scenario, time_scale: timeScale, autostart: false }),
    }),
  command: (id: string, action: "start" | "pause" | "resume" | "abort" | "estop" | "estop/clear") =>
    request<unknown>(`/api/v1/missions/${id}/${action}`, { method: "POST" }),
  candidateCommand: (id: string, candidateId: string, action: "confirm" | "reject") =>
    request<unknown>(`/api/v1/missions/${id}/candidates/${candidateId}/${action}`, {
      method: "POST",
    }),
  droneCommand: (id: string, droneId: string, action: "return" | "hold") =>
    request<unknown>(`/api/v1/missions/${id}/drones/${droneId}/${action}`, {
      method: "POST",
    }),
};
