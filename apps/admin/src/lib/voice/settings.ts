/**
 * How spoken replies are produced: which engine, which of its voices, how fast.
 *
 * The provider is part of the voice's identity, not decoration. A voice name is
 * not unique across engines, a browser voice exists only on the machine that
 * enumerated it, and a supplier voice is a server-side catalog entry. Storing
 * `{provider, voice}` from the start means adding a server-side TTS binding
 * later is a new provider value and a new catalog — it is not a migration of
 * everybody's saved choice, and a voice saved for one engine can never be sent
 * to another by accident.
 *
 * `browser` is the only provider implemented today. It costs nothing, needs no
 * credential and works offline with the platform's own voices, which is why it
 * is the default; a vendor provider will add its voices to the same structure.
 */
export const BROWSER_PROVIDER = 'browser';

export interface VoiceOutputSettings {
  /** Which engine speaks. `browser` now; a supplier id once one is bound. */
  provider: string;
  /**
   * Provider-scoped voice id. For the browser this is `voiceURI`, which is
   * stable on a machine but not across machines; empty means "let the engine
   * choose for the reply's language", which is also the right fallback when a
   * saved browser voice has disappeared.
   */
  voice: string;
  /** Synthesis rate, 0.5–2.0. */
  rate: number;
}

const STORAGE_KEY = 'winnie.admin.voice.output';

export const DEFAULT_VOICE_SETTINGS: VoiceOutputSettings = {
  provider: BROWSER_PROVIDER,
  voice: '',
  rate: 1,
};

export function readVoiceSettings(): VoiceOutputSettings {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return { ...DEFAULT_VOICE_SETTINGS };
    }
    return normalise(JSON.parse(raw) as Partial<VoiceOutputSettings>);
  } catch {
    return { ...DEFAULT_VOICE_SETTINGS };
  }
}

/** Merge one change, persist it, and return the stored value. */
export function saveVoiceSettings(
  patch: Partial<VoiceOutputSettings>,
): VoiceOutputSettings {
  const next = normalise({ ...readVoiceSettings(), ...patch });
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be unavailable; the choice still applies to this page.
  }
  return next;
}

function normalise(value: Partial<VoiceOutputSettings>): VoiceOutputSettings {
  const provider =
    typeof value.provider === 'string' && value.provider.trim()
      ? value.provider.trim()
      : BROWSER_PROVIDER;
  const voice = typeof value.voice === 'string' ? value.voice : '';
  const rate =
    typeof value.rate === 'number' && Number.isFinite(value.rate)
      ? Math.min(2, Math.max(0.5, value.rate))
      : DEFAULT_VOICE_SETTINGS.rate;
  return { provider, voice, rate };
}
