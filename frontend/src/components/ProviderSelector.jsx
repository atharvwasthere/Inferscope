const LABELS = { bedrock: "Bedrock", gemini: "Gemini", groq: "Groq" };

export default function ProviderSelector({ models, provider, model, onChange }) {
  // models is the grouped dict from GET /models: { provider: [model, ...] }
  if (!models || Object.keys(models).length === 0) return null;

  const pickProvider = (p) => onChange(p, models[p][0]); // reset model to first
  const pickModel = (m) => onChange(provider, m);

  return (
    <div className="prov-sel">
      <div className="prov-tabs">
        {Object.keys(models).map((p) => (
          <button
            key={p}
            className={provider === p ? "prov-tab active" : "prov-tab"}
            onClick={() => pickProvider(p)}
            type="button"
          >
            {LABELS[p] ?? p}
          </button>
        ))}
      </div>
      <select
        className="prov-model"
        value={model}
        onChange={(e) => pickModel(e.target.value)}
      >
        {(models[provider] ?? []).map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}
