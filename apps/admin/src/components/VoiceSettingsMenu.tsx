import { useEffect, useState } from 'react';

import { tr } from '../i18n';
import {
  ensureVoices,
  speak,
  stopSpeaking,
  canSpeak,
  type SpeakOptions,
} from '../lib/voice/speech';
import type { VoiceOutputSettings } from '../lib/voice/settings';

/**
 * The chat's voice controls: which voice speaks and how fast.
 *
 * The list is the browser's own catalog, which is the whole point of the
 * browser provider: Edge on Linux exposes the Microsoft online voices, Firefox
 * speaks through speech-dispatcher. Both load that catalog asynchronously, so
 * the menu waits for `voiceschanged` instead of freezing the first (empty)
 * snapshot — see `ensureVoices`.
 *
 * A saved voice is a provider-scoped id. When it is absent from this machine's
 * catalog, the menu falls back to automatic and says nothing: a voice that
 * exists on the desktop but not on the laptop must not make the reply silent.
 */
export function VoiceSettingsMenu({
  settings,
  onChange,
}: {
  settings: VoiceOutputSettings;
  onChange: (patch: Partial<VoiceOutputSettings>) => void;
}) {
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [loading, setLoading] = useState(canSpeak());
  const [error, setError] = useState<string | null>(null);
  const speechAvailable = canSpeak();

  useEffect(() => {
    if (!speechAvailable) {
      return;
    }
    let alive = true;
    const load = () => {
      void ensureVoices().then((catalog) => {
        if (!alive) {
          return;
        }
        setVoices(catalog);
        setLoading(false);
      });
    };
    load();
    const onChanged = () => load();
    window.speechSynthesis.addEventListener('voiceschanged', onChanged);
    return () => {
      alive = false;
      window.speechSynthesis.removeEventListener('voiceschanged', onChanged);
    };
  }, [speechAvailable]);

  // A voice stored on another machine is not in this catalog. Clear it once the
  // list is known, so the select and the spoken reply agree on "automatic".
  useEffect(() => {
    if (loading || voices.length === 0 || !settings.voice) {
      return;
    }
    if (!voices.some((voice) => voice.voiceURI === settings.voice)) {
      onChange({ voice: '' });
    }
  }, [voices, loading, settings.voice, onChange]);

  const { groups, truncated } = groupVoices(voices, settings.voice);
  const selected = voices.find((voice) => voice.voiceURI === settings.voice);

  const preview = () => {
    setError(null);
    const chinese =
      selected?.lang.toLowerCase().startsWith('zh') ||
      (!selected && document.documentElement.lang.toLowerCase().startsWith('zh'));
    const sample = chinese
      ? '你好，我是温妮。今天天气不错，适合出去走走。'
      : 'Hello, this is Winnie. The weather is nice today.';
    const options: SpeakOptions = {
      voiceURI: settings.voice || undefined,
      rate: settings.rate,
    };
    void speak(sample, options).catch(() => {
      setError(tr('Could not play the preview.'));
    });
  };

  return (
    <div className="dropdown">
      <button
        type="button"
        className="btn btn-sm btn-outline-secondary"
        data-bs-toggle="dropdown"
        data-bs-auto-close="outside"
        aria-expanded="false"
        title={tr('Voice settings')}
        aria-label={tr('Voice settings')}
      >
        <i className="bi bi-volume-up" aria-hidden="true" />
      </button>
      <div
        className="dropdown-menu dropdown-menu-end p-3 winnie-voice-settings"
        style={{ width: '22rem' }}
      >
        <div className="mb-2">
          <label className="form-label small mb-1" htmlFor="voice-voice">
            {tr('Voice')}
          </label>
          <select
            id="voice-voice"
            className="form-select form-select-sm"
            value={settings.voice}
            disabled={!speechAvailable || loading}
            onChange={(event) => onChange({ voice: event.target.value })}
          >
            <option value="">{tr('Automatic (by language)')}</option>
            {groups.map((group) => (
              <optgroup key={group.code} label={group.label}>
                {group.voices.map((voice) => (
                  <option key={voice.voiceURI} value={voice.voiceURI}>
                    {shortName(voice)}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          {loading ? (
            <div className="form-text">{tr('Loading voices…')}</div>
          ) : voices.length === 0 ? (
            <div className="form-text">
              {tr('No voice list is available; the system default will be used.')}
            </div>
          ) : truncated ? (
            <div className="form-text">
              {tr('Showing a selection of the browser’s voices.')}
            </div>
          ) : null}
        </div>

        <div className="mb-2">
          <label className="form-label small mb-1" htmlFor="voice-rate">
            {tr('Rate')}
          </label>
          <select
            id="voice-rate"
            className="form-select form-select-sm"
            value={String(settings.rate)}
            disabled={!speechAvailable}
            onChange={(event) => onChange({ rate: Number(event.target.value) })}
          >
            {RATES.map((rate) => (
              <option key={rate} value={String(rate)}>
                {rate}×
              </option>
            ))}
          </select>
        </div>

        {!speechAvailable ? (
          <p className="form-text mb-0">{tr('This browser cannot play speech.')}</p>
        ) : (
          <div className="d-flex align-items-center gap-2">
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={preview}
            >
              <i className="bi bi-play-fill me-1" aria-hidden="true" />
              {tr('Preview')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-outline-secondary"
              onClick={() => stopSpeaking()}
            >
              {tr('Stop')}
            </button>
          </div>
        )}
        {error ? (
          <p className="small text-danger mt-2 mb-0" role="alert">
            {error}
          </p>
        ) : null}
        <p className="form-text mt-2 mb-0">
          {tr('Using the browser’s own voices. A connected speech provider will list its voices here.')}
        </p>
      </div>
    </div>
  );
}

const RATES = [0.8, 1, 1.2, 1.5] as const;

interface VoiceGroup {
  code: string;
  label: string;
  voices: SpeechSynthesisVoice[];
}

/**
 * How many voices one primary language may contribute.
 *
 * Chromium/Edge exposes a curated catalog (321 voices here), but Firefox's
 * speech-dispatcher backend expands every espeak language into all of its
 * variants: 14,805 voices, 945 of them English. A select with 14,805 options is
 * unusable and freezes the page, so the picker keeps a real choice for the two
 * languages this console speaks and one representative voice for every other
 * language. Automatic selection is unaffected: a reply in any language still
 * gets a voice from `utterance.lang`, whether or not it is pinnable here.
 */
const PRIMARY_LANGUAGES = new Set(['en', 'zh']);
const MAX_PRIMARY_VOICES = 50;

function groupVoices(
  voices: SpeechSynthesisVoice[],
  savedVoice: string,
): { groups: VoiceGroup[]; truncated: boolean } {
  const buckets = new Map<string, SpeechSynthesisVoice[]>();
  for (const voice of voices) {
    const code = voice.lang.toLowerCase().split('-')[0] || 'other';
    const bucket = buckets.get(code);
    if (bucket) {
      bucket.push(voice);
    } else {
      buckets.set(code, [voice]);
    }
  }
  let truncated = false;
  const groups: VoiceGroup[] = [];
  for (const [code, list] of buckets) {
    const sorted = [...list].sort((left, right) =>
      left.name.localeCompare(right.name),
    );
    let kept: SpeechSynthesisVoice[];
    if (PRIMARY_LANGUAGES.has(code)) {
      kept = sorted.slice(0, MAX_PRIMARY_VOICES);
      if (sorted.length > MAX_PRIMARY_VOICES) {
        truncated = true;
      }
    } else {
      // One voice keeps every language reachable without a hundred variants of
      // the same synthesizer; the engine's own default is the least surprising
      // representative.
      const preferred = sorted.find((voice) => voice.default) ?? sorted[0];
      kept = preferred ? [preferred] : [];
    }
    // A voice saved on this machine must stay selectable even when it fell
    // outside the cap, or the menu would show a value it does not contain.
    const chosen = list.find((voice) => voice.voiceURI === savedVoice);
    if (chosen && !kept.includes(chosen)) {
      kept = [...kept, chosen];
      truncated = truncated || !PRIMARY_LANGUAGES.has(code);
    }
    if (kept.length > 0) {
      groups.push({
        code,
        label: languageLabel(code),
        voices: kept,
      });
    }
  }
  groups.sort((left, right) => left.label.localeCompare(right.label));
  return { groups, truncated };
}

/**
 * The language's name in the console's language.
 *
 * `Intl.DisplayNames` is the platform's own list, so the picker does not ship a
 * translation table for sixty languages. A tag it does not know (a regional
 * variant) falls back to the raw code rather than hiding the group.
 */
function languageLabel(code: string): string {
  try {
    const display = new Intl.DisplayNames(
      [document.documentElement.lang || 'en'],
      { type: 'language' },
    );
    return display.of(code) ?? code;
  } catch {
    return code;
  }
}

/**
 * Drop the language suffix from a voice name: the group already says it.
 * Edge names read "Microsoft 晓晓 Online (Natural) - Chinese (Mandarin, …)";
 * the part after " - " is redundant inside a Chinese group.
 */
function shortName(voice: SpeechSynthesisVoice): string {
  const cut = voice.name.split(' - ')[0].trim();
  return cut || voice.name;
}
