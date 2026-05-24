function hhmmss(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function latClass(ms) {
  if (ms == null) return "";
  if (ms > 2000) return "lat-bad";
  if (ms > 1000) return "lat-warn";
  return "lat-ok";
}

function short(id) {
  return id ? id.slice(0, 8) : "—";
}

export default function TraceTable({ traces, loading, onRowClick }) {
  if (loading) {
    return (
      <div className="table-wrap" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton skeleton-line" style={{ height: 28 }} />
        ))}
      </div>
    );
  }

  if (!traces?.length) {
    return (
      <div className="empty">
        <span className="mark">◎</span>
        <span className="empty-text">No traces in this window</span>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Time</th>
            <th>Conversation</th>
            <th>Model</th>
            <th>Provider</th>
            <th>Latency</th>
            <th>Tokens</th>
            <th>Cost</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {traces.map((t) => (
            <tr key={t.id} className="clickable" onClick={() => onRowClick(t)}>
              <td className="mono">{hhmmss(t.created_at || t.started_at)}</td>
              <td className="mono">{short(t.conversation_id)}</td>
              <td className="mono">{t.model}</td>
              <td>
                <span className="badge">{t.provider}</span>
              </td>
              <td className={`mono ${latClass(t.latency_ms)}`}>
                {t.latency_ms != null ? `${Math.round(t.latency_ms)}ms` : "—"}
              </td>
              <td className="mono">{(t.total_tokens ?? 0).toLocaleString()}</td>
              <td className="mono">{t.cost_usd != null ? `$${Number(t.cost_usd).toFixed(6)}` : "—"}</td>
              <td>
                <span className={`badge badge-${t.status === "success" ? "success" : "error"}`}>
                  {t.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
