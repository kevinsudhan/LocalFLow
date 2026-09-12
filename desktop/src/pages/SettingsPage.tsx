import { useEffect, useRef, useState } from "react";

import type { Route } from "../components/layout/Shell";
import {
  Button,
  Card,
  ConfirmButton,
  Meter,
  PageHeader,
  Row,
  Section,
  Segmented,
  Select,
  Slider,
  TextInput,
  Toggle,
} from "../components/ui";
import { Icon } from "../components/ui/Icon";
import { api, BridgeError, onBackend } from "../services/bridge";
import { useAppStore } from "../stores/appStore";
import type { Aggressiveness, HotkeyMode, HudPosition, InjectionMethod, Theme } from "../types";

const SECTIONS = [
  "General",
  "Audio",
  "Speech",
  "AI",
  "Formatting",
  "Keyboard",
  "Insertion",
  "Appearance",
  "History",
  "Advanced",
] as const;
type SectionName = (typeof SECTIONS)[number];

const LANGUAGES = [
  ["auto", "Detect automatically"],
  ["en", "English"],
  ["ta", "Tamil"],
  ["hi", "Hindi"],
  ["ml", "Malayalam"],
  ["te", "Telugu"],
  ["kn", "Kannada"],
  ["mr", "Marathi"],
  ["bn", "Bengali"],
  ["fr", "French"],
  ["de", "German"],
  ["es", "Spanish"],
  ["pt", "Portuguese"],
  ["ja", "Japanese"],
  ["zh", "Chinese"],
  ["ar", "Arabic"],
];

