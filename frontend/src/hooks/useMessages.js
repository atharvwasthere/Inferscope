import { useQuery } from "@tanstack/react-query";

const BASE = import.meta.env.VITE_CHATBOT_URL;

export const useMessages = (id) =>
  useQuery({
    queryKey: ["messages", id],
    queryFn: () => fetch(`${BASE}/conversations/${id}/messages`).then((r) => r.json()),
    enabled: !!id && id !== "new",
  });
