import type { AskCitation } from "@/api/client";

import {
  answerParts,
  askFilters,
  filtersOf,
  fromAskFilters,
  paragraphs,
  validateAskPage,
} from "./askState";

function citation(n: number, overrides: Partial<AskCitation> = {}): AskCitation {
  return {
    n,
    video_id: `v${n.toString()}`,
    filename: "vacances.mp4",
    title: null,
    kind: "shot",
    t_start: 45,
    t_end: 60,
    shot_idx: 2,
    timecode: "00:45",
    thumb_url: null,
    excerpt: "Une femme verse du riz.",
    available: true,
    ...overrides,
  };
}

describe("the address of the « Questions » page", () => {
  it("keeps the filters and a question id, never a search text", () => {
    expect(
      validateAskPage({
        q: "riz",
        weather: "snow",
        speech: "no",
        id: "01a0e4c661f575dc97927e143604df49",
      }),
    ).toEqual({ weather: "snow", speech: "no", id: "01a0e4c661f575dc97927e143604df49" });
    expect(validateAskPage({ id: "../../etc" })).toEqual({});
    expect(filtersOf({ weather: "snow", id: "x" })).toEqual({ weather: "snow" });
  });

  it("sends the filters as the endpoint takes them, and reads them back", () => {
    const search = {
      kind: "shot" as const,
      from: "2026-07-01",
      weather: "clear" as const,
      light: "golden_hour" as const,
      speech: "yes" as const,
      subject: "chat",
      shot: "wide" as const,
      rating: 4,
      favorite: true as const,
      root: "r1",
      folder: "Sommets",
      usability: 70,
    };
    const filters = askFilters(search);
    expect(JSON.parse(JSON.stringify(filters))).toEqual({
      kinds: ["shot"],
      date_from: "2026-07-01",
      weather: ["clear"],
      light_phase: ["golden_hour"],
      has_speech: true,
      subjects: ["chat"],
      shot_types: ["wide"],
      min_rating: 4,
      favorite: true,
      root_id: "r1",
      folder: "Sommets",
      min_usability: 70,
    });
    expect(fromAskFilters(filters)).toEqual(search);
    expect(fromAskFilters({ has_speech: false, weather: [] })).toEqual({ speech: "no" });
    expect(JSON.parse(JSON.stringify(askFilters({})))).toEqual({
      kinds: [],
      weather: [],
      light_phase: [],
      subjects: [],
      shot_types: [],
    });
  });
});

describe("answerParts", () => {
  it("picks out the citations of the answer, and nothing else", () => {
    const one = citation(1);
    const three = citation(3);
    expect(answerParts("Du riz [1] et [2], puis [3].", [one, three])).toEqual([
      { text: "Du riz " },
      { citation: one },
      { text: " et [2], puis " },
      { citation: three },
      { text: "." },
    ]);
    expect(answerParts("<b>gras</b> [1]", [])).toEqual([{ text: "<b>gras</b> [1]" }]);
  });

  it("cuts paragraphs at blank lines", () => {
    expect(paragraphs("Un.\nDeux.\n\n  \nTrois.")).toEqual(["Un.\nDeux.", "Trois."]);
    expect(paragraphs("  ")).toEqual([]);
  });
});
