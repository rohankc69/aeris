"use client";

import type { Candidate, Detection } from "@/lib/types";

interface Props {
  detections: Detection[];
  candidates: Candidate[];
  onConfirm: (candidateId: string) => void;
  onReject: (candidateId: string) => void;
}

function summarize(d: Detection | undefined): string {
  if (!d) return "";
  const best: Record<string, number> = {};
  for (const o of d.observations) best[o.source] = Math.max(best[o.source] ?? 0, o.confidence);
  return Object.entries(best)
    .map(([k, v]) => `${k.toLowerCase()} ${Math.round(v * 100)}%`)
    .join(" · ");
}

export function CandidatesPanel({ detections, candidates, onConfirm, onReject }: Props) {
  const byId = new Map(detections.map((d) => [d.detection_id, d]));
  const open = candidates.filter((c) => c.confirmed === null);
  const resolved = candidates.filter((c) => c.confirmed !== null);
  const unescalated = detections.filter((d) => !candidates.some((c) => c.detection_id === d.detection_id));

  return (
    <>
      <h2>Candidates {open.length > 0 ? `· ${open.length} awaiting you` : ""}</h2>
      {open.length === 0 && unescalated.length === 0 && resolved.length === 0 && (
        <p className="small">No detections yet.</p>
      )}
      {open.map((c) => {
        const d = byId.get(c.detection_id);
        return (
          <div key={c.candidate_id} className="candidate open">
            <div className="name">Possible survivor · human confirmation required</div>
            <div className="small">
              {c.position.latitude.toFixed(5)}, {c.position.longitude.toFixed(5)} · {summarize(d)}
              {d?.investigating_drone_id ? ` · ${d.investigating_drone_id} investigating` : ""}
            </div>
            <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
              <button className="confirm" onClick={() => onConfirm(c.candidate_id)}>Confirm survivor</button>
              <button onClick={() => onReject(c.candidate_id)}>Reject</button>
            </div>
          </div>
        );
      })}
      {unescalated.map((d) => (
        <div key={d.detection_id} className="candidate">
          <div className="name">Candidate detection · {d.triage ?? "untriaged"}</div>
          <div className="small">
            {summarize(d)} · {d.observations.length} obs
            {d.investigating_drone_id ? ` · ${d.investigating_drone_id} investigating` : ""}
          </div>
        </div>
      ))}
      {resolved.map((c) => (
        <div key={c.candidate_id} className={`candidate ${c.confirmed ? "confirmed" : "rejected"}`}>
          <div className="name">{c.confirmed ? "Survivor confirmed" : "Rejected by operator"}</div>
          <div className="small">{summarize(byId.get(c.detection_id))}</div>
        </div>
      ))}
    </>
  );
}
