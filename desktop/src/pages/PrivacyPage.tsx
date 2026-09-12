import { useEffect, useState } from "react";

import { Button, Card, ConfirmButton, PageHeader, Section, StatusDot } from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { PrivacyReport } from "../types";

export function PrivacyPage() {
  const [report, setReport] = useState<PrivacyReport | null>(null);
  const settings = useAppStore((s) => s.settings);
  const patch = useAppStore((s) => s.patch);
  const toast = useAppStore((s) => s.toast);

  useEffect(() => {
    void api.privacy().then(setReport).catch(() => undefined);
  }, [settings]);

  const rows = report
    ? [
        ["Audio processing", report.audio_processing],
        ["Speech recognition", report.speech_recognition],
        ["AI processing", report.ai_processing],
        ["History", report.history],
        ["Audio history", report.audio_history],
        ["Telemetry", report.telemetry],
      ]
    : [];

  return (
    <div>
      <PageHeader
        title="Privacy"
        description="LocalFlow has no account, no server and no analytics. This page states plainly where everything happens."
      />

      <Card className="mb-6 p-6">
        <div className="mb-5 flex items-center gap-3">
          <div className="nm-inset flex h-11 w-11 items-center justify-center rounded-2xl text-positive">
            <Icon name="shield" size={20} />
          </div>
          <div>
            <p className="text-[15px] font-semibold tracking-tight text-ink">
              Your voice never leaves this PC
            </p>
            <p className="hint mt-0.5">
              Recording, transcription and AI cleanup all run as local processes.
            </p>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-8 gap-y-0">
          {rows.map(([label, value]) => (
            <div
              key={label}
              className="flex items-center justify-between gap-4 border-b border-line/50 py-2.5 last:border-0"
            >
              <dt className="text-2xs text-muted">{label}</dt>
              <dd className="flex items-center gap-2 text-2xs font-medium text-ink">
                <StatusDot
                  tone={
                    /not stored|disabled/i.test(value)
                      ? "neutral"
                      : /local/i.test(value)
                        ? "positive"
                        : "warning"
                  }
                />
                {value}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <Section
        title="Network access"
        description="The complete list of network connections LocalFlow can make."
      >
        <Card>
          {report?.network.map((entry) => (
            <div key={entry.name} className="row">
              <div className="min-w-0 flex-1">
                <p className="label">{entry.name}</p>
                <p className="hint mt-1">{entry.scope}</p>
              </div>
              <code className="chip font-mono text-2xs">{entry.endpoint}</code>
            </div>
          ))}
        </Card>
      </Section>

      <Section title="Your data" description="Everything LocalFlow stores lives in one folder.">
        <Card>
          <div className="row">
            <div className="min-w-0 flex-1">
              <p className="label">Data folder</p>
              <p className="hint mt-1 break-all font-mono">{report?.data_dir}</p>
            </div>
            <Button
              onClick={() => report && void api.openPath(report.data_dir)}
              icon={<Icon name="folder" />}
            >
              Open
            </Button>
          </div>
          <div className="row">
            <div className="min-w-0 flex-1">
              <p className="label">Keep recordings of my voice</p>
              <p className="hint mt-1">
                Off by default. Turning this on writes a WAV file for every dictation so you can
                replay it. Those files stay on this PC, but they are recordings of your voice -
                leave this off unless you need it.
              </p>
            </div>
            <Button
              variant={settings?.privacy.store_audio ? "danger" : "ghost"}
              onClick={() =>
                void patch({ privacy: { store_audio: !settings?.privacy.store_audio } })
              }
            >
              {settings?.privacy.store_audio ? "Turn off" : "Turn on"}
            </Button>
          </div>
          <div className="row">
            <div className="min-w-0 flex-1">
              <p className="label">Delete everything</p>
              <p className="hint mt-1">
                Erases all history, saved recordings and learned corrections. Settings, vocabulary
                and snippets are kept.
              </p>
            </div>
            <ConfirmButton
              confirmLabel="Delete it all?"
              onConfirm={() =>
                void api.wipeAll().then(() => toast("success", "All dictation data deleted"))
              }
            >
              Delete my data
            </ConfirmButton>
          </div>
        </Card>
      </Section>
    </div>
  );
}
