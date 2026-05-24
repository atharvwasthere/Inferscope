import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from "@/components/ui/chart";

const config = {
  p50_ms: { label: "p50", color: "var(--chart-1)" },
  p95_ms: { label: "p95", color: "var(--chart-2)" },
};

const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

export default function LatencyChart({ data = [] }) {
  return (
    <ChartContainer config={config} className="aspect-auto h-[240px] w-full">
      <LineChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <CartesianGrid vertical={false} stroke="transparent" />
        <XAxis dataKey="bucket" tickFormatter={hhmm} tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis tickFormatter={(v) => `${v}ms`} tickLine={false} axisLine={false} width={48} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={hhmm} />} />
        <Line dataKey="p50_ms" type="monotone" stroke="var(--color-p50_ms)" strokeWidth={2} dot={false} />
        <Line dataKey="p95_ms" type="monotone" stroke="var(--color-p95_ms)" strokeWidth={2} dot={false} />
        <ChartLegend content={<ChartLegendContent />} verticalAlign="bottom" />
      </LineChart>
    </ChartContainer>
  );
}
