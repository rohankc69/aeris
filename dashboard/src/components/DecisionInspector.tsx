"use client";

import { useState } from "react";
import type { DecisionRecord, SafetyEvent } from "@/lib/types";

interface Props {
  decisions: DecisionRecord[];
  safetyEvents: SafetyEvent[];
  filterDrone: string | null;
}

function pct(v: number): string {
  return `${Math.round(v * 100)}%`;
}

function fmtInput(value: unknown): string {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  return String(value);
}

export function DecisionInspector({ decisions, safetyEvents, filterDrone }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);
  const rows = filterDrone ? decisions.filter((d) => d.drone_id === filterDrone) : decisions;
  const eventsById = new Map(safetyEvents.map((e) => [e.event_id, e]));

  if (rows.length === 0) {
    return <p className="small">No AI-assisted decisions yet{filterDrone ? ` for ${filterDrone}` : ""}.</p>;
  }

  return (
    <ul className="decisions">
      {rows.map((d) => {
        const open = d.decision_id === openId;
        const override = eventsById.get(d.safety_event_id ?? "");
        const ranked = Object.entries(d.probabilities).sort((a, b) => b[1] - a[1]);
        return (
          <li key={d.decision_id} className={d.safety_override ? "override" : ""}>
            <div className="row" onClick={() => setOpenId(open ? null : d.decision_id)}>
              <span className="time">{new Date(d.timestamp).toISOString().slice(11, 19)}</span>
              <span className="mono">{d.drone_id ?? "mission"}</span>
              <span className="mono">{d.decision_type}</span>
              <span className="mono">{d.provider}{d.fallback_reason ? ` (fallback: ${d.fallback_reason})` : ""}</span>
              <span className="mono">
                {d.selected_value}
                {d.safety_override ? ` → ${d.final_action}` : ""}
              </span>
              <span className={`tag ${d.safety_override ? "danger" : "ok"}`}>
                {d.safety_override ? "SAFETY OVERRIDE" : "allowed"}
              </span>
            </div>
            {open && (
              <div className="detail">
                <div>
                  <h3>Input</h3>
                  <dl>
                    {Object.entries(d.input_state).map(([k, v]) => (
                      <div key={k}>
                        <dt>{k}</dt>
                        <dd>{fmtInput(v)}</dd>
                      </div>
                    ))}
                    <div>
                      <dt>state hash</dt>
                      <dd>{d.input_state_hash}</dd>
                    </div>
                  </dl>
                </div>
                <div>
                  <h3>
                    {d.provider} · {d.model ?? "–"}
                    {d.latency_ms !== null ? ` · ${d.latency_ms.toFixed(0)} ms` : ""}
                    {d.input_tokens !== null ? ` · ${d.input_tokens} tok` : ""}
                    {d.estimated_cost_usd !== null ? ` · $${d.estimated_cost_usd.toFixed(5)}` : ""}
                  </h3>
                  {ranked.length > 0 ? (
                    <dl>
                      {ranked.map(([choice, p]) => (
                        <div key={choice}>
                          <dt>{choice}</dt>
                          <dd>
                            <span className="bar" style={{ width: `${Math.max(2, p * 100)}%` }} /> {pct(p)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <dl>
                      <div>
                        <dt>value</dt>
                        <dd>{d.selected_value}</dd>
                      </div>
                    </dl>
                  )}
                </div>
                <div>
                  <h3>Policy</h3>
                  <dl>
                    <div>
                      <dt>rules say</dt>
                      <dd>{d.policy_value ?? "–"}</dd>
                    </div>
                    <div>
                      <dt>because</dt>
                      <dd>{d.policy_reason ?? "–"}</dd>
                    </div>
                  </dl>
                  <h3>Safety override</h3>
                  <dl>
                    <div>
                      <dt>override</dt>
                      <dd>{d.safety_override ? "YES" : "NO"}</dd>
                    </div>
                    {override && (
                      <>
                        <div>
                          <dt>rule</dt>
                          <dd>{override.rule}</dd>
                        </div>
                        <div>
                          <dt>reason</dt>
                          <dd>{override.reason}</dd>
                        </div>
                      </>
                    )}
                    <div>
                      <dt>final action</dt>
                      <dd>
                        <strong>{d.final_action}</strong>
                      </dd>
                    </div>
                  </dl>
                </div>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
