import { useQuery } from "@tanstack/react-query";

const BASE = "/api/chatbot";

export const useConversations = () =>
  useQuery({
    queryKey: ["conversations"],
    queryFn: () => fetch(`${BASE}/conversations`).then((r) => r.json()),
    refetchInterval: 10_000,
  });
