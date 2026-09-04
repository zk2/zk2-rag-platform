/**
 * Model catalog served by the API. The UI never hardcodes model ids: what the
 * backend can bill and validate is exactly what the selector offers.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type ChatModel = {
  id: string;
  provider: string;
  display_name: string;
  context_window: number;
  max_output_tokens: number | null;
  supports_tools: boolean;
  supports_vision: boolean;
  supports_temperature: boolean;
  price_in_per_mtok: string;
  price_out_per_mtok: string;
};

export type EmbeddingModel = {
  id: string;
  provider: string;
  display_name: string;
  native_dimensions: number;
  supports_dimensions: boolean;
  price_in_per_mtok: string;
};

export type Catalog = { chat: ChatModel[]; embedding: EmbeddingModel[] };

export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: () => api.get<Catalog>("/settings/models"),
    staleTime: 60 * 60 * 1000,
  });
}

export function formatPrice(model: ChatModel): string {
  const input = Number(model.price_in_per_mtok);
  const output = Number(model.price_out_per_mtok);
  if (input === 0 && output === 0) return "local, no token cost";
  return `$${input}/$${output} per Mtok`;
}
