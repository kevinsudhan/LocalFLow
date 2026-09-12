import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  ConfirmButton,
  EmptyState,
  PageHeader,
  TextInput,
} from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError, onBackend } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { HistoryItem, HistoryStats } from "../types";
import { relativeTime } from "./Dashboard";

export function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [stats, setStats] = useState<HistoryStats | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const toast = useAppStore((s) => s.toast);
  const settings = useAppStore((s) => s.settings);
  const searchTimer = useRef<number>();

  const load = useCallback(
    async (query: string) => {
      try {
        const result = await api.historyList(query, 200);
        setItems(result.items);
        setStats(result.stats);
      } catch (error) {
        toast("error", "Could not load history", error instanceof BridgeError ? error.message : "");
      } finally {
        setLoading(false);
      }
    },
    [toast],
  );

  useEffect(() => {
    void load("");
    const subscription = onBackend<{ id: number }>("history.added", () => void load(""));
    return () => void subscription.then((off) => off());
  }, [load]);

  useEffect(() => {
    window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => void load(search), 180);
    return () => window.clearTimeout(searchTimer.current);
  }, [search, load]);

  const grouped = useMemo(() => groupByDay(items), [items]);

  const saveEdit = async () => {
    if (!selected || draft === selected.final_text) {
      setSelected(null);
      return;
    }
    try {
      // Editing history is also how LocalFlow learns your spellings.
      await api.historyEdit(selected.id, draft);
      toast("success", "Saved", "LocalFlow will remember any spelling you corrected.");
      setSelected(null);
      void load(search);
    } catch (error) {
      toast("error", "Could not save", error instanceof BridgeError ? error.message : "");
    }
  };

  if (!settings?.privacy.store_history) {
    return (
      <div>
        <PageHeader title="History" />
        <Card>
          <EmptyState
            icon={<Icon name="history" size={22} />}
            title="History is turned off"
            description="LocalFlow is not keeping a record of your dictations. You can turn it on in Settings › History."
          />
        </Card>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        title="History"
        description={
          stats
            ? `${stats.total.toLocaleString()} dictations · ${stats.words.toLocaleString()} words · stored only on this PC`
            : "Stored only on this PC."
        }
        actions={
          <ConfirmButton
            confirmLabel="Delete everything?"
            onConfirm={() =>
              void api.historyClear().then(() => {
                setItems([]);
                toast("success", "History cleared");
              })
            }
          >
            Clear all
          </ConfirmButton>
        }
      />

      <div className="mb-4 flex gap-2">
        <div className="relative flex-1">
          <Icon
            name="search"
            className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-faint"
          />
          <TextInput
            className="pl-10"
            placeholder="Search your dictations…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search history"
          />
        </div>
      </div>

      {loading ? (
        <Card className="p-5">
          <div className="space-y-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton h-12" />
            ))}
          </div>
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Icon name="history" size={22} />}
            title={search ? "Nothing matched" : "No dictations yet"}
            description={
              search
                ? "Try a different word."
                : "Hold your shortcut anywhere in Windows and start talking."
            }
          />
        </Card>
      ) : (
        <div className="space-y-5">
          {grouped.map(([day, entries]) => (
            <div key={day}>
              <p className="section-title mb-2 px-1">{day}</p>
              <Card>
                <ul>
                  {entries.map((item) => (
                    <li key={item.id} className="group px-5 py-3.5 [&+li]:border-t [&+li]:border-line/50">
                      <div className="flex items-start gap-3">
                        <div className="min-w-0 flex-1">
                          <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-ink">
                            {item.final_text}
                          </p>
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-2xs text-faint">
                            <span>{item.app_name || "Unknown"}</span>
                            <span aria-hidden="true">·</span>
                            <span>{relativeTime(item.created_at)}</span>
                            <span aria-hidden="true">·</span>
                            <span>{item.word_count} words</span>
                            {item.used_llm && <Badge tone="accent">AI refined</Badge>}
                            {item.language && item.language !== "en" && (
                              <Badge>{item.language.toUpperCase()}</Badge>
                            )}
                            {item.audio_path && <Badge tone="warning">audio saved</Badge>}
                          </div>
                        </div>
                        <div className="flex shrink-0 gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                          <Button
                            variant="quiet"
                            title="Copy"
                            onClick={() => {
                              void navigator.clipboard.writeText(item.final_text);
                              toast("success", "Copied");
                            }}
                          >
                            <Icon name="copy" />
                          </Button>
                          <Button
                            variant="quiet"
                            title="Insert into the focused app"
                            onClick={() =>
                              void api
                                .insertText(item.final_text)
                                .then(() => toast("success", "Inserted"))
                                .catch((error) =>
                                  toast(
                                    "error",
                                    "Could not insert",
                                    error instanceof BridgeError ? error.message : "",
                                  ),
                                )
                            }
                          >
                            <Icon name="play" />
                          </Button>
                          <Button
                            variant="quiet"
                            title="Edit"
                            onClick={() => {
                              setSelected(item);
                              setDraft(item.final_text);
                            }}
                          >
                            <Icon name="edit" />
                          </Button>
                          <Button
                            variant="quiet"
                            title="Delete"
                            onClick={() =>
                              void api.historyDelete(item.id).then(() => {
                                setItems((prev) => prev.filter((x) => x.id !== item.id));
                              })
                            }
                          >
                            <Icon name="trash" />
                          </Button>
                        </div>
                      </div>

                      {selected?.id === item.id && (
                        <div className="mt-3">
                          <textarea
                            className="input min-h-[90px] resize-y font-sans"
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            aria-label="Edit dictation"
                          />
                          <p className="hint mt-1.5">
                            Correcting a word here teaches LocalFlow your spelling for next time.
                          </p>
                          <div className="mt-2 flex justify-end gap-2">
                            <Button variant="quiet" onClick={() => setSelected(null)}>
                              Cancel
                            </Button>
                            <Button variant="primary" onClick={() => void saveEdit()}>
                              Save
                            </Button>
                          </div>
                        </div>
                      )}

                      {item.audio_path && (
                        <audio
                          controls
                          preload="none"
                          className="mt-3 h-8 w-full max-w-md"
                          src={convertAssetUrl(item.audio_path)}
                        />
                      )}

                      {item.raw_transcript !== item.final_text && (
                        <details className="mt-2">
                          <summary className="cursor-pointer text-2xs text-faint hover:text-muted">
                            What was heard
                          </summary>
                          <p className="mt-1.5 whitespace-pre-wrap text-2xs italic text-muted">
                            {item.raw_transcript}
                          </p>
                        </details>
                      )}
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function groupByDay(items: HistoryItem[]): Array<[string, HistoryItem[]]> {
  const buckets = new Map<string, HistoryItem[]>();
  for (const item of items) {
    const date = new Date(
      item.created_at.endsWith("Z") || item.created_at.includes("+")
        ? item.created_at
        : `${item.created_at}Z`,
    );
    const key = Number.isNaN(date.getTime()) ? "Earlier" : dayLabel(date);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(item);
    else buckets.set(key, [item]);
  }
  return [...buckets.entries()];
}

function dayLabel(date: Date): string {
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === yesterday.toDateString()) return "Yesterday";
  return date.toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" });
}

/** Tauri's asset protocol, so a local WAV can be played in the webview. */
function convertAssetUrl(path: string): string {
  return `http://asset.localhost/${encodeURIComponent(path)}`;
}
