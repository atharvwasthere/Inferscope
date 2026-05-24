import { useState } from "react";

function Copy({ text }) {
  const [done, setDone] = useState(false);
  return (
    <button
      className="copy-btn"
      onClick={() => {
        navigator.clipboard?.writeText(text);
        setDone(true);
        setTimeout(() => setDone(false), 1200);
      }}
    >
      {done ? "copied" : "copy"}
    </button>
  );
}

function Field({ label, children, danger }) {
  return (
    <div>
      <div className="detail-label">{label}</div>
      <div className="detail-value" style={danger ? { color: "var(--error)" } : undefined}>
        {children ?? "—"}
      </div>
    </div>
  );
}

export default function TraceDetail({ trace, onClose }) {
  if (!trace) return null;

  const isErr = trace.status === "error";
  const rawMeta = JSON.stringify(
    { raw_usage: trace.raw_usage ?? null, attributes: trace.attributes ?? null },
    null,
    2
  );

  return (
    <>
      <div className="overlay" onClick={onClose} />
      <aside className="trace-detail">
        <div className="trace-detail-header">
          <div className="section-title">Trace Details</div>
          <button className="icon-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="trace-detail-body">
          {/* 1. status + timestamp */}
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className={`badge badge-${isErr ? "error" : "success"}`}>{trace.status}</span>
            <span className="detail-value" style={{ color: "var(--text-muted)" }}>
              {trace.started_at ? new Date(trace.started_at).toLocaleString() : ""}
            </span>
          </div>

          {/* trace_id with copy */}
          <div>
            <div className="detail-label">trace_id</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="detail-value" style={{ color: "var(--accent)" }}>
                {trace.trace_id || "—"}
              </span>
              {trace.trace_id && <Copy text={trace.trace_id} />}
            </div>
          </div>

          {/* 2. grid */}
          <div className="detail-grid">
            <Field label="Conversation">{trace.conversation_id}</Field>
            <Field label="Provider">{trace.provider}</Field>
            <Field label="Model">{trace.model}</Field>
            <Field label="Status">{trace.status}</Field>
            <Field label="Latency" danger={isErr}>
              {trace.latency_ms != null ? `${Math.round(trace.latency_ms)}ms` : "—"}
            </Field>
            <Field label="TTFT">
              {trace.time_to_first_token_ms != null ? `${Math.round(trace.time_to_first_token_ms)}ms` : "—"}
            </Field>
            <Field label="Input Tokens">{trace.input_tokens?.toLocaleString()}</Field>
            <Field label="Output Tokens">{trace.output_tokens?.toLocaleString()}</Field>
            <Field label="Total Tokens">{trace.total_tokens?.toLocaleString()}</Field>
            <Field label="Cost">
              {trace.cost_usd != null ? `$${Number(trace.cost_usd).toFixed(6)}` : "—"}
            </Field>
          </div>

          {isErr && trace.error_message && (
            <div>
              <div className="section-title">Error</div>
              <div className="detail-value" style={{ color: "var(--error)" }}>
                {trace.error_message}
              </div>
            </div>
          )}

          {/* 3. inputs & outputs */}
          <div>
            <div className="section-title">Inputs &amp; Outputs</div>
            <div className="detail-grid">
              <Field label="input_preview">{trace.input_preview}</Field>
              <Field label="output_preview">{trace.output_preview}</Field>
            </div>
          </div>

          {/* 4. raw metadata */}
          <div>
            <div className="section-title">Raw Metadata</div>
            <div className="code-block">
              <Copy text={rawMeta} />
              {rawMeta}
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
