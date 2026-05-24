import { useState } from "react";

function fmtCost(c) {
  if (c == null) return "—";
  return `$${Number(c).toFixed(6)}`;
}

export default function MessageBubble({ role, content, metadata, streaming }) {
  const [open, setOpen] = useState(false);

  return (
    <div className={`msg-row ${role}`}>
      <div className={`bubble ${role}`}>
        {content}
        {streaming && <span className="blink">|</span>}
      </div>

      {role === "assistant" && metadata && (
        <>
          <div className="msg-meta" onClick={() => setOpen((o) => !o)}>
            ↓ {Math.round(metadata.latency_ms ?? 0)}ms {"  •  "}
            {(metadata.total_tokens ?? 0).toLocaleString()} tokens {"  •  "}
            {fmtCost(metadata.cost_usd)} {"  •  "}
            {metadata.provider}/{metadata.model}
          </div>
          {open && (
            <div className="code-block" style={{ maxWidth: "76%", marginTop: 6 }}>
              {JSON.stringify(metadata, null, 2)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
