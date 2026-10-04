import { Link, useNavigate } from "@tanstack/react-router";
import { Database, Search, SearchX } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { errorMessage, type SearchIndex } from "@/api/client";
import { useFolders, useSearch, useSearchFacets } from "@/api/queries";
import { searchRoute } from "@/app/router";
import { EmptyState, PageHeader } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

import { SearchFilterBar } from "./SearchFilterBar";
import { SearchResults } from "./SearchResults";
import { groupByVideo, searchParams, withoutFilters, type SearchPageSearch } from "./searchState";

const PAGE = 50; // passages shown at first, and added by « Show more »
const MAX = 100; // the API gives at most this many at once

/** Why the search finds less than it could: an index still partial, words only. */
function IndexNotice({ index, note }: { index: SearchIndex; note: string | null | undefined }) {
  const { t } = useTranslation();
  const notes: string[] = [];
  if (index.indexed < index.videos) {
    notes.push(t("search.partial", { indexed: index.indexed, videos: index.videos }));
  }
  if (!index.semantic) {
    notes.push(t("search.wordsOnly"));
  } else if (index.vectors < index.passages) {
    notes.push(t("search.someVectors"));
  }
  if (note) {
    notes.push(note);
  }
  if (notes.length === 0) {
    return null;
  }
  return (
    <Alert>
      <Database className="size-4" aria-hidden />
      <AlertDescription>
        {notes.map((line) => (
          <p key={line}>{line}</p>
        ))}
      </AlertDescription>
    </Alert>
  );
}

export function SearchPage() {
  const { t } = useTranslation();
  const search = searchRoute.useSearch();
  const navigate = useNavigate({ from: "/search" });
  const [query, setQuery] = useState(search.q ?? "");

  // Typing goes to the address after a pause (the address is the page's state).
  useEffect(() => {
    const handle = window.setTimeout(() => {
      if ((search.q ?? "") !== query.trim()) {
        void navigate({
          search: (prev) => ({ ...prev, q: query.trim() || undefined }),
          replace: true,
        });
      }
    }, 300);
    return () => {
      window.clearTimeout(handle);
    };
  }, [query, search.q, navigate]);

  const update = (patch: Partial<SearchPageSearch>, clear = false): void => {
    void navigate({
      search: (prev: SearchPageSearch) => (clear ? withoutFilters(prev) : { ...prev, ...patch }),
      replace: true,
    });
  };

  const facets = useSearchFacets();
  const folders = useFolders();
  const params = searchParams(search);
  // « Show more » shows more of the same search; another search starts over.
  const shown = JSON.stringify(params);
  const [more, setMore] = useState({ shown, limit: PAGE });
  const limit = more.shown === shown ? more.limit : PAGE;
  const results = useSearch(params, limit);
  const index = results.data?.index ?? facets.data?.index;

  let content;
  if (index?.indexed === 0) {
    content = (
      <EmptyState
        icon={<Database className="size-10" />}
        title={t("search.noIndex.title")}
        body={t("search.noIndex.body")}
        action={
          <Button variant="secondary" asChild>
            <Link to="/library">{t("search.toLibrary")}</Link>
          </Button>
        }
      />
    );
  } else if (params === null) {
    content = <p className="text-muted-foreground max-w-2xl text-sm">{t("search.start")}</p>;
  } else if (results.isError) {
    content = (
      <Alert variant="destructive">
        <AlertDescription>{errorMessage(results.error)}</AlertDescription>
      </Alert>
    );
  } else if (!results.data) {
    content = (
      <div className="grid gap-3" aria-busy>
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-32 rounded-xl" />
        ))}
      </div>
    );
  } else if (results.data.hits.length === 0) {
    content = (
      <EmptyState
        icon={<SearchX className="size-10" />}
        title={t("search.empty.title")}
        body={t("search.empty.body")}
      />
    );
  } else {
    const data = results.data;
    const how = data.retrievers.length === 2 ? "both" : (data.retrievers[0] ?? "words");
    content = (
      <>
        <p className="text-muted-foreground text-sm" role="status">
          {t("search.count", { count: data.total })}
          {data.query ? ` ${t(`search.retrievers.${how}`)}` : ""}
        </p>
        <SearchResults groups={groupByVideo(data.hits)} />
        {data.total > data.hits.length && limit < MAX ? (
          <div className="flex justify-center">
            <Button
              variant="secondary"
              disabled={results.isPlaceholderData}
              onClick={() => {
                setMore({ shown, limit: Math.min(MAX, limit + PAGE) });
              }}
            >
              {t("search.loadMore")}
            </Button>
          </div>
        ) : null}
        <p className="text-muted-foreground text-xs">{t("search.generated")}</p>
      </>
    );
  }

  return (
    <div className="grid gap-6">
      <PageHeader title={t("search.title")} subtitle={t("search.subtitle")} />
      <form
        role="search"
        className="relative"
        onSubmit={(e) => {
          e.preventDefault();
          void navigate({
            search: (prev) => ({ ...prev, q: query.trim() || undefined }),
            replace: true,
          });
        }}
      >
        <Search
          className="text-muted-foreground absolute top-1/2 left-3 size-4 -translate-y-1/2"
          aria-hidden
        />
        <Input
          type="search"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
          }}
          placeholder={t("search.placeholder")}
          aria-label={t("search.label")}
          className="h-11 pl-9 text-base"
          maxLength={500}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- the page exists to type a search
          autoFocus
        />
      </form>
      <SearchFilterBar
        search={search}
        facets={facets.data}
        folders={folders.data}
        onChange={update}
      />
      {index && index.indexed > 0 ? <IndexNotice index={index} note={results.data?.note} /> : null}
      {content}
    </div>
  );
}
