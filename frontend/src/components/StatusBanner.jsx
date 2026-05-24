const COPY = {
  healthy: "System healthy",
  elevated_errors: "Elevated errors",
  critical: "Critical — high error rate",
};

export default function StatusBanner({ status, total_requests, error_rate_pct, avg_latency_ms }) {
  const cls = COPY[status] ? status : "healthy";
  return (
    <div className={`status-banner ${cls}`}>
      <span className="status-dot" />
      <div>
        <div className="status-title">{COPY[cls]}</div>
        <div className="status-summary">
          {(total_requests ?? 0).toLocaleString()} requests
          {"  •  "}
          {(error_rate_pct ?? 0).toFixed(2)}% errors
          {"  •  "}
          {Math.round(avg_latency_ms ?? 0).toLocaleString()}ms avg latency
        </div>
      </div>
    </div>
  );
}
