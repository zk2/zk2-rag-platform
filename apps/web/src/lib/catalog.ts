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
  /** ISO date the provider shuts the model down, or null. */
  retires_on: string | null;
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
  /** False when this deployment has no way to run the model. */
  usable: boolean;
  unusable_reason: string | null;
};

export type Catalog = {
  chat: ChatModel[];
  embedding: EmbeddingModel[];
  /** Width every vector is stored at, whatever the model's native size. */
  embedding_dimensions: number;
};

export function useCatalog() {
  return useQuery({
    queryKey: ["catalog"],
    queryFn: () => api.get<Catalog>("/settings/models"),
    staleTime: 60 * 60 * 1000,
  });
}

/**
 * Models a selector should offer. A retiring model is left out unless it is
 * the one already chosen, since a bot still on it has to show what it runs.
 * The API still accepts one until the shutdown date, which is the way back.
 */
export function selectableChatModels(
  models: ChatModel[],
  current?: string,
): ChatModel[] {
  return models.filter((m) => !m.retires_on || m.id === current);
}

export function modelLabel(model: ChatModel): string {
  return model.retires_on
    ? `${model.display_name} (retires ${model.retires_on})`
    : model.display_name;
}

export function formatPrice(model: ChatModel): string {
  const input = Number(model.price_in_per_mtok);
  const output = Number(model.price_out_per_mtok);
  if (input === 0 && output === 0) return "local, no token cost";
  return `$${input}/$${output} per Mtok`;
}
