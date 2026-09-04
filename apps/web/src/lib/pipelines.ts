/** Types and queries for the pipeline editor. */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type JsonSchema = {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  properties?: Record<string, JsonSchema>;
  anyOf?: JsonSchema[];
};

export type NodeType = {
  type: string;
  title: string;
  description: string;
  config_schema: JsonSchema;
};

export type DagNode = {
  id: string;
  type: string;
  config: Record<string, unknown>;
  position?: { x: number; y: number } | null;
};

export type Dag = { nodes: DagNode[]; edges: [string, string][] };

export type Pipeline = {
  id: number;
  name: string;
  description: string | null;
  current_version_id: number | null;
  created_at: string;
  updated_at: string;
};

export type PipelineDetail = Pipeline & {
  dag: Dag;
  version_count: number;
  used_by_bots: number[];
};

export type PipelineVersion = {
  id: number;
  pipeline_id: number;
  label: string | null;
  created_at: string;
  is_current: boolean;
};

export type NodeTiming = { node_id: string; type: string; duration_ms: number };

export type TestRun = {
  status: string;
  question: string;
  answer: string | null;
  error: string | null;
  duration_ms: number;
  nodes: NodeTiming[];
  sources: Array<{ name: string; ordinal: number; score?: number; matched_by?: string[] }>;
  citations: number[];
  tokens_in: number;
  tokens_out: number;
  cost_usd: string | null;
  trace_url: string | null;
};

export function useNodeTypes() {
  return useQuery({
    queryKey: ["pipeline-node-types"],
    queryFn: () => api.get<NodeType[]>("/pipelines/node-types"),
    staleTime: 60 * 60 * 1000,
  });
}

/** Config defaults straight from the node's JSON Schema. */
export function defaultConfig(schema: JsonSchema): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, property] of Object.entries(schema.properties ?? {})) {
    if (property.default !== undefined && property.default !== null) out[key] = property.default;
  }
  return out;
}

/** The editable field list for a node config, derived from its schema. */
export function schemaFields(schema: JsonSchema): Array<[string, JsonSchema]> {
  return Object.entries(schema.properties ?? {});
}

/** A schema may be `{anyOf: [{type: "integer"}, {type: "null"}]}` for optionals. */
export function fieldType(schema: JsonSchema): string {
  if (schema.type) return schema.type;
  const option = schema.anyOf?.find((s) => s.type && s.type !== "null");
  return option?.type ?? "string";
}
