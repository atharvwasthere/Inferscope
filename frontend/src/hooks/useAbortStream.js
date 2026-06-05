import { useMutation } from "@tanstack/react-query";

const BASE = "/api/chatbot";

// Real cancellation: stops the in-flight LLM generation server-side.
// The chatbot publishes an abort on a Redis pub/sub channel; the replica
// that owns the stream task flips its local event, breaks the SSE loop,
// closes the provider socket (so the provider stops billing), and persists
// the partial assistant reply.
export const useAbortStream = () => {
  return useMutation({
    mutationFn: (streamId) =>
      fetch(`${BASE}/streams/${streamId}/abort`, { method: "POST" }).then((r) => r.json()),
  });
};
