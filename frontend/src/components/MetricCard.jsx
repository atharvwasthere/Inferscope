export default function MetricCard({ label, value, sub, mono = true }) {
  return (
    <div className="metric-card">
      <span className="metric-label">{label}</span>
      <span className={`metric-value ${mono ? "mono" : "ui"}`}>{value}</span>
      {sub != null && <span className="metric-sub">{sub}</span>}
    </div>
  );
}