export function SettingsPage({ onNavigate }: { onNavigate(route: Route): void }) {
  const [section, setSection] = useState<SectionName>("General");
  const { settings, patch, styles, autostart, setAutostart, toast } = useAppStore();

  if (!settings) return null;

  return (
    <div>
      <PageHeader
        title="Settings"
        description="Everything here applies immediately and is stored on this machine."
      />

      <div className="grid grid-cols-[168px_1fr] gap-6">
        <nav className="sticky top-0 self-start" aria-label="Settings sections">
          <ul className="space-y-0.5">
            {SECTIONS.map((name) => (
              <li key={name}>
                <button
                  type="button"
                  onClick={() => setSection(name)}
                  aria-current={section === name ? "true" : undefined}
                  className={`w-full rounded-lg px-3 py-1.5 text-left text-[13px] transition-colors
                              ${
                                section === name
                                  ? "nm-inset font-medium text-ink"
                                  : "text-muted hover:text-ink"
                              }`}
                >
                  {name}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0">
          {section === "General" && (
            <Section title="General">
              <Card>
                <Row
                  label="Start LocalFlow with Windows"
                  hint="Launches minimised to the system tray so the shortcut works right after you log in."
                >
                  <Toggle checked={autostart} onChange={(next) => void setAutostart(next)} />
                </Row>
                <Row label="Start minimised" hint="Open straight to the tray instead of this window.">
                  <Toggle
                    checked={settings.advanced.start_minimized}
                    onChange={(next) => void patch({ advanced: { start_minimized: next } })}
                  />
                </Row>
                <Row
                  label="Default writing style"
                  hint="Used when an application has no style of its own."
                >
                  <Select
                    value={settings.processing.default_style}
                    onChange={(e) => void patch({ processing: { default_style: e.target.value } })}
                  >
                    {styles.map((style) => (
                      <option key={style.key} value={style.key}>
                        {style.label}
                      </option>
                    ))}
                  </Select>
                </Row>
                <Row
                  label="Show the welcome tour again"
                  hint="Re-runs the microphone, model and shortcut checks."
                >
                  <Button
                    onClick={() => void patch({ advanced: { show_onboarding: true } })}
                  >
                    Run setup
                  </Button>
                </Row>
              </Card>
            </Section>
          )}

          {section === "Audio" && <AudioSection />}

          {section === "Speech" && (
            <Section title="Speech recognition">
              <Card>
                <Row label="Model" hint="Choose and download models on the Models page.">
                  <Button onClick={() => onNavigate("models")} icon={<Icon name="chip" />}>
                    {settings.asr.model}
                  </Button>
                </Row>
                <Row
                  label="Language"
                  hint="Automatic detection handles mixed-language speech. Pick a language to skip detection and save a little time."
                >
                  <Select
                    value={settings.asr.language}
                    onChange={(e) => void patch({ asr: { language: e.target.value } })}
                  >
                    {LANGUAGES.map(([code, label]) => (
                      <option key={code} value={code}>
                        {label}
                      </option>
                    ))}
                  </Select>
                </Row>
                <Row
                  label="Live transcript while speaking"
                  hint="Shows partial results in the HUD. Costs a little GPU time; the final result is always transcribed fresh."
                >
                  <Toggle
                    checked={settings.asr.streaming_partials}
                    onChange={(next) => void patch({ asr: { streaming_partials: next } })}
                  />
                </Row>
                <Row label="Partial update interval" hint="How often the live transcript refreshes.">
                  <Slider
                    min={300}
                    max={2000}
                    step={50}
                    value={settings.asr.partial_interval_ms}
                    onChange={(v) => void patch({ asr: { partial_interval_ms: v } })}
                    format={(v) => `${v} ms`}
                    label="Partial update interval"
                  />
                </Row>
                <Row
                  label="Accuracy versus speed"
                  hint="Beam size. Higher explores more alternatives per word."
                >
                  <Slider
                    min={1}
                    max={8}
                    value={settings.asr.beam_size}
                    onChange={(v) => void patch({ asr: { beam_size: v } })}
                    label="Beam size"
                  />
                </Row>
                <Row
                  label="Bias towards your vocabulary"
                  hint="Feeds your saved terms to Whisper so it is more likely to hear them correctly."
                >
                  <Toggle
                    checked={settings.asr.use_vocabulary_prompt}
                    onChange={(next) => void patch({ asr: { use_vocabulary_prompt: next } })}
                  />
                </Row>
                <Row label="Keep the model warm" hint="Avoids a slow first dictation after idling.">
                  <Toggle
                    checked={settings.asr.keep_warm}
                    onChange={(next) => void patch({ asr: { keep_warm: next } })}
                  />
                </Row>
              </Card>

              <div className="mt-4">
                <Card>
                  <div className="px-5 pb-1 pt-4">
                    <h3 className="text-[13px] font-semibold text-ink">Voice activity detection</h3>
                    <p className="hint mt-0.5">
                      Trims silence around your speech without clipping the first or last syllable.
                    </p>
                  </div>
                  <Row label="Enabled" hint="Turn off only if speech is being cut off.">
                    <Toggle
                      checked={settings.vad.enabled}
                      onChange={(next) => void patch({ vad: { enabled: next } })}
                    />
                  </Row>
                  <Row label="Sensitivity" hint="Lower detects quieter speech but may keep noise.">
                    <Slider
                      min={0.2}
                      max={0.8}
                      step={0.05}
                      value={settings.vad.threshold}
                      onChange={(v) => void patch({ vad: { threshold: v } })}
                      format={(v) => v.toFixed(2)}
                      label="VAD sensitivity"
                    />
                  </Row>
                  <Row label="Padding" hint="Extra audio kept either side of detected speech.">
                    <Slider
                      min={0}
                      max={600}
                      step={25}
                      value={settings.vad.speech_pad_ms}
                      onChange={(v) => void patch({ vad: { speech_pad_ms: v } })}
                      format={(v) => `${v} ms`}
                      label="Speech padding"
                    />
                  </Row>
                  <Row
                    label="Natural pause tolerance"
                    hint="How long you can pause mid-sentence before it counts as the end."
                  >
                    <Slider
                      min={200}
                      max={1500}
                      step={50}
                      value={settings.vad.min_silence_ms}
                      onChange={(v) => void patch({ vad: { min_silence_ms: v } })}
                      format={(v) => `${v} ms`}
                      label="Pause tolerance"
                    />
                  </Row>
                </Card>
              </div>
            </Section>
          )}

          {section === "AI" && (
            <Section
              title="AI cleanup"
              description="A local language model tidies grammar and resolves spoken corrections. It never answers what you dictate."
            >
              <Card>
                <Row
                  label="Enable AI cleanup"
                  hint="With this off, LocalFlow still removes fillers, fixes punctuation and resolves corrections deterministically."
                >
                  <Toggle
                    checked={settings.llm.enabled}
                    onChange={(next) => void patch({ llm: { enabled: next } })}
                  />
                </Row>
                <Row label="Model" hint="Download and switch models on the Models page.">
                  <Button onClick={() => onNavigate("models")} icon={<Icon name="chip" />}>
                    {settings.llm.model || "Not selected"}
                  </Button>
                </Row>
                <Row
                  label="Always use the model"
                  hint="By default LocalFlow only calls it when the deterministic pipeline is not enough, which keeps most dictations instant."
                >
                  <Toggle
                    checked={settings.llm.always_use}
                    onChange={(next) => void patch({ llm: { always_use: next } })}
                  />
                </Row>
                <Row
                  label="Timeout"
                  hint="If the model takes longer, LocalFlow inserts the cleaned transcript instead of making you wait."
                >
                  <Slider
                    min={3}
                    max={60}
                    value={settings.llm.timeout_s}
                    onChange={(v) => void patch({ llm: { timeout_s: v } })}
                    format={(v) => `${v}s`}
                    label="LLM timeout"
                  />
                </Row>
                <Row label="Keep loaded for" hint="How long Ollama holds the model in memory.">
                  <Select
                    value={settings.llm.keep_alive}
                    onChange={(e) => void patch({ llm: { keep_alive: e.target.value } })}
                  >
                    <option value="0">Unload immediately</option>
                    <option value="5m">5 minutes</option>
                    <option value="15m">15 minutes</option>
                    <option value="1h">1 hour</option>
                    <option value="-1">Until I quit</option>
                  </Select>
                </Row>
                <Row label="Ollama address" hint="Loopback only. LocalFlow will not use a remote host.">
                  <TextInput
                    className="w-64"
                    value={settings.llm.base_url}
                    spellCheck={false}
                    onChange={(e) => void patch({ llm: { base_url: e.target.value } })}
                  />
                </Row>
              </Card>
            </Section>
          )}

          {section === "Formatting" && (
            <Section title="Text processing">
              <Card>
                <Row
                  label="Remove filler words"
                  hint="Removes hesitations always, and discourse fillers only where they are unambiguous."
                >
                  <Toggle
                    checked={settings.processing.remove_fillers}
                    onChange={(next) => void patch({ processing: { remove_fillers: next } })}
                  />
                </Row>
                <Row
                  label="How aggressively"
                  hint="Conservative keeps anything that might carry meaning."
                >
                  <Segmented<Aggressiveness>
                    ariaLabel="Filler aggressiveness"
                    value={settings.processing.filler_aggressiveness}
                    onChange={(value) =>
                      void patch({ processing: { filler_aggressiveness: value } })
                    }
                    options={[
                      { value: "conservative", label: "Light" },
                      { value: "balanced", label: "Balanced" },
                      { value: "aggressive", label: "Strong" },
                    ]}
                  />
                </Row>
                <Row
                  label="Resolve spoken corrections"
                  hint={'"Send it Monday, actually Tuesday" becomes "Send it Tuesday."'}
                >
                  <Toggle
                    checked={settings.processing.resolve_corrections}
                    onChange={(next) => void patch({ processing: { resolve_corrections: next } })}
                  />
                </Row>
                <Row label="Punctuation and capitalisation" hint="Finishes sentences and fixes casing.">
                  <Toggle
                    checked={settings.processing.auto_punctuation}
                    onChange={(next) => void patch({ processing: { auto_punctuation: next } })}
                  />
                </Row>
                <Row label="Paragraphs" hint="Inserts a break where you paused between sentences.">
                  <Toggle
                    checked={settings.processing.auto_paragraphs}
                    onChange={(next) => void patch({ processing: { auto_paragraphs: next } })}
                  />
                </Row>
                <Row
                  label="Lists"
                  hint={'Formats "one … two … three …" as a numbered list. Ordinary sentences containing numbers are left alone.'}
                >
                  <Toggle
                    checked={settings.processing.auto_lists}
                    onChange={(next) => void patch({ processing: { auto_lists: next } })}
                  />
                </Row>
                <Row
                  label="Use surrounding text"
                  hint="Reads the text around your cursor to decide capitalisation and spacing. Never used to add content."
                >
                  <Toggle
                    checked={settings.processing.use_context}
                    onChange={(next) => void patch({ processing: { use_context: next } })}
                  />
                </Row>
                <Row label="Voice commands" hint={'Phrases like "undo that" or "make that formal".'}>
                  <Toggle
                    checked={settings.processing.commands_enabled}
                    onChange={(next) => void patch({ processing: { commands_enabled: next } })}
                  />
                </Row>
                <Row label="Snippets" hint="Expand saved text from a spoken trigger phrase.">
                  <Toggle
                    checked={settings.processing.snippets_enabled}
                    onChange={(next) => void patch({ processing: { snippets_enabled: next } })}
                  />
                </Row>
                <Row
                  label="Learn from my edits"
                  hint="When you correct the same word repeatedly, LocalFlow remembers it. Stored locally and never sent anywhere."
                >
                  <Toggle
                    checked={settings.processing.learning_enabled}
                    onChange={(next) => void patch({ processing: { learning_enabled: next } })}
                  />
                </Row>
              </Card>
            </Section>
          )}

          {section === "Keyboard" && <KeyboardSection />}

          {section === "Insertion" && (
            <Section
              title="Text insertion"
              description="How the finished text reaches the application you are typing into."
            >
              <Card>
                <Row
                  label="Method"
                  hint="Automatic types short text and pastes longer text, which also gives you a single undo step."
                >
                  <Segmented<InjectionMethod>
                    ariaLabel="Insertion method"
                    value={settings.injection.method}
                    onChange={(value) => void patch({ injection: { method: value } })}
                    options={[
                      { value: "auto", label: "Automatic" },
                      { value: "unicode", label: "Type" },
                      { value: "clipboard", label: "Paste" },
                    ]}
                  />
                </Row>
                <Row label="Paste above" hint="Length at which automatic switches from typing to pasting.">
                  <Slider
                    min={20}
                    max={500}
                    step={10}
                    value={settings.injection.clipboard_threshold}
                    onChange={(v) => void patch({ injection: { clipboard_threshold: v } })}
                    format={(v) => `${v} chars`}
                    label="Clipboard threshold"
                  />
                </Row>
                <Row
                  label="Restore my clipboard"
                  hint="Puts back whatever you had copied. If your clipboard holds something LocalFlow cannot restore, such as an image, it types instead of pasting."
                >
                  <Toggle
                    checked={settings.injection.restore_clipboard}
                    onChange={(next) => void patch({ injection: { restore_clipboard: next } })}
                  />
                </Row>
                <Row
                  label="Replace selected text"
                  hint="Dictating with text selected overwrites it instead of appending."
                >
                  <Toggle
                    checked={settings.injection.replace_selection}
                    onChange={(next) => void patch({ injection: { replace_selection: next } })}
                  />
                </Row>
                <Row label="Typing speed" hint="Delay between synthetic keystrokes. Raise it if characters go missing.">
                  <Slider
                    min={0}
                    max={12}
                    value={settings.injection.key_delay_ms}
                    onChange={(v) => void patch({ injection: { key_delay_ms: v } })}
                    format={(v) => `${v} ms`}
                    label="Key delay"
                  />
                </Row>
              </Card>
            </Section>
          )}

          {section === "Appearance" && (
            <Section title="Appearance">
              <Card>
                <Row label="Theme">
                  <Segmented<Theme>
                    ariaLabel="Theme"
                    value={settings.appearance.theme}
                    onChange={(value) => void patch({ appearance: { theme: value } })}
                    options={[
                      { value: "system", label: "System" },
                      { value: "dark", label: "Dark" },
                      { value: "light", label: "Light" },
                    ]}
                  />
                </Row>
                <Row label="Where the HUD appears" hint="Near the cursor follows the text field you are in.">
                  <Segmented<HudPosition>
                    ariaLabel="HUD position"
                    value={settings.appearance.hud_position}
                    onChange={(value) => void patch({ appearance: { hud_position: value } })}
                    options={[
                      { value: "bottom_center", label: "Bottom" },
                      { value: "near_cursor", label: "Near cursor" },
                      { value: "top_center", label: "Top" },
                    ]}
                  />
                </Row>
                <Row label="Distance from the edge">
                  <Slider
                    min={16}
                    max={300}
                    step={4}
                    value={settings.appearance.hud_offset}
                    onChange={(v) => void patch({ appearance: { hud_offset: v } })}
                    format={(v) => `${v} px`}
                    label="HUD offset"
                  />
                </Row>
                <Row label="Show the live transcript" hint="Widens the HUD pill while you speak.">
                  <Toggle
                    checked={settings.appearance.show_partials}
                    onChange={(next) => void patch({ appearance: { show_partials: next } })}
                  />
                </Row>
                <Row label="Reduce motion" hint="Removes animation throughout the app and the HUD.">
                  <Toggle
                    checked={settings.appearance.reduced_motion}
                    onChange={(next) => void patch({ appearance: { reduced_motion: next } })}
                  />
                </Row>
                <Row label="High contrast" hint="Flattens the soft shading and raises text contrast.">
                  <Toggle
                    checked={settings.appearance.high_contrast}
                    onChange={(next) => void patch({ appearance: { high_contrast: next } })}
                  />
                </Row>
              </Card>
            </Section>
          )}

          {section === "History" && (
            <Section title="History">
              <Card>
                <Row label="Keep a history of dictations" hint="Stored in a local SQLite database.">
                  <Toggle
                    checked={settings.privacy.store_history}
                    onChange={(next) => void patch({ privacy: { store_history: next } })}
                  />
                </Row>
                <Row label="Delete entries older than">
                  <Select
                    value={String(settings.privacy.history_retention_days)}
                    onChange={(e) =>
                      void patch({ privacy: { history_retention_days: Number(e.target.value) } })
                    }
                  >
                    <option value="0">Never</option>
                    <option value="7">7 days</option>
                    <option value="30">30 days</option>
                    <option value="90">90 days</option>
                    <option value="365">1 year</option>
                  </Select>
                </Row>
                <Row
                  label="Keep the audio too"
                  hint="Off by default. Saves a WAV file for every dictation so you can replay it. Uses disk and keeps a recording of your voice on this PC."
                >
                  <Toggle
                    checked={settings.privacy.store_audio}
                    onChange={(next) => void patch({ privacy: { store_audio: next } })}
                  />
                </Row>
                <Row label="Delete recordings older than">
                  <Select
                    value={String(settings.privacy.audio_retention_days)}
                    disabled={!settings.privacy.store_audio}
                    onChange={(e) =>
                      void patch({ privacy: { audio_retention_days: Number(e.target.value) } })
                    }
                  >
                    <option value="1">1 day</option>
                    <option value="7">7 days</option>
                    <option value="30">30 days</option>
                    <option value="0">Never</option>
                  </Select>
                </Row>
              </Card>
            </Section>
          )}

          {section === "Advanced" && (
            <Section title="Advanced">
              <Card>
                <Row
                  label="Developer mode"
                  hint="Adds the Diagnostics page with per-stage latency and the last transcript."
                >
                  <Toggle
                    checked={settings.advanced.developer_mode}
                    onChange={(next) => void patch({ advanced: { developer_mode: next } })}
                  />
                </Row>
                <Row label="Log level">
                  <Select
                    value={settings.advanced.log_level}
                    onChange={(e) => void patch({ advanced: { log_level: e.target.value } })}
                  >
                    {["DEBUG", "INFO", "WARNING", "ERROR"].map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </Select>
                </Row>
                <Row label="Maximum dictation length" hint="Recording stops automatically at this point.">
                  <Slider
                    min={30}
                    max={900}
                    step={30}
                    value={settings.audio.max_duration_s}
                    onChange={(v) => void patch({ audio: { max_duration_s: v } })}
                    format={(v) => `${Math.round(v / 60)} min`}
                    label="Maximum duration"
                  />
                </Row>
                <Row label="Open the log folder">
                  <Button
                    onClick={() => void api.paths().then((p) => api.openPath(p.logs_dir))}
                    icon={<Icon name="folder" />}
                  >
                    Open
                  </Button>
                </Row>
                <Row label="Back up your settings, vocabulary and snippets">
                  <div className="flex gap-2">
                    <Button
                      onClick={async () => {
                        const data = await api.exportAll();
                        downloadJson("localflow-backup.json", data);
                        toast("success", "Backup saved to your downloads");
                      }}
                      icon={<Icon name="download" />}
                    >
                      Export
                    </Button>
                    <ImportButton />
                  </div>
                </Row>
                <Row
                  label="Reset every setting"
                  hint="Vocabulary, snippets and history are kept."
                >
                  <ConfirmButton
                    onConfirm={() =>
                      void api
                        .resetSettings()
                        .then(() => useAppStore.getState().refreshSettings())
                        .then(() => toast("success", "Settings reset"))
                    }
                  >
                    Reset settings
                  </ConfirmButton>
                </Row>
              </Card>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ audio */

function AudioSection() {
  const settings = useAppStore((s) => s.settings)!;
  const patch = useAppStore((s) => s.patch);
  const devices = useAppStore((s) => s.devices);
  const deviceError = useAppStore((s) => s.deviceError);
  const deviceTotal = useAppStore((s) => s.deviceTotal);
  const showAllDevices = useAppStore((s) => s.showAllDevices);
  const refreshDevices = useAppStore((s) => s.refreshDevices);
  const rescanDevices = useAppStore((s) => s.rescanDevices);
  const activeDevice = useAppStore((s) => s.activeDevice);
  const [level, setLevel] = useState(0);

  useEffect(() => {
    const subscription = onBackend<{ level: number }>("audio.level", (p) => setLevel(p.level));
    return () => void subscription.then((off) => off());
  }, []);

  return (
    <Section
      title="Audio"
      description="LocalFlow keeps a short rolling buffer so the beginning of your first word is never clipped."
      actions={
        <Button onClick={() => void rescanDevices()} icon={<Icon name="refresh" />}>
          Rescan
        </Button>
      }
    >
      <Card>
        <Row
          label="Microphone"
          hint={
            deviceError ||
            (activeDevice.name
              ? `Recording from ${activeDevice.name}${activeDevice.hostApi ? ` via ${activeDevice.hostApi}` : ""}.`
              : "The device LocalFlow records from.")
          }
        >
          <Select
            className="w-72"
            value={settings.audio.device_id === null ? "" : String(settings.audio.device_id)}
            onChange={(e) => {
              const value = e.target.value;
              const device = devices.find((d) => String(d.id) === value);
              void patch({
                audio: {
                  device_id: value === "" ? null : Number(value),
                  device_name: device?.name ?? "",
                },
                // Re-read which device the stream actually landed on: a device
                // that will not open falls back to another one, and saying so
                // beats a meter that sits at zero for no visible reason.
              }).finally(() => void refreshDevices());
            }}
          >
            <option value="">System default</option>
            {devices.map((device) => (
              <option key={device.id} value={device.id}>
                {device.name}
                {showAllDevices ? ` - ${device.host_api}` : ""}
                {device.is_default ? " (default)" : ""}
              </option>
            ))}
          </Select>
        </Row>
        <Row
          label="Show every input"
          hint={
            showAllDevices
              ? `Listing all ${deviceTotal} endpoints, including duplicates and exclusive-mode drivers that often fail to open.`
              : `Windows lists each microphone once per audio driver. LocalFlow shows ${devices.length} real ${devices.length === 1 ? "device" : "devices"} out of ${deviceTotal} endpoints.`
          }
        >
          <Toggle
            checked={showAllDevices}
            label="Show every input"
            onChange={(next) => void refreshDevices(next)}
          />
        </Row>
        <div className="row">
          <div className="min-w-0 flex-1">
            <p className="label">Input level</p>
            <p className="hint mt-1">Speak now - the bar should move.</p>
            <div className="mt-2.5 max-w-sm">
              <Meter value={Math.min(1, level * 7)} tone="positive" />
            </div>
          </div>
        </div>
        <Row label="Input gain" hint="Raise this only if your microphone is very quiet.">
          <Slider
            min={0.5}
            max={3}
            step={0.1}
            value={settings.audio.input_gain}
            onChange={(v) => void patch({ audio: { input_gain: v } })}
            format={(v) => `${v.toFixed(1)}x`}
            label="Input gain"
          />
        </Row>
        <Row
          label="Pre-roll buffer"
          hint="Audio kept from just before you pressed the shortcut, so the first syllable survives."
        >
          <Slider
            min={0}
            max={1200}
            step={50}
            value={settings.audio.pre_buffer_ms}
            onChange={(v) => void patch({ audio: { pre_buffer_ms: v } })}
            format={(v) => `${v} ms`}
            label="Pre-roll buffer"
          />
        </Row>
        <Row label="Tail buffer" hint="Recording continues briefly after you release, so the last syllable survives.">
          <Slider
            min={0}
            max={900}
            step={50}
            value={settings.audio.post_buffer_ms}
            onChange={(v) => void patch({ audio: { post_buffer_ms: v } })}
            format={(v) => `${v} ms`}
            label="Tail buffer"
          />
        </Row>
      </Card>
    </Section>
  );
}

/* --------------------------------------------------------------- keyboard */

function KeyboardSection() {
  const settings = useAppStore((s) => s.settings)!;
  const patch = useAppStore((s) => s.patch);
  const commands = useAppStore((s) => s.commands);
  const toast = useAppStore((s) => s.toast);

  return (
    <Section title="Keyboard">
      <Card>
        <Row
          label="Dictation shortcut"
          hint="Hold it anywhere in Windows. LocalFlow intercepts this exact combination and passes everything else through."
        >
          <HotkeyCapture
            value={settings.hotkeys.primary}
            onChange={(next) => void patch({ hotkeys: { primary: next } })}
            onError={(message) => toast("error", "That shortcut will not work", message)}
          />
        </Row>
        <Row
          label="Mode"
          hint="Hold to talk is the default. A quick tap locks recording on so you can let go."
        >
          <Segmented<HotkeyMode>
            ariaLabel="Hotkey mode"
            value={settings.hotkeys.mode}
            onChange={(value) => void patch({ hotkeys: { mode: value } })}
            options={[
              { value: "push_to_talk", label: "Hold" },
              { value: "toggle", label: "Toggle" },
              { value: "hands_free", label: "Hands-free" },
            ]}
          />
        </Row>
        <Row
          label="Stop after silence"
          hint="Hands-free mode only: how long a pause ends the dictation."
        >
          <Slider
            min={800}
            max={5000}
            step={100}
            value={settings.hotkeys.hands_free_silence_ms}
            onChange={(v) => void patch({ hotkeys: { hands_free_silence_ms: v } })}
            format={(v) => `${(v / 1000).toFixed(1)}s`}
            label="Hands-free silence"
          />
        </Row>
        <Row label="Undo shortcut" hint="Removes the text LocalFlow last inserted.">
          <HotkeyCapture
            value={settings.hotkeys.undo_hotkey}
            onChange={(next) => void patch({ hotkeys: { undo_hotkey: next } })}
            onError={(message) => toast("error", "That shortcut will not work", message)}
          />
        </Row>
        <Row label="Cancel key" hint="Discards the current dictation without inserting anything.">
          <kbd className="kbd">{settings.hotkeys.cancel_key}</kbd>
        </Row>
        <Row label="Shortcuts enabled" hint="Turn off to release the global shortcut entirely.">
          <Toggle
            checked={settings.hotkeys.enabled}
            onChange={(next) => void patch({ hotkeys: { enabled: next } })}
          />
        </Row>
      </Card>

      <div className="mt-4">
        <Card>
          <div className="px-5 pb-1 pt-4">
            <h3 className="text-[13px] font-semibold text-ink">Voice commands</h3>
            <p className="hint mt-0.5">
              Recognised only when the whole dictation is the command, so they cannot fire by
              accident mid-sentence.
            </p>
          </div>
          <ul className="px-5 pb-4 pt-2">
            {commands.map((command) => (
              <li key={command.phrase} className="flex items-baseline gap-3 py-1.5">
                <span className="chip shrink-0 font-mono">{command.phrase}</span>
                <span className="text-2xs text-muted">{command.description}</span>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </Section>
  );
}

function HotkeyCapture({
  value,
  onChange,
  onError,
}: {
  value: string;
  onChange(next: string): void;
  onError(message: string): void;
}) {
  const [capturing, setCapturing] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!capturing) return;
    const onKeyDown = async (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.key === "Escape") {
        setCapturing(false);
        return;
      }
      // Ignore a bare modifier: wait for the real key.
      if (["Control", "Shift", "Alt", "Meta"].includes(event.key)) return;

      const parts: string[] = [];
      if (event.ctrlKey) parts.push("Ctrl");
      if (event.shiftKey) parts.push("Shift");
      if (event.altKey) parts.push("Alt");
      if (event.metaKey) parts.push("Win");
      parts.push(normaliseKey(event.key, event.code));
      const candidate = parts.join("+");

      try {
        const accepted = await api.validateHotkey(candidate);
        onChange(accepted);
        setCapturing(false);
      } catch (error) {
        onError(error instanceof BridgeError ? error.message : String(error));
        setCapturing(false);
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [capturing, onChange, onError]);

  return (
    <button
      ref={ref}
      type="button"
      onClick={() => setCapturing((c) => !c)}
      onBlur={() => setCapturing(false)}
      className={`min-w-[150px] rounded-xl px-3.5 py-2 text-[13px] font-medium transition-all
                  ${capturing ? "nm-inset text-accent" : "nm-raised-sm text-ink"}`}
    >
      {capturing ? "Press a combination…" : value}
    </button>
  );
}

function normaliseKey(key: string, code: string): string {
  if (key === " " || code === "Space") return "Space";
  if (key.length === 1) return key.toUpperCase();
  if (/^F\d{1,2}$/.test(key)) return key;
  const map: Record<string, string> = {
    ArrowLeft: "Left",
    ArrowRight: "Right",
    ArrowUp: "Up",
    ArrowDown: "Down",
    Enter: "Enter",
    Tab: "Tab",
    Backspace: "Backspace",
    Delete: "Delete",
    Insert: "Insert",
    Home: "Home",
    End: "End",
    PageUp: "PageUp",
    PageDown: "PageDown",
    CapsLock: "CapsLock",
  };
  return map[key] ?? key;
}

/* ---------------------------------------------------------------- helpers */

function ImportButton() {
  const toast = useAppStore((s) => s.toast);
  const refreshSettings = useAppStore((s) => s.refreshSettings);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept="application/json"
        className="hidden"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          if (!file) return;
          try {
            const payload = JSON.parse(await file.text());
            const counts = await api.importAll(payload);
            await refreshSettings();
            toast(
              "success",
              "Backup restored",
              `${counts.vocabulary ?? 0} terms, ${counts.snippets ?? 0} snippets`,
            );
          } catch (error) {
            toast(
              "error",
              "Could not read that file",
              error instanceof Error ? error.message : String(error),
            );
          } finally {
            event.target.value = "";
          }
        }}
      />
      <Button onClick={() => inputRef.current?.click()}>Import</Button>
    </>
  );
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
