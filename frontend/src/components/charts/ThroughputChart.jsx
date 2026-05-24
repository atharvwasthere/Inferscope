import { Area, AreaChart, CartesianGrid, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart";

const config = {
  count: { label: "Requests", color: "var(--chart-2)" },
};

const hhmm = (iso) =>
  iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";

export default function ThroughputChart({ data = [] }) {
  return (
    <ChartContainer config={config} className="aspect-auto h-[240px] w-full">
      <AreaChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
        <defs>
          <linearGradient id="fillThroughput" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-count)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--color-count)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="transparent" />
        <XAxis dataKey="bucket" tickFormatter={hhmm} tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis tickLine={false} axisLine={false} width={40} allowDecimals={false} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={hhmm} />} />
        <Area
          dataKey="count"
          type="monotone"
          stroke="var(--color-count)"
          strokeWidth={2}
          fill="url(#fillThroughput)"
        />
      </AreaChart>
    </ChartContainer>
  );
}
