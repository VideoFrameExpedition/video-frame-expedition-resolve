import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Database, MessageSquareText, PlugZap, Send, Square } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";

import type { AskFilters } from "@/api/client";
import {
  queryKeys,
  useAskHistory,
  useDeleteQuestion,
  useFolders,
  useLmModels,
  useQuestion,
  useSearchFacets,
} from "@/api/queries";
import { askRoute } from "@/app/router";
import { EmptyState, PageHeader } from "@/components/common";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { SearchFilterBar } from "@/features/search/SearchFilterBar";
import type { SearchPageSearch } from "@/features/search/searchState";

import { AnswerView, VisualCheckNote } from "./AnswerView";
import { askFilters, filtersOf, fromAskFilters, type AskPageSearch } from "./askState";
import { QuestionHistory } from "./QuestionHistory";
import { useAskStream, type AskState } from "./useAskStream";

const MAX_QUESTION = 1000;
const BUSY = new Set(["searching", "writing", "checking"]);

/** What LM Studio offers now: off, nothing loaded, or a model (and whether it reads images). */
function useLoadedModel() {
  const models = useLmModels();
  if (models.isError) {
    return { state: "offline" as const, vision: false };
  }
  const loaded = (models.data ?? []).filter(
    (model) => model.type === "llm" && model.loaded_instances.length > 0,
  );
  if (models.data && loaded.length === 0) {
    return { state: "noModel" as const, vision: false };
  }
  return { state: "ready" as const, vision: loaded.some((model) => model.vision) };
}

function errorText(code: string, message: string, t: (key: string) => string): string {
  const known: Record<string, string> = {
    lmstudio_unavailable: "ask.error.lmstudio",
    no_model_loaded: "ask.error.noModel",
    empty_index: "ask.error.emptyIndex",
    interrupted: "ask.error.interrupted",
  };
  const key = known[code];
  return key ? t(key) : message;
}

