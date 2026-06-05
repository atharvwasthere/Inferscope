import { useMutation, useQueryClient } from "@tanstack/react-query";

const BASE = "/api/chatbot";

// Soft-lock a conversation: future sends to this id are rejected with 409.
// Does NOT abort an in-flight stream — see useAbortStream for that.
export const useCloseConversation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id) =>
      fetch(`${BASE}/conversations/${id}/close`, { method: "PATCH" }).then((r) => r.json()),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
};
