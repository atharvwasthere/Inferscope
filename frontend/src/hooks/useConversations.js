import { useQuery } from "@tanstack/react-query";

const BASE = import.meta.env.VITE_CHATBOT_URL;

export const useConversations = () =>
  useQuery({
    queryKey: ["conversations"],
    queryFn: () => fetch(`${BASE}/conversations`).then((r) => r.json()),
    refetchInterval: 10_000,
  });
