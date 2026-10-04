import type { AskCitation, AskFilters } from "@/api/client";
import { validateSearchPage, type SearchPageSearch } from "@/features/search/searchState";

/** The « Questions » page's address: the filters of the search, and the question shown. */
export type AskPageSearch = Omit<SearchPageSearch, "q"> & { id?: string };

const QUESTION_ID = /^[0-9a-f]{32}$/;

export function validateAskPage(search: Record<string, unknown>): AskPageSearch {
  const filters = validateSearchPage(search);
  delete filters.q; // the question is typed, not in the address
  const result: AskPageSearch = filters;
  if (typeof search.id === "string" && QUESTION_ID.test(search.id)) {
    result.id = search.id;
  }
  return result;
}

/** The filters alone (for the filter row, which knows nothing of the question shown). */
export function filtersOf(search: AskPageSearch): SearchPageSearch {
  const filters = { ...search };
  delete filters.id;
  return filters;
}

/** The filters as the question endpoint takes them (an unset one is undefined: JSON leaves it
 * out of the request). */
export function askFilters(search: SearchPageSearch): AskFilters {
  return {
    kinds: search.kind ? [search.kind] : [],
    date_from: search.from,
    date_to: search.to,
    place: search.place,
    weather: search.weather ? [search.weather] : [],
    light_phase: search.light ? [search.light] : [],
    device: search.device,
    orientation: search.orientation,
    has_speech: search.speech === undefined ? undefined : search.speech === "yes",
    subjects: search.subject ? [search.subject] : [],
    shot_types: search.shot ? [search.shot] : [],
    min_rating: search.rating,
    favorite: search.favorite,
    root_id: search.root,
    folder: search.root !== undefined ? search.folder : undefined,
    min_usability: search.usability,
  };
}

/** The filters a question was asked with, back in the page's address (« Ask again »). The row
 * offers one value per filter: the first one is kept. */
export function fromAskFilters(filters: AskFilters): SearchPageSearch {
  return validateSearchPage({
    kind: filters.kinds?.[0],
    from: filters.date_from ?? undefined,
    to: filters.date_to ?? undefined,
    place: filters.place ?? undefined,
    weather: filters.weather?.[0],
    light: filters.light_phase?.[0],
    device: filters.device ?? undefined,
    orientation: filters.orientation ?? undefined,
    speech:
      filters.has_speech === undefined || filters.has_speech === null
        ? undefined
        : filters.has_speech
          ? "yes"
          : "no",
    subject: filters.subjects?.[0],
    shot: filters.shot_types?.[0],
    rating: filters.min_rating ?? undefined,
    favorite: filters.favorite === true ? true : undefined,
    root: filters.root_id ?? undefined,
    folder: filters.root_id ? (filters.folder ?? "") : undefined,
    usability: filters.min_usability ?? undefined,
  });
}

/** A piece of an answer: plain text, or a citation « [n] » of a passage the answer cites. */
export type AnswerPart = { text: string } | { citation: AskCitation };

/** The answer's text with its citations picked out; a number the answer does not cite (or
 * that is not a passage) stays plain text. Never read as HTML. */
export function answerParts(text: string, citations: readonly AskCitation[]): AnswerPart[] {
  const byNumber = new Map(citations.map((citation) => [citation.n, citation]));
  const parts: AnswerPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(/\[(\d{1,3})\]/g)) {
    const citation = byNumber.get(Number(match[1]));
    if (!citation) {
      continue;
    }
    if (match.index > cursor) {
      parts.push({ text: text.slice(cursor, match.index) });
    }
    parts.push({ citation });
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) {
    parts.push({ text: text.slice(cursor) });
  }
  return parts;
}

/** Paragraphs of an answer (blank lines between them). */
export function paragraphs(text: string): string[] {
  return text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
}
