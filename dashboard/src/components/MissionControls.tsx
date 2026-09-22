"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { MissionStatus, MissionSummary } from "@/lib/types";

interface Props {
  missionId: string | null;
  status: MissionStatus | null;
  onMissionChange: (id: string) => void;
}

export function MissionControls({ missionId, status, onMissionChange }: Props) {
  const [scenarios, setScenarios] = useState<string[]>([]);
  const [scenario, setScenario] = useState("basic_search");
  const [timeScale, setTimeScale] = useState(5);
  const [missions, setMissions] = useState<MissionSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.scenarios().then(setScenarios).catch((e) => setError(String(e)));
    api.missions().then(setMissions).catch(() => undefined);
  }, [missionId]);

  const run = async (fn: () => Promise<unknown>) => {
    try {
      setError(null);
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <>
      <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
        {scenarios.map((s) => (
          <option key={s}>{s}</option>
        ))}
      </select>
      <input
        type="number"
        min={1}
        max={100}
        value={timeScale}
        onChange={(e) => setTimeScale(Number(e.target.value))}
        title="Simulated seconds per real second"
        style={{ width: 64 }}
      />
      <button onClick={() => run(async () => onMissionChange((await api.createMission(scenario, timeScale)).mission_id))}>
        New mission
      </button>
      {missions.length > 0 && (
        <select value={missionId ?? ""} onChange={(e) => onMissionChange(e.target.value)}>
          <option value="">— select —</option>
          {missions.map((m) => (
            <option key={m.mission_id} value={m.mission_id}>
              {m.name} · {m.status}
            </option>
          ))}
        </select>
      )}
      <span className="spacer" />
      {missionId && (
        <>
          <button disabled={status !== "CREATED"} onClick={() => run(() => api.command(missionId, "start"))}>Start</button>
          <button disabled={status !== "ACTIVE"} onClick={() => run(() => api.command(missionId, "pause"))}>Pause</button>
          <button disabled={status !== "PAUSED"} onClick={() => run(() => api.command(missionId, "resume"))}>Resume</button>
          <button disabled={!status || ["COMPLETED", "ABORTED", "PERSON_LOCATED"].includes(status)} onClick={() => run(() => api.command(missionId, "abort"))}>Abort</button>
        </>
      )}
      {error && <span className="small" style={{ color: "var(--danger)" }}>{error}</span>}
    </>
  );
}
