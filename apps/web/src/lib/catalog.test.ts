/**
 * Which chat models a selector offers. A retiring model must not be picked
 * anew, but a bot still on one has to show it rather than a blank select.
 */
import { describe, expect, it } from "vitest";
import {
  type ChatModel,
  modelLabel,
  selectableChatModels,
} from "@/lib/catalog";

function model(id: string, retires_on: string | null = null): ChatModel {
  return {
    id,
    provider: "openai",
    display_name: id,
    context_window: 128000,
    max_output_tokens: null,
    supports_tools: true,
    supports_vision: false,
    supports_temperature: true,
    retires_on,
    price_in_per_mtok: "0.10",
    price_out_per_mtok: "0.50",
  };
}

const models = [model("gpt-6-luna"), model("gpt-4o-mini", "2027-01-14")];

describe("selectableChatModels", () => {
  it("leaves retiring models out of a new choice", () => {
    expect(selectableChatModels(models, "gpt-6-luna").map((m) => m.id)).toEqual(
      ["gpt-6-luna"],
    );
    expect(selectableChatModels(models).map((m) => m.id)).toEqual([
      "gpt-6-luna",
    ]);
  });

  it("keeps a retiring model a bot is still on", () => {
    expect(
      selectableChatModels(models, "gpt-4o-mini").map((m) => m.id),
    ).toEqual(["gpt-6-luna", "gpt-4o-mini"]);
  });
});

describe("modelLabel", () => {
  it("names the shutdown date", () => {
    expect(modelLabel(models[1])).toBe("gpt-4o-mini (retires 2027-01-14)");
    expect(modelLabel(models[0])).toBe("gpt-6-luna");
  });
});
