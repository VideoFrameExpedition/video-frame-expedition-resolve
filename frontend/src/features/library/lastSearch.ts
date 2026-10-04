import type { LibrarySearch } from "@/app/router";

// The library's last bin and filters, found again on the way back from a video page.
let last: LibrarySearch = {};

export function rememberLibrarySearch(search: LibrarySearch): void {
  last = search;
}

export function lastLibrarySearch(): LibrarySearch {
  return last;
}
