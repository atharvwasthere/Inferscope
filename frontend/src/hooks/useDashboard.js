import { useQuery } from "@tanstack/react-query";

const BASE = "/api/dashboard";
const f = (path) => fetch(`${BASE}${path}`).then((r) => r.json());

export const useSummary = (i) =>
  useQuery({ queryKey: ["dashboard", "summary", i], queryFn: () => f(`/dashboard/summary?interval=${i}`), refetchInterval: 30_000 });

export const useLatency = (i) =>
  useQuery({ queryKey: ["dashboard", "latency", i], queryFn: () => f(`/dashboard/latency?interval=${i}`), refetchInterval: 30_000 });

export const useThroughput = (i) =>
  useQuery({ queryKey: ["dashboard", "throughput", i], queryFn: () => f(`/dashboard/throughput?interval=${i}`), refetchInterval: 30_000 });

export const useErrors = (i) =>
  useQuery({ queryKey: ["dashboard", "errors", i], queryFn: () => f(`/dashboard/errors?interval=${i}`), refetchInterval: 30_000 });

export const useLatencySeries = (i) =>
  useQuery({ queryKey: ["dashboard", "latency-series", i], queryFn: () => f(`/dashboard/latency/series?interval=${i}`), refetchInterval: 30_000 });

export const useErrorSeries = (i) =>
  useQuery({ queryKey: ["dashboard", "error-series", i], queryFn: () => f(`/dashboard/errors/series?interval=${i}`), refetchInterval: 30_000 });

export const useCost = (i) =>
  useQuery({ queryKey: ["dashboard", "cost", i], queryFn: () => f(`/dashboard/cost?interval=${i}`), refetchInterval: 30_000 });

export const useTraces = (p) =>
  useQuery({ queryKey: ["traces", p], queryFn: () => f(`/dashboard/traces?${new URLSearchParams(p)}`), refetchInterval: 30_000 });

export const useTrace = (id) =>
  useQuery({ queryKey: ["trace", id], queryFn: () => f(`/dashboard/traces/${id}`), enabled: !!id });

export const useConvStats = (i) =>
  useQuery({ queryKey: ["dashboard", "convstats", i], queryFn: () => f(`/dashboard/conversations/stats?interval=${i}`), refetchInterval: 30_000 });
