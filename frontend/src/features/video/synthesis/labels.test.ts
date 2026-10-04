import type { Clip, SynthesisSuggestion } from "@/api/client";
import i18n from "@/i18n";

import {
  clipNotes,
  criterionLabel,
  hasSynthesis,
  reasonLabel,
  rolesByShot,
  soundRange,
  usabilityLevel,
} from "./labels";
import { EMPTY, READY } from "./fixtures";

const fr = i18n.getFixedT("fr");
const en = i18n.getFixedT("en");

const CUTS: Clip = {
  picture_in_s: 103.75,
  picture_out_s: 105.75,
  sound_in_s: 103.67,
  sound_out_s: 106.46,
  notes: [
    "le son commence 0,08 s avant l'image (J-cut)",
    "le son continue 0,7 s après la coupe (L-cut)",
    "parole continue : coupe dans un mot inévitable",
  ],
};

function suggestion(role: SynthesisSuggestion["role"], shots: number[]): SynthesisSuggestion {
  return {
    role,
    shots,
    clip: { picture_in_s: 0, picture_out_s: 1, sound_in_s: null, sound_out_s: null, notes: [] },
    usability: 90,
    reasons: [],
  };
}

describe("synthesis labels", () => {
  it("translates the API's French labels, and shows an unknown one as it is", () => {
    expect(reasonLabel(fr, "horizon penché")).toBe("horizon penché");
    expect(reasonLabel(en, "horizon penché")).toBe("tilted horizon");
    expect(reasonLabel(en, "très court")).toBe("very short");
    expect(reasonLabel(en, "vignettage")).toBe("vignettage");
    expect(criterionLabel(fr, "utilisable 96")).toBe("utilisable 96");
    expect(criterionLabel(en, "utilisable 96")).toBe("usable 96");
    expect(criterionLabel(en, "plan fort")).toBe("strong shot");
    expect(criterionLabel(en, "nouveau critère")).toBe("nouveau critère");
  });

  it("writes the J-cut and L-cut notes from the clip's own times", () => {
    expect(clipNotes(fr, CUTS, "fr")).toEqual([
      "le son commence 0,08 s avant l'image (J-cut)",
      "le son continue 0,7 s après la coupe (L-cut)",
      "parole continue : coupe dans un mot inévitable",
    ]);
    expect(clipNotes(en, CUTS, "en")).toEqual([
      "the sound starts 0.08 s before the picture (J-cut)",
      "the sound runs 0.7 s past the cut (L-cut)",
      "continuous speech: a cut inside a word cannot be avoided",
    ]);
    const moved: Clip = { ...CUTS, sound_in_s: null, sound_out_s: null, notes: ["autre note"] };
    expect(clipNotes(en, moved, "en")).toEqual(["autre note"]);
  });

  it("gives a sound range only when the sound leaves the picture's", () => {
    expect(soundRange(CUTS)).toEqual([103.67, 106.46]);
    expect(soundRange({ ...CUTS, sound_in_s: null, sound_out_s: null })).toBeNull();
    expect(soundRange({ ...CUTS, sound_in_s: 103.75, sound_out_s: 105.75 })).toBeNull();
    expect(soundRange({ ...CUTS, sound_in_s: null, sound_out_s: 107 })).toEqual([103.75, 107]);
  });

  it("bands the usability score", () => {
    expect(usabilityLevel(100)).toBe("good");
    expect(usabilityLevel(80)).toBe("good");
    expect(usabilityLevel(79)).toBe("fair");
    expect(usabilityLevel(50)).toBe("fair");
    expect(usabilityLevel(0)).toBe("poor");
  });

  it("gives each shot its roles once, in a fixed order", () => {
    const roles = rolesByShot([
      suggestion("b_roll", [0]),
      suggestion("establishing", [0]),
      suggestion("b_roll", [0]), // two blocks of one long shot
      suggestion("avoid", [2, 3]), // a block over two short shots: scored as a whole
      suggestion("avoid", [4]),
    ]);
    expect(roles.get(0)).toEqual(["establishing", "b_roll"]);
    expect(roles.get(1)).toBeUndefined();
    // « avoid » on a merged block would contradict one shot's own good score.
    expect(roles.get(3)).toBeUndefined();
    expect(roles.get(4)).toEqual(["avoid"]);
  });

  it("knows whether anything was written", () => {
    expect(hasSynthesis(READY)).toBe(true);
    expect(hasSynthesis(EMPTY)).toBe(false);
    expect(hasSynthesis({ ...EMPTY, logline: "Une phrase." })).toBe(true);
  });
});
