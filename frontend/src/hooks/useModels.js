import { useQuery } from "@tanstack/react-query";

const BASE = import.meta.env.VITE_CHATBOT_URL;

// Catalog rarely changes — fetch once per session, never auto-refetch.
// Call queryClient.invalidateQueries(["models"]) explicitly if a model-management UI is added.
export const useModels = () =>
  useQuery({
    queryKey: ["models"],
    queryFn: () => fetch(`${BASE}/models`).then((r) => r.json()),
    staleTime: Infinity,
  });
