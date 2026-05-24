import { useState } from "react";

import IntervalSelector from "../components/IntervalSelector.jsx";
import MetricCard from "../components/MetricCard.jsx";
import StatusBanner from "../components/StatusBanner.jsx";
import LatencyChart from "../components/charts/LatencyChart.jsx";
import ThroughputChart from "../components/charts/ThroughputChart.jsx";
import ErrorRateChart from "../components/charts/ErrorRateChart.jsx";
import {
  useSummary,
  useLatency,
  useThroughput,
  useCost,
  useConvStats,
  useLatencySeries,
  useErrorSeries,
} from "../hooks/useDashboard.js";

const fmt = (n) => (n ?? 0).toLocaleString();
const ms = (n) => `${Math.round(n ?? 0)}ms`;
const usd = (n) => `$${Number(n ?? 0).toFixed(4)}`;
const short = (id) => (id ? id.slice(0, 8) : "—");

function bannerStatus(rate) {
  if (rate == null) return "healthy";
  if (rate >= 10) return "critical";
  if (rate >= 2) return "elevated_errors";
  return "healthy";
}

// Wrap a section: skeleton while loading, inline error, else content.
function Section({ query, height = 280, children }) {
  if (query.isLoading) return <div className="skeleton" style={{ height, borderRadius: 12 }} />;
  if (query.isError)
    return (
      <div className="card" style={{ padding: 20, color: "var(--error)" }}>
        Failed to load this section.
      </div>
    );
  return children;
}

export default function Dashboard() {
  const [interval, setInterval] = useState("1h");

  const summary = useSummary(interval);
  const latency = useLatency(interval);
  const throughput = useThroughput(interval);
  const cost = useCost(interval);
  const convStats = useConvStats(interval);
  const latSeries = useLatencySeries(interval);
  const errSeries = useErrorSeries(interval);

  const s = summary.data || {};

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
        <IntervalSelector value={interval} onChange={setInterval} />
      </div>

      {/* status banner */}
      <Section query={summary} height={72}>
        <StatusBanner
          status={bannerStatus(s.error_rate_pct)}
          total_requests={s.total_requests}
          error_rate_pct={s.error_rate_pct}
          avg_latency_ms={s.avg_latency_ms}
        />
      </Section>

      {/* metric cards */}
      <div className="metric-grid" style={{ marginTop: 24 }}>
        <MetricCard label="Requests" value={fmt(s.total_requests)} />
        <MetricCard label="Error Rate" value={`${(s.error_rate_pct ?? 0).toFixed(2)}%`} />
        <MetricCard label="Avg Latency" value={ms(s.avg_latency_ms)} />
        <MetricCard label="p95 Latency" value={ms(latency.data?.p95_ms)} />
        <MetricCard label="Tokens" value={fmt(s.total_tokens)} />
        <MetricCard label="Cost" value={usd(s.total_cost_usd)} />
      </div>

      {/* latency + throughput */}
      <div className="chart-row" style={{ marginTop: 24 }}>
        <div className="chart-card">
          <div className="chart-title">Latency (p50 / p95)</div>
          <Section query={latSeries} height={240}>
            <LatencyChart data={latSeries.data} />
          </Section>
        </div>
        <div className="chart-card">
          <div className="chart-title">Throughput</div>
          <Section query={throughput} height={240}>
            <ThroughputChart data={throughput.data} />
          </Section>
        </div>
      </div>

      {/* error rate full width */}
      <div className="chart-card" style={{ marginTop: 24 }}>
        <div className="chart-title">Error Rate</div>
        <Section query={errSeries} height={220}>
          <ErrorRateChart data={errSeries.data} />
        </Section>
      </div>

      {/* cost breakdown */}
      <div style={{ marginTop: 24 }}>
        <div className="chart-title">Cost by provider / model</div>
        <Section query={cost} height={200}>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Model</th>
                  <th>Requests</th>
                  <th>Tokens</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {(cost.data || []).map((r, i) => (
                  <tr key={i}>
                    <td>
                      <span className="badge">{r.provider}</span>
                    </td>
                    <td className="mono">{r.model}</td>
                    <td className="mono">{fmt(r.requests)}</td>
                    <td className="mono">{fmt(r.total_tokens)}</td>
                    <td className="mono">{usd(r.cost_usd)}</td>
                  </tr>
                ))}
                {!cost.data?.length && (
                  <tr>
                    <td colSpan={5} style={{ color: "var(--text-muted)" }}>
                      No cost data in this window.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Section>
      </div>

      {/* conversation stats */}
      <div style={{ marginTop: 24 }}>
        <div className="chart-title">Conversations</div>
        <Section query={convStats} height={200}>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Conversation</th>
                  <th>Requests</th>
                  <th>Avg Latency</th>
                  <th>Total Cost</th>
                  <th>Errors</th>
                </tr>
              </thead>
              <tbody>
                {(convStats.data || []).map((r) => (
                  <tr key={r.conversation_id}>
                    <td className="mono">{short(r.conversation_id)}</td>
                    <td className="mono">{fmt(r.requests)}</td>
                    <td className="mono">{ms(r.avg_latency_ms)}</td>
                    <td className="mono">{usd(r.total_cost_usd)}</td>
                    <td className="mono">{fmt(r.errors)}</td>
                  </tr>
                ))}
                {!convStats.data?.length && (
                  <tr>
                    <td colSpan={5} style={{ color: "var(--text-muted)" }}>
                      No conversation activity in this window.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </Section>
      </div>
    </div>
  );
}