/** The answer while it is written (plain text; its citations become links once kept). */
function LiveAnswer({ state }: { state: AskState }) {
  const { t } = useTranslation();
  return (
    <article className="grid gap-4" aria-busy={BUSY.has(state.phase)}>
      <h2 className="min-w-0 text-lg font-semibold break-words">{state.question}</h2>
      {state.start ? (
        <p className="text-muted-foreground text-xs">
          {t("ask.reading", { count: state.start.passages, model: state.start.model })}
        </p>
      ) : null}
      {state.phase === "searching" ? (
        <div className="grid gap-2">
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : (
        <p className="text-[0.95rem] leading-relaxed break-words whitespace-pre-wrap">
          {state.text}
          {state.phase === "writing" ? (
            <span className="bg-brand-teal ml-0.5 inline-block h-4 w-1.5 animate-pulse align-middle" />
          ) : null}
        </p>
      )}
      {state.phase === "stopped" ? (
        <p className="text-muted-foreground text-xs">{t("ask.phase.stopped")}</p>
      ) : null}
      {state.error ? (
        <Alert variant="destructive">
          <PlugZap className="size-4" aria-hidden />
          <AlertDescription>{errorText(state.error.code, state.error.message, t)}</AlertDescription>
        </Alert>
      ) : null}
      {state.result?.visual_check ? <VisualCheckNote check={state.result.visual_check} /> : null}
    </article>
  );
}

export function AskPage() {
  const { t } = useTranslation();
  const search = askRoute.useSearch();
  const navigate = useNavigate({ from: "/ask" });
  const queryClient = useQueryClient();
  const questionId = useId();
  const checkId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [visualCheck, setVisualCheck] = useState(false);

  const facets = useSearchFacets();
  const folders = useFolders();
  const history = useAskHistory();
  const remove = useDeleteQuestion();
  const loaded = useLoadedModel();
  const filters = filtersOf(search);

  const stream = useAskStream({
    onSettled: (state) => {
      if (state.result) {
        const result = state.result;
        queryClient.setQueryData(queryKeys.question(result.id), result);
        void navigate({
          search: (prev: AskPageSearch) => ({ ...prev, id: result.id }),
          replace: true,
          resetScroll: false,
        });
      }
      // A stopped answer is kept once the server notices the closed connection.
      window.setTimeout(
        () => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.askHistory });
        },
        state.phase === "stopped" ? 800 : 0,
      );
    },
  });
  const live = stream.state;
  const liveId = live.result?.id ?? live.start?.id;
  const showLive = live.phase !== "idle" && (search.id === undefined || search.id === liveId);
  const stored = useQuestion(showLive ? undefined : search.id);

  const busy = BUSY.has(live.phase);
  const indexed = facets.data?.index.indexed;
  const blocked = loaded.state !== "ready" || indexed === 0;

  const update = (patch: Partial<SearchPageSearch>, clear = false): void => {
    void navigate({
      search: (prev: AskPageSearch) => {
        const kept = clear ? (prev.id ? { id: prev.id } : {}) : { ...prev, ...patch };
        return kept;
      },
      replace: true,
      resetScroll: false,
    });
  };

  const submit = (): void => {
    const text = question.trim();
    if (!text || busy || blocked) {
      return;
    }
    void navigate({
      search: (prev: AskPageSearch) => filtersOf(prev),
      replace: true,
      resetScroll: false,
    });
    void stream.ask({
      question: text,
      filters: askFilters(filters),
      visual_check: visualCheck && loaded.vision,
    });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  // Another answer: back to the top, where it starts (the history is below it on a phone).
  const open = (id: string): void => {
    stream.reset();
    void navigate({
      search: (prev: AskPageSearch) => ({ ...prev, id }),
      replace: true,
      resetScroll: true,
    });
  };

  const askAgain = (text: string, from: AskFilters): void => {
    stream.reset();
    setQuestion(text);
    void navigate({ search: () => fromAskFilters(from), replace: true, resetScroll: true });
    input.current?.focus();
  };

  const onDelete = (id: string): void => {
    remove.mutate(id, {
      onSuccess: () => {
        if (id === search.id || id === liveId) {
          stream.reset();
          void navigate({
            search: (prev: AskPageSearch) => filtersOf(prev),
            replace: true,
            resetScroll: false,
          });
        }
      },
    });
  };

  let content;
  if (indexed === 0) {
    content = (
      <EmptyState
        icon={<Database className="size-10" />}
        title={t("ask.noIndex.title")}
        body={t("ask.noIndex.body")}
        action={
          <Button variant="secondary" asChild>
            <Link to="/library">{t("search.toLibrary")}</Link>
          </Button>
        }
      />
    );
  } else if (showLive && live.phase === "done" && live.result) {
    const result = live.result;
    content = (
      <AnswerView
        result={result}
        onAskAgain={() => {
          askAgain(result.question, result.filters);
        }}
      />
    );
  } else if (showLive) {
    content = <LiveAnswer state={live} />;
  } else if (search.id !== undefined && stored.isError) {
    content = (
      <Alert variant="destructive">
        <AlertDescription>{t("ask.error.notFound")}</AlertDescription>
      </Alert>
    );
  } else if (search.id !== undefined && !stored.data) {
    content = <Skeleton className="h-40 rounded-xl" />;
  } else if (stored.data) {
    const result = stored.data;
    content = (
      <AnswerView
        result={result}
        onAskAgain={() => {
          askAgain(result.question, result.filters);
        }}
      />
    );
  } else {
    content = <p className="text-muted-foreground max-w-2xl text-sm">{t("ask.start")}</p>;
  }

  const announcement =
    live.phase === "idle" || live.phase === "error"
      ? ""
      : t(`ask.phase.${live.phase}`, { count: live.frames });

  return (
    <div className="grid gap-6">
      <PageHeader title={t("ask.title")} subtitle={t("ask.subtitle")} />
      {loaded.state !== "ready" ? (
        <Alert variant={loaded.state === "offline" ? "destructive" : "default"}>
          <PlugZap className="size-4" aria-hidden />
          <AlertDescription>{t(`ask.lmstudio.${loaded.state}`)}</AlertDescription>
        </Alert>
      ) : null}
      <form
        className="glass grid gap-3 rounded-xl border p-4"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <Label htmlFor={questionId}>{t("ask.label")}</Label>
        <Textarea
          id={questionId}
          ref={input}
          value={question}
          onChange={(event) => {
            setQuestion(event.target.value);
          }}
          onKeyDown={onKeyDown}
          placeholder={t("ask.placeholder")}
          maxLength={MAX_QUESTION}
          rows={2}
          className="min-h-20 text-base"
          aria-describedby={`${questionId}-hint`}
          // eslint-disable-next-line jsx-a11y/no-autofocus -- the page exists to type a question
          autoFocus
        />
        <p id={`${questionId}-hint`} className="text-muted-foreground text-xs">
          {t("ask.hint")}
        </p>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Switch
              id={checkId}
              checked={visualCheck && loaded.vision}
              disabled={!loaded.vision}
              onCheckedChange={setVisualCheck}
              aria-describedby={`${checkId}-hint`}
            />
            <Label htmlFor={checkId}>{t("ask.check.label")}</Label>
            <span id={`${checkId}-hint`} className="text-muted-foreground hidden text-xs md:inline">
              {loaded.state === "ready" && !loaded.vision
                ? t("ask.check.noVision")
                : t("ask.check.hint")}
            </span>
          </div>
          {busy ? (
            <Button type="button" variant="secondary" onClick={stream.stop}>
              <Square className="size-3.5 fill-current" aria-hidden />
              {t("ask.stop")}
            </Button>
          ) : (
            <Button type="submit" disabled={!question.trim() || blocked}>
              <Send className="size-4" aria-hidden />
              {t("ask.submit")}
            </Button>
          )}
        </div>
      </form>
      <SearchFilterBar
        search={filters}
        facets={facets.data}
        folders={folders.data}
        onChange={update}
      />
      <p className="sr-only" role="status" aria-live="polite">
        {announcement}
      </p>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <section
          className="glass min-h-40 rounded-xl border p-4 md:p-5"
          aria-label={t("ask.answer")}
        >
          {content}
        </section>
        <aside className="lg:border-l lg:pl-4">
          <QuestionHistory
            items={history.data?.items}
            current={showLive ? liveId : search.id}
            onOpen={open}
            onDelete={onDelete}
            deleting={remove.isPending}
          />
        </aside>
      </div>
      <p className="text-muted-foreground flex items-center gap-2 text-xs">
        <MessageSquareText className="size-3.5" aria-hidden />
        {t("ask.privacy")}
      </p>
    </div>
  );
}
