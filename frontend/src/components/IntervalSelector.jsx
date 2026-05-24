const INTERVALS = ["1h", "6h", "24h", "7d"];

export default function IntervalSelector({ value, onChange }) {
  return (
    <div className="pill-row">
      {INTERVALS.map((i) => (
        <button
          key={i}
          className={value === i ? "pill active mono" : "pill mono"}
          onClick={() => onChange(i)}
        >
          {i}
        </button>
      ))}
    </div>
  );
}
