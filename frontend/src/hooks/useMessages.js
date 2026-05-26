import { useQuery } from "@tanstack/react-query";

const BASE = "/api/chatbot";

export const useMessages = (id) =>
  useQuery({
    queryKey: ["messages", id],
    queryFn: () => fetch(`${BASE}/conversations/${id}/messages`).then((r) => r.json()),
    enabled: !!id && id !== "new",
  });
