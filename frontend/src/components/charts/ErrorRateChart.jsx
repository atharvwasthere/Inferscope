import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

const config = {
  error_rate_pct: { label: "Error rate", color: "var(--chart-5)" },
};

const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

export default function ErrorRateChart({ data = [] }) {
  return (
    <ChartContainer config={config} className="aspect-auto h-[220px] w-full">
      <LineChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} stroke="transparent" />
        <XAxis dataKey="bucket" tickFormatter={hhmm} tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis tickFormatter={(v) => `${v}%`} tickLine={false} axisLine={false} width={44} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={hhmm} />} />
        <Line
          dataKey="error_rate_pct"
          type="monotone"
          stroke="var(--color-error_rate_pct)"
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ChartContainer>
  );
}
