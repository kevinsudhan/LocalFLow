import { useEffect, useState } from "react";

import { Button, Card, PageHeader } from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api } from "../services/bridge";
import { useAppStore } from "../stores/appStore";

const CREDITS = [
  ["faster-whisper + CTranslate2", "Speech recognition", "MIT"],
  ["Silero VAD", "Voice activity detection", "MIT"],
  ["Ollama", "Local language models", "MIT"],
  ["Tauri", "Desktop shell", "MIT / Apache-2.0"],
  ["React", "Interface", "MIT"],
];

export function AboutPage() {
  const [version, setVersion] = useState("");
  const [paths, setPaths] = useState<{ data_dir: string; logs_dir: string } | null>(null);
  const hardware = useAppStore((s) => s.hardware);

  useEffect(() => {
    void api.diagnostics().then((info) => setVersion(info.version)).catch(() => undefined);
    void api.paths().then(setPaths).catch(() => undefined);
  }, []);

  return (
    <div>
      <PageHeader title="About" />

      <Card className="mb-6 p-7">
        <div className="flex items-start gap-5">
          <div className="nm-raised flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              {[
                { x: 3.2, h: 6 },
                { x: 7.6, h: 11 },
                { x: 12, h: 17 },
                { x: 16.4, h: 12 },
                { x: 20.8, h: 7 },
              ].map((bar, index) => (
                <rect
                  key={index}
                  x={bar.x - 1.1}
                  y={12 - bar.h / 2}
                  width="2.2"
                  height={bar.h}
                  rx="1.1"
                  fill="rgb(var(--accent))"
                  opacity={0.55 + index * 0.11}
                />
              ))}
            </svg>
          </div>
          <div className="min-w-0">
            <h2 className="text-[18px] font-semibold tracking-tight text-ink">LocalFlow</h2>
            <p className="mt-0.5 text-2xs text-faint">Version {version || "-"}</p>
            <p className="mt-3 max-w-xl text-[13px] leading-relaxed text-muted">
              Voice as a first-class input method for Windows. Hold a key anywhere, speak, and
              polished text appears at your cursor. Speech recognition and AI cleanup run entirely
              on your own machine - there is no account, no server, and nothing to opt out of.
            </p>
            {hardware && (
              <p className="mt-3 text-2xs text-faint">
                Running on {hardware.gpu_name || `${hardware.cpu_count} CPU threads`}
                {hardware.cuda_available && hardware.vram_total_mb
                  ? ` · ${(hardware.vram_total_mb / 1024).toFixed(0)} GB VRAM`
                  : ""}
              </p>
            )}
          </div>
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card className="p-5">
          <h3 className="mb-3 text-[13px] font-semibold text-ink">Built on</h3>
          <ul className="space-y-2.5">
            {CREDITS.map(([name, role, licence]) => (
              <li key={name} className="flex items-baseline justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-2xs font-medium text-ink">{name}</p>
                  <p className="text-2xs text-faint">{role}</p>
                </div>
                <span className="shrink-0 text-2xs text-faint">{licence}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5">
          <h3 className="mb-3 text-[13px] font-semibold text-ink">On this machine</h3>
          <div className="space-y-3">
            <div>
              <p className="text-2xs text-faint">Data folder</p>
              <p className="mt-0.5 break-all font-mono text-2xs text-muted">
                {paths?.data_dir ?? "-"}
              </p>
            </div>
            <div className="flex gap-2">
              <Button
                onClick={() => paths && void api.openPath(paths.data_dir)}
                icon={<Icon name="folder" />}
              >
                Open data folder
              </Button>
              <Button
                onClick={() => paths && void api.openPath(paths.logs_dir)}
                icon={<Icon name="folder" />}
              >
                Open logs
              </Button>
            </div>
            <div className="border-t border-line/60 pt-3">
              <p className="hint">
                LocalFlow keeps running in the system tray when you close this window. Quit from the
                tray icon to stop it completely.
              </p>
              <Button className="mt-2.5" variant="danger" onClick={() => void api.quit()}>
                Quit LocalFlow
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
