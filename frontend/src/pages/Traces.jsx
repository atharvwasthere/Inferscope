import { useState } from "react";

import IntervalSelector from "../components/IntervalSelector.jsx";
import TraceTable from "../components/TraceTable.jsx";
import TraceDetail from "../components/TraceDetail.jsx";
import { useTraces, useTrace } from "../hooks/useDashboard.js";

const LIMIT = 20;

function FilterPills({ label, options, value, onChange }) {
  return (
    <div className="filter-group">
      <span className="label">{label}</span>
      <div className="pill-row">
        {options.map((o) => (
          <button
            key={o.label}
            className={value === o.value ? "pill active" : "pill"}
            onClick={() => onChange(o.value)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Traces() {
  const [interval, setInterval] = useState("1h");
  const [status, setStatus] = useState(null);
  const [provider, setProvider] = useState(null);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState(null);

  // strip null filters so URLSearchParams doesn't serialize "null"
  const params = { interval, limit: LIMIT, offset: page * LIMIT };
  if (status) params.status = status;
  if (provider) params.provider = provider;

  const { data: traces, isLoading } = useTraces(params);
  const { data: detail } = useTrace(selected);

  const reset = (setter) => (v) => {
    setter(v);
    setPage(0);
  };

  const count = traces?.length ?? 0;
  const from = count ? page * LIMIT + 1 : 0;
  const to = page * LIMIT + count;

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Traces</h1>
        <IntervalSelector value={interval} onChange={reset(setInterval)} />
      </div>

      <div className="filter-bar">
        <FilterPills
          label="Status"
          value={status}
          onChange={reset(setStatus)}
          options={[
            { label: "All", value: null },
            { label: "Success", value: "success" },
            { label: "Error", value: "error" },
          ]}
        />
        <FilterPills
          label="Provider"
          value={provider}
          onChange={reset(setProvider)}
          options={[
            { label: "All", value: null },
            { label: "Bedrock", value: "bedrock" },
            { label: "Gemini", value: "gemini" },
            { label: "Groq", value: "groq" },
          ]}
        />
      </div>

      <TraceTable
        traces={traces}
        loading={isLoading}
        onRowClick={(t) => setSelected(t.trace_id)}
      />

      <div className="pagination">
        <span className="info">
          Showing {from}–{to}
        </span>
        <span className="spacer" />
        <button className="btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
          ← Prev
        </button>
        <button className="btn" disabled={count < LIMIT} onClick={() => setPage((p) => p + 1)}>
          Next →
        </button>
      </div>

      {selected && detail?.length > 0 && (
        <TraceDetail trace={detail[0]} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
