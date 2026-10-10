import type { BenchModel } from "@/api/client";

import {
  FAMILY_COLORS,
  familyColors,
  folderOf,
  modelTree,
  NO_FILTERS,
  PARAMS_CHIPS,
  paramsBillions,
  paramsChip,
  passes,
  QUANT_CHIPS,
  quantBits,
  quantChip,
  underSpot,
} from "./modelCodes";

function model(key: string, publisher: string | null, quantization: string, size = 1): BenchModel {
  return {
    key,
    display_name: key,
    publisher,
    params: "4B",
    quantization,
    architecture: null,
    size_bytes: size,
    loaded: false,
    reasoning: false,
    fit: "ok",
  };
}

describe("model codes", () => {
  it("reads the parameters and the bits of LM Studio's labels", () => {
    expect(["4B", "4.6B", "26B-A4B", "300M", "1.8B", "", null].map(paramsBillions)).toEqual([
      4,
      4.6,
      26,
      0.3,
      1.8,
      null,
      null,
    ]);
    const names = ["Q4_K_M", "IQ2_XXS", "Q8_K_XL", "Q1_0", "TQ1_0", "MXFP4", "BF16", "F32", "?"];
    expect(names.map(quantBits)).toEqual([4, 2, 8, 1, 1, 4, 16, 32, null]);
  });

  it("colours each step of the parameters and of the quantization", () => {
    expect(paramsChip("1.8B")).toBe(PARAMS_CHIPS[0]);
    expect(paramsChip("4B")).toBe(PARAMS_CHIPS[1]);
    expect(paramsChip("9.4B")).toBe(PARAMS_CHIPS[2]);
    expect(paramsChip("12B")).toBe(PARAMS_CHIPS[3]);
    expect(paramsChip("35B-A3B")).toBe(PARAMS_CHIPS[4]);
    expect(paramsChip("70B")).toBe(PARAMS_CHIPS[5]);
    expect(["IQ2_S", "Q3_K_S", "Q4_0", "Q6_K", "Q8_0", "F16"].map(quantChip)).toEqual(QUANT_CHIPS);
    expect(paramsChip(null)).toBeNull();
    expect(quantChip("unknown")).toBeNull();
  });

  it("gives every family its own colour, the same each time", () => {
    const names = ["Qwen3-VL", "Qwen3.5", "Gemma-4", "Nemotron-3", "Bonsai", "GLM-4.6V"];
    const colors = familyColors(names);
    expect(new Set(colors.values()).size).toBe(names.length);
    expect(familyColors([...names].reverse())).toEqual(colors);
    const many = familyColors(Array.from({ length: 14 }, (_, index) => `family ${index}`));
    expect(new Set(many.values()).size).toBe(FAMILY_COLORS.length); // shared beyond twelve
    // The families of LM Studio first: those only the history names share what is left.
    const old = Array.from({ length: 10 }, (_, index) => `old ${index}`);
    const now = familyColors(names, old);
    expect(new Set(names.map((name) => now.get(name))).size).toBe(names.length);
  });

  it("arranges the models as LM Studio's folders, families and folders by name", () => {
    const models = [
      model("qwen3-vl-4b-instruct@q8_0", "Qwen3-VL", "Q8_0", 4),
      model("qwen3-vl-8b-instruct", "Qwen3-VL", "Q8_0", 8),
      model("qwen3-vl-4b-instruct@q4_k_m", "Qwen3-VL", "Q4_K_M", 2),
      model("gemma-4-e4b-it", "Gemma-4", "Q8_0"),
      model("qwen/qwen3-vl-4b", "qwen", "Q4_K_M"), // a model of LM Studio's catalogue
      model("loose", null, "Q4_K_M"),
    ];
    const tree = modelTree(models);
    expect(tree.map((family) => [family.name, family.count])).toEqual([
      ["Gemma-4", 1],
      ["qwen", 1],
      ["Qwen3-VL", 3],
      ["", 1], // no family: last
    ]);
    const qwen = tree[2];
    expect(qwen?.folders.map((folder) => folder.name)).toEqual([
      "qwen3-vl-4b-instruct",
      "qwen3-vl-8b-instruct",
    ]);
    expect(qwen?.folders[0]?.models.map((m) => m.quantization)).toEqual(["Q4_K_M", "Q8_0"]);
    expect(folderOf({ key: "qwen/qwen3-vl-4b" })).toBe("qwen3-vl-4b");

    expect(underSpot(tree, {})).toEqual(tree);
    expect(underSpot(tree, { family: "Qwen3-VL", folder: "qwen3-vl-8b-instruct" })).toEqual([
      { ...qwen, folders: [qwen?.folders[1]], count: 1 },
    ]);
    expect(underSpot(tree, { family: "Gone" })).toEqual([]);
  });

  it("keeps a model when it passes every kind of filter chosen", () => {
    const qwen = { publisher: "Qwen3-VL", params: "4B", quantization: "Q6_K" };
    expect(passes(qwen, NO_FILTERS)).toBe(true);
    expect(passes(qwen, { ...NO_FILTERS, families: ["Gemma-4", "Qwen3-VL"] })).toBe(true);
    expect(passes(qwen, { ...NO_FILTERS, params: [1], quants: [3] })).toBe(true);
    expect(passes(qwen, { ...NO_FILTERS, params: [1], quants: [2] })).toBe(false); // 4 bits only
    expect(passes({ ...qwen, params: null }, { ...NO_FILTERS, params: [1] })).toBe(false);
  });
});
