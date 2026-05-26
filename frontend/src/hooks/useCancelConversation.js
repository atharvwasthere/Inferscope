import { useMutation, useQueryClient } from "@tanstack/react-query";

const BASE = "/api/chatbot";

export const useCancelConversation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) =>
      fetch(`${BASE}/conversations/${id}/cancel`, { method: "PATCH" }).then((r) => r.json()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
};
