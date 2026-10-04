import type { Synthesis, SynthesisChapter, SynthesisHighlight } from "@/api/client";

/** Test data shaped like GET /videos/{id}/synthesis (a 10 s video, two chapters). */
export const CHAPTERS: SynthesisChapter[] = [
  {
    index: 1,
    start_s: 0,
    end_s: 6,
    title: "Arrivée du héron",
    summary: "Un héron se pose sur la berge.",
  },
  {
    index: 2,
    start_s: 6,
    end_s: 10,
    title: "Envol",
    summary: "Le héron s'envole au-dessus du lac.",
  },
];

export const HIGHLIGHTS: SynthesisHighlight[] = [
  {
    rank: 1,
    chapter: 1,
    clip: {
      picture_in_s: 2,
      picture_out_s: 4,
      sound_in_s: 1.5,
      sound_out_s: 4.8,
      notes: [
        "le son commence 0,5 s avant l'image (J-cut)",
        "le son continue 0,8 s après la coupe (L-cut)",
      ],
    },
    keyframe_id: "k1",
    thumb_url: "/thumbs/k1.jpg",
    reason: "Le héron se pose en déployant ses ailes.",
    criteria: ["utilisable 96", "plan fort", "parole"],
  },
  {
    rank: 2,
    chapter: 2,
    clip: {
      picture_in_s: 7,
      picture_out_s: 9,
      sound_in_s: null,
      sound_out_s: null,
      notes: ["entrée déplacée entre deux mots"],
    },
    keyframe_id: null,
    thumb_url: null,
    reason: "Le héron s'envole.",
    criteria: ["utilisable 88", "action"],
  },
];

export const READY: Synthesis = {
  status: "ready",
  note: null,
  stale: false,
  title: "Le héron du lac",
  logline: "Un héron se pose, puis s'envole.",
  summary: "Au bord d'un lac, un héron se pose sur la berge avant de repartir.",
  chapters: CHAPTERS,
  highlights: HIGHLIGHTS,
  suggestions: [],
  usability: [],
  weather: {
    category: "partly_cloudy",
    agreement: "agree",
    confidence: "high",
    line: "partiellement nuageux (Open-Meteo : nuages 40 % ; images : partiellement nuageux)",
  },
  tags: [
    { label: "héron", source: "llm" },
    { label: "lac", source: "frames" },
    { label: "cri d'oiseau", source: "sounds" },
  ],
  model: "qwen/qwen3-vl-4b",
  created_at: "2026-09-27T17:17:08Z",
  strategy: "single",
  proofread: true,
};

/** Nothing written yet (the weather and the usability are computed on read all the same). */
export const EMPTY: Synthesis = {
  ...READY,
  status: "not_run",
  title: null,
  logline: null,
  summary: null,
  chapters: [],
  highlights: [],
  tags: [],
  model: null,
  created_at: null,
  strategy: null,
  proofread: false,
};
