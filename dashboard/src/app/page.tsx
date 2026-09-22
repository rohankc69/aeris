"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { EventLog } from "@/components/EventLog";
import { FleetPanel } from "@/components/FleetPanel";
import { MissionControls } from "@/components/MissionControls";
import { api } from "@/lib/api";
import { useMissionStream } from "@/lib/useMissionStream";

// MapLibre touches window at import time, so it must not render on the server.
const MissionMap = dynamic(() => import("@/components/MissionMap").then((m) => m.MissionMap), { ssr: false });

export default function CommandCenter() {
  const [missionId, setMissionId] = useState<string | null>(null);
  const [selectedDrone, setSelectedDrone] = useState<string | null>(null);
  const { snapshot, events, connected } = useMissionStream(missionId);
  const status = snapshot?.mission.status ?? null;

  return (
    <main className="console">
      <header className="topbar">
        <h1>AERIS</h1>
        {snapshot && (
          <>
            <span className="small">MISSION: {snapshot.mission.name}</span>
            <span className={`status-pill ${status}`}>{status}</span>
            <span className="small">{connected ? "● live" : "○ offline"}</span>
          </>
        )}
        <MissionControls missionId={missionId} status={status} onMissionChange={setMissionId} />
      </header>

      <MissionMap snapshot={snapshot} selectedDrone={selectedDrone} onSelectDrone={setSelectedDrone} />

      <aside className="panel">
        <h2>Fleet</h2>
        <FleetPanel
          snapshot={snapshot}
          selectedDrone={selectedDrone}
          onSelect={setSelectedDrone}
          onReturn={(id) => missionId && api.droneCommand(missionId, id, "return")}
          onHold={(id) => missionId && api.droneCommand(missionId, id, "hold")}
        />
      </aside>

      <section className="bottom">
        <div>
          <h2>Search coverage: {snapshot ? `${Math.round(snapshot.coverage_fraction * 100)}%` : "–"}</h2>
          <div className="coverage">
            <div style={{ width: `${(snapshot?.coverage_fraction ?? 0) * 100}%` }} />
          </div>
          {snapshot && (
            <div className="zone-grid">
              {snapshot.zones.map((z) => (
                <div key={z.zone_id} title={z.status}>
                  {z.zone_id} {Math.round(z.coverage * 100)}%
                  <div className="small">{z.assigned_drone_id?.replace("drone-", "D") ?? z.status.toLowerCase()}</div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div>
          <h2>Events</h2>
          <EventLog events={events} />
        </div>
      </section>
    </main>
  );
}
