import { useEffect, useState } from "react";

import {
  Button,
  Card,
  EmptyState,
  Field,
  Modal,
  PageHeader,
  TextInput,
  Toggle,
} from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { SnippetItem } from "../types";

export function SnippetsPage() {
  const [items, setItems] = useState<SnippetItem[]>([]);
  const [editing, setEditing] = useState<SnippetItem | null>(null);
  const [adding, setAdding] = useState(false);
  const toast = useAppStore((s) => s.toast);
  const settings = useAppStore((s) => s.settings);
  const patch = useAppStore((s) => s.patch);

  const load = () =>
    api
      .snippetsList()
      .then((result) => setItems(result.items))
      .catch((error) =>
        toast("error", "Could not load snippets", error instanceof BridgeError ? error.message : ""),
      );

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <PageHeader
        title="Snippets"
        description="Say a trigger phrase and LocalFlow inserts the saved text exactly - no rewriting, no paraphrasing."
        actions={
          <>
            <Toggle
              checked={settings?.processing.snippets_enabled ?? true}
              label="Enable snippets"
              onChange={(next) => void patch({ processing: { snippets_enabled: next } })}
            />
            <Button variant="primary" onClick={() => setAdding(true)} icon={<Icon name="plus" />}>
              New snippet
            </Button>
          </>
        }
      />

      <Card>
        {items.length === 0 ? (
          <EmptyState
            icon={<Icon name="snippet" size={22} />}
            title="No snippets yet"
            description="Good candidates: your email signature, a standard reply, a meeting follow-up."
            action={
              <Button variant="primary" onClick={() => setAdding(true)}>
                Create a snippet
              </Button>
            }
          />
        ) : (
          <ul>
            {items.map((item) => (
              <li
                key={item.id}
                className="group px-5 py-3.5 [&+li]:border-t [&+li]:border-line/50"
              >
                <div className="flex items-start gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-medium text-ink">{item.name}</span>
                      {item.uses > 0 && (
                        <span className="text-2xs text-faint">used {item.uses}×</span>
                      )}
                    </div>
                    <p className="mt-1.5 text-2xs text-muted">
                      Say{" "}
                      <span className="chip font-mono text-ink">
                        <Icon name="mic" size={11} />
                        {item.trigger}
                      </span>
                    </p>
                    <pre className="well mt-2 max-h-28 overflow-auto whitespace-pre-wrap p-3 font-sans text-2xs leading-relaxed text-muted">
                      {item.expansion}
                    </pre>
                  </div>
                  <Toggle
                    checked={item.enabled}
                    label={`Enable ${item.name}`}
                    onChange={(next) =>
                      void api.snippetsUpdate(item.id, { enabled: next }).then(load)
                    }
                  />
                  <div className="flex gap-1 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <Button variant="quiet" title="Edit" onClick={() => setEditing(item)}>
                      <Icon name="edit" />
                    </Button>
                    <Button
                      variant="quiet"
                      title="Delete"
                      onClick={() => void api.snippetsDelete(item.id).then(load)}
                    >
                      <Icon name="trash" />
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <SnippetDialog
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

function SnippetDialog({
  open,
  item,
  onClose,
  onSaved,
}: {
  open: boolean;
  item: SnippetItem | null;
  onClose(): void;
  onSaved(): void;
}) {
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState("");
  const [expansion, setExpansion] = useState("");
  const [error, setError] = useState("");
  const toast = useAppStore((s) => s.toast);

  useEffect(() => {
    if (!open) return;
    setName(item?.name ?? "");
    setTrigger(item?.trigger ?? "");
    setExpansion(item?.expansion ?? "");
    setError("");
  }, [open, item]);

  const save = async () => {
    if (!trigger.trim() || !expansion.trim()) {
      setError("A snippet needs both a trigger phrase and some text.");
      return;
    }
    try {
      if (item) {
        await api.snippetsUpdate(item.id, {
          name: name.trim() || trigger.trim(),
          trigger: trigger.trim(),
          expansion,
        });
      } else {
        await api.snippetsAdd(name.trim() || trigger.trim(), trigger.trim(), expansion);
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
      width={600}
      title={item ? "Edit snippet" : "New snippet"}
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
      <Field label="Name" hint="Just for your own reference.">
        {(id) => (
          <TextInput
            id={id}
            value={name}
            placeholder="Email signature"
            onChange={(e) => setName(e.target.value)}
          />
        )}
      </Field>
      <Field
        label="Trigger phrase"
        error={error}
        hint="Say this and nothing else. LocalFlow also accepts a natural lead-in, so “insert my email signature” works for the trigger “email signature”."
      >
        {(id) => (
          <TextInput
            id={id}
            value={trigger}
            spellCheck={false}
            placeholder="email signature"
            onChange={(e) => setTrigger(e.target.value)}
          />
        )}
      </Field>
      <Field label="Text to insert" hint="Inserted exactly as written, including line breaks.">
        {(id) => (
          <textarea
            id={id}
            className="input min-h-[140px] resize-y font-sans leading-relaxed"
            value={expansion}
            onChange={(e) => setExpansion(e.target.value)}
            placeholder={"Best regards,\nKevin Sudhan"}
          />
        )}
      </Field>
    </Modal>
  );
}
