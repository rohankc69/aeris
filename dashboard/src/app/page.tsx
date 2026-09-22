"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { CandidatesPanel } from "@/components/CandidatesPanel";
import { DecisionInspector } from "@/components/DecisionInspector";
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
  const [focusRequest, setFocusRequest] = useState(0);
  const showDrone = (id: string) => {
    setSelectedDrone(id);
    setFocusRequest((n) => n + 1);
  };
  const [bottomTab, setBottomTab] = useState<"coverage" | "decisions">("coverage");
  const { snapshot, events, decisions, safetyEvents, connected } = useMissionStream(missionId);
  const status = snapshot?.mission.status ?? null;
  const overrides = decisions.filter((d) => d.safety_override).length;
  const estop = snapshot?.emergency_stop_active ?? false;

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
        {estop && <span className="status-pill ABORTED">E-STOP</span>}
        <MissionControls missionId={missionId} status={status} estop={estop} onMissionChange={setMissionId} />
      </header>

      <MissionMap
        snapshot={snapshot}
        selectedDrone={selectedDrone}
        onSelectDrone={setSelectedDrone}
        focusRequest={focusRequest}
      />

      <aside className="panel">
        {snapshot && (
          <CandidatesPanel
            detections={snapshot.detections}
            candidates={snapshot.candidates}
            onConfirm={(id) => missionId && api.candidateCommand(missionId, id, "confirm")}
            onReject={(id) => missionId && api.candidateCommand(missionId, id, "reject")}
          />
        )}
        <h2 style={{ marginTop: 12 }}>Fleet</h2>
        <FleetPanel
          snapshot={snapshot}
          selectedDrone={selectedDrone}
          onSelect={setSelectedDrone}
          onReturn={(id) => missionId && api.droneCommand(missionId, id, "return")}
          onHold={(id) => missionId && api.droneCommand(missionId, id, "hold")}
          onResume={(id) => missionId && api.droneCommand(missionId, id, "resume")}
          onShow={showDrone}
          decisions={decisions}
        />
      </aside>

      <section className="bottom">
        <div>
          <div className="tabs">
            <button className={bottomTab === "coverage" ? "active" : ""} onClick={() => setBottomTab("coverage")}>
              Coverage {snapshot ? `${Math.round(snapshot.coverage_fraction * 100)}%` : ""}
            </button>
            <button className={bottomTab === "decisions" ? "active" : ""} onClick={() => setBottomTab("decisions")}>
              Decisions {decisions.length}
              {overrides > 0 ? ` · ${overrides} overridden` : ""}
            </button>
            {selectedDrone && bottomTab === "decisions" && (
              <span className="small">filtered to {selectedDrone}</span>
            )}
          </div>
          {bottomTab === "coverage" ? (
            <>
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
            </>
          ) : (
            <DecisionInspector decisions={decisions} safetyEvents={safetyEvents} filterDrone={selectedDrone} />
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
