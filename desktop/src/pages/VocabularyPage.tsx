import { useEffect, useRef, useState } from "react";

import {
  Badge,
  Button,
  Card,
  ConfirmButton,
  EmptyState,
  Field,
  Modal,
  PageHeader,
  Section,
  TextInput,
  Toggle,
} from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { CorrectionItem, VocabularyItem } from "../types";

export function VocabularyPage() {
  const [items, setItems] = useState<VocabularyItem[]>([]);
  const [corrections, setCorrections] = useState<CorrectionItem[]>([]);
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<VocabularyItem | null>(null);
  const [adding, setAdding] = useState(false);
  const toast = useAppStore((s) => s.toast);
  const settings = useAppStore((s) => s.settings);
  const patch = useAppStore((s) => s.patch);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    try {
      const [vocabulary, learned] = await Promise.all([
        api.vocabularyList(search),
        api.correctionsList(),
      ]);
      setItems(vocabulary.items);
      setCorrections(learned.items);
    } catch (error) {
      toast("error", "Could not load vocabulary", error instanceof BridgeError ? error.message : "");
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 150);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  return (
    <div>
      <PageHeader
        title="Vocabulary"
        description="Names, products and jargon that speech recognition would otherwise get wrong. LocalFlow biases Whisper towards these terms and repairs near-misses afterwards."
        actions={
          <>
            <Button
              onClick={async () => {
                const data = await api.vocabularyExport();
                const blob = new Blob([JSON.stringify(data.items, null, 2)], {
                  type: "application/json",
                });
                const url = URL.createObjectURL(blob);
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = "localflow-vocabulary.json";
                anchor.click();
                URL.revokeObjectURL(url);
              }}
              icon={<Icon name="download" />}
            >
              Export
            </Button>
            <Button onClick={() => fileRef.current?.click()}>Import</Button>
            <Button variant="primary" onClick={() => setAdding(true)} icon={<Icon name="plus" />}>
              Add word
            </Button>
          </>
        }
      />

      <input
        ref={fileRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          try {
            const parsed = JSON.parse(await file.text());
            const list = Array.isArray(parsed) ? parsed : parsed.items;
            const result = await api.vocabularyImport(list);
            toast("success", `Imported ${result.added} terms`);
            void load();
          } catch (error) {
            toast("error", "Could not import", error instanceof Error ? error.message : "");
          } finally {
            event.target.value = "";
          }
        }}
      />

      <div className="mb-4 relative">
        <Icon
          name="search"
          className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-faint"
        />
        <TextInput
          className="pl-10"
          placeholder="Search terms…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search vocabulary"
        />
      </div>

      <Card className="mb-7">
        {items.length === 0 ? (
          <EmptyState
            icon={<Icon name="book" size={22} />}
            title={search ? "Nothing matched" : "No terms yet"}
            description={
              search
                ? "Try a different search."
                : "Add the names and jargon you use every day - colleagues, clients, products, acronyms."
            }
            action={
              !search && (
                <Button variant="primary" onClick={() => setAdding(true)}>
                  Add your first word
                </Button>
              )
            }
          />
        ) : (
          <ul>
            {items.map((item) => (
              <li
                key={item.id}
                className="group flex items-center gap-3 px-5 py-3 [&+li]:border-t [&+li]:border-line/50"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-medium text-ink">{item.term}</span>
                    {item.category !== "general" && <Badge>{item.category}</Badge>}
                    {item.hits > 0 && (
                      <span className="text-2xs text-faint">
                        corrected {item.hits} {item.hits === 1 ? "time" : "times"}
                      </span>
                    )}
                  </div>
                  {item.sounds_like.length > 0 && (
                    <p className="hint mt-1">
                      Also heard as: {item.sounds_like.join(", ")}
                    </p>
                  )}
                </div>
                <Toggle
                  checked={item.enabled}
                  label={`Enable ${item.term}`}
                  onChange={(next) =>
                    void api.vocabularyUpdate(item.id, { enabled: next }).then(load)
                  }
                />
                <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                  <Button variant="quiet" title="Edit" onClick={() => setEditing(item)}>
                    <Icon name="edit" />
                  </Button>
                  <Button
                    variant="quiet"
                    title="Delete"
                    onClick={() => void api.vocabularyDelete(item.id).then(load)}
                  >
                    <Icon name="trash" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Section
        title="Learned from your edits"
        description="When you fix the same word repeatedly in History, LocalFlow starts applying that correction automatically. This never leaves your machine."
        actions={
          <>
            <Toggle
              checked={settings?.processing.learning_enabled ?? true}
              label="Enable learning"
              onChange={(next) => void patch({ processing: { learning_enabled: next } })}
            />
            {corrections.length > 0 && (
              <ConfirmButton
                confirmLabel="Forget all?"
                onConfirm={() => void api.correctionsClear().then(load)}
              >
                Forget all
              </ConfirmButton>
            )}
          </>
        }
      >
        <Card>
          {corrections.length === 0 ? (
            <EmptyState
              icon={<Icon name="sparkle" size={22} />}
              title="Nothing learned yet"
              description="Edit a dictation in History and LocalFlow will notice the pattern."
            />
          ) : (
            <ul>
              {corrections.map((item) => (
                <li
                  key={item.id}
                  className="group flex items-center gap-3 px-5 py-2.5 [&+li]:border-t [&+li]:border-line/50"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-[13px] text-ink">
                      <span className="text-muted line-through">{item.wrong}</span>
                      <span className="mx-2 text-faint" aria-label="becomes">
                        →
                      </span>
                      <span className="font-medium">{item.correct}</span>
                    </p>
                    <p className="hint mt-0.5">
                      seen {item.occurrences}{" "}
                      {item.occurrences === 1 ? "time" : "times"}
                      {item.promoted ? " · applied automatically" : " · not applied yet"}
                    </p>
                  </div>
                  {item.promoted ? (
                    <Badge tone="positive">Active</Badge>
                  ) : (
                    <Badge>Learning</Badge>
                  )}
                  <Toggle
                    checked={item.enabled}
                    label={`Enable ${item.wrong} correction`}
                    onChange={(next) =>
                      void api.correctionsToggle(item.id, next).then(load)
                    }
                  />
                  <Button
                    variant="quiet"
                    title="Forget"
                    onClick={() => void api.correctionsDelete(item.id).then(load)}
                  >
                    <Icon name="trash" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </Section>

      <VocabularyDialog
        open={adding || editing !== null}
        item={editing}
        onClose={() => {
          setAdding(false);
          setEditing(null);
        }}
        onSaved={() => {
          setAdding(false);
          setEditing(null);
          void load();
        }}
      />
    </div>
  );
}

function VocabularyDialog({
  open,
  item,
  onClose,
  onSaved,
}: {
  open: boolean;
  item: VocabularyItem | null;
  onClose(): void;
  onSaved(): void;
}) {
  const [term, setTerm] = useState("");
  const [aliases, setAliases] = useState("");
  const [category, setCategory] = useState("general");
  const [error, setError] = useState("");
  const toast = useAppStore((s) => s.toast);

  useEffect(() => {
    if (!open) return;
    setTerm(item?.term ?? "");
    setAliases(item?.sounds_like.join(", ") ?? "");
    setCategory(item?.category ?? "general");
    setError("");
  }, [open, item]);

  const save = async () => {
    if (!term.trim()) {
      setError("Enter the word as it should be written.");
      return;
    }
    const list = aliases
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    try {
      if (item) {
        await api.vocabularyUpdate(item.id, {
          term: term.trim(),
          sounds_like: list,
          category,
        });
      } else {
        await api.vocabularyAdd(term.trim(), list, category);
      }
      onSaved();
    } catch (err) {
      toast("error", "Could not save", err instanceof BridgeError ? err.message : String(err));
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={item ? "Edit term" : "Add a term"}
      footer={
        <>
          <Button variant="quiet" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()}>
            Save
          </Button>
        </>
      }
    >
      <Field label="Correct spelling" error={error} hint="Exactly as you want it written.">
        {(id) => (
          <TextInput
            id={id}
            value={term}
            spellCheck={false}
            placeholder="Araxys"
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void save()}
          />
        )}
      </Field>
      <Field
        label="What it sounds like"
        hint="Comma separated. Add whatever the recogniser actually produces - for example “Araxis, a raxis”. LocalFlow also catches close misses on its own."
      >
        {(id) => (
          <TextInput
            id={id}
            value={aliases}
            spellCheck={false}
            placeholder="Araxis, a raxis"
            onChange={(e) => setAliases(e.target.value)}
          />
        )}
      </Field>
      <Field label="Category" hint="Only used for organising this list.">
        {(id) => (
          <TextInput
            id={id}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            placeholder="general"
          />
        )}
      </Field>
    </Modal>
  );
}
