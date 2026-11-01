/**
 * Spoken replies for the console chat.
 *
 * The assistant answers in Markdown, which is written to be read, not
 * listened to: a fenced code block, a link's URL or a table's pipes would be
 * read aloud as punctuation. The reply is therefore reduced to sentences
 * first, then handed to the browser's own speech synthesiser.
 *
 * Deliberately no vendor: the console already pays for one ASR call per turn,
 * and voice *output* does not need to leave the machine. The browser uses the
 * platform's voices (speech-dispatcher on Linux, SAPI on Windows, AVSpeech on
 * macOS), so a spoken reply costs nothing and works offline. A vendor TTS
 * binding would be a separate, billable decision — `docs/decisions.md` records
 * why it was not taken here.
 */

const MAX_SPEECH_CHARS = 6_000;
const MAX_CHUNK_CHARS = 220;
const MIN_SENTENCE_CHARS = 30;
const START_TIMEOUT_MS = 3_000;

let generation = 0;
const waiters = new Set<() => void>();

export function canSpeak(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.speechSynthesis !== 'undefined' &&
    typeof SpeechSynthesisUtterance !== 'undefined'
  );
}

/** Stop a reply that is being spoken, and resolve the promise that awaited it. */
export function stopSpeaking(): void {
  generation += 1;
  if (canSpeak()) {
    window.speechSynthesis.cancel();
  }
  for (const wake of [...waiters]) {
    wake();
  }
  waiters.clear();
}

/**
 * Speak `markdown` and resolve when the last sentence has been heard.
 *
 * A cancellation (the Stop button, starting another turn, leaving the page)
 * resolves rather than rejects: the caller's next step is always the same, and
 * a rejected promise would turn a deliberate stop into an error message.
 */
export async function speak(
  markdown: string,
  options: { rate?: number } = {},
): Promise<void> {
  if (!canSpeak()) {
    throw new Error('this browser cannot speak replies');
  }
  const text = speakableText(markdown);
  if (!text) {
    return;
  }
  stopSpeaking();
  const token = generation;
  const lang = speechLanguage(text);
  const chunks = splitForSpeech(text);
  for (const chunk of chunks) {
    if (token !== generation) {
      return;
    }
    await speakChunk(chunk, lang, options.rate ?? 1);
  }
}

/** Strip the parts of a Markdown reply that exist only for the eye. */
export function speakableText(markdown: string): string {
  let text = markdown;
  // Fenced code is dropped entirely: reading a block of code aloud is noise,
  // and its punctuation would be spoken as words.
  text = text.replace(/```[\s\S]*?```/g, ' ');
  text = text.replace(/~~~[\s\S]*?~~~/g, ' ');
  // Images have no readable content; links keep their label and lose the URL.
  text = text.replace(/!\[[^\]]*\]\([^)]*\)/g, ' ');
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
  text = text.replace(/<[^>]*>/g, ' ');
  // Block markers at the start of a line.
  text = text.replace(/^\s{0,3}(?:#{1,6}|>)\s?/gm, '');
  text = text.replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '');
  // Table rules and cells: a spoken table is a list of words, so the pipes go.
  text = text.replace(/^\s*\|?[\s:|-]*\|[\s:|-]*$/gm, ' ');
  text = text.replace(/\|/g, ' ');
  // Bare URLs are unlistenable and an inline code span's backticks are silent.
  text = text.replace(/https?:\/\/\S+/g, ' ');
  text = text.replace(/(\*\*|__|\*|_|~~|`)/g, '');
  text = text.replace(/\s+/g, ' ').trim();
  return text.slice(0, MAX_SPEECH_CHARS);
}

/**
 * Pick the voice language from the reply's script.
 *
 * The owner writes in Chinese and English in the same conversation, so the
 * reply itself decides — not the console's UI language.
 */
export function speechLanguage(text: string): string {
  let cjk = 0;
  let latin = 0;
  let kana = 0;
  let hangul = 0;
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if ((code >= 0x3040 && code <= 0x30ff) || (code >= 0x31f0 && code <= 0x31ff)) {
      kana += 1;
    } else if (code >= 0xac00 && code <= 0xd7af) {
      hangul += 1;
    } else if (code >= 0x3400 && code <= 0x9fff) {
      cjk += 1;
    } else if ((code >= 0x41 && code <= 0x5a) || (code >= 0x61 && code <= 0x7a)) {
      latin += 1;
    }
  }
  if (kana > cjk + latin) {
    return 'ja-JP';
  }
  if (hangul > cjk + latin) {
    return 'ko-KR';
  }
  return cjk > latin ? 'zh-CN' : 'en-US';
}

function splitForSpeech(text: string): string[] {
  const chunks: string[] = [];
  let current = '';
  for (const char of text) {
    current += char;
    const sentenceEnd = '。！？!?；;'.includes(char);
    if (
      (sentenceEnd && current.length >= MIN_SENTENCE_CHARS) ||
      current.length >= MAX_CHUNK_CHARS
    ) {
      const chunk = current.trim();
      if (chunk) {
        chunks.push(chunk);
      }
      current = '';
    }
  }
  const tail = current.trim();
  if (tail) {
    chunks.push(tail);
  }
  return chunks;
}

function speakChunk(chunk: string, lang: string, rate: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const utterance = new SpeechSynthesisUtterance(chunk);
    utterance.lang = lang;
    utterance.rate = rate;
    const voice = pickVoice(lang);
    if (voice) {
      utterance.voice = voice;
    }
    let started = false;
    let done = false;
    let startTimer = 0;
    const finish = (error?: Error) => {
      if (done) {
        return;
      }
      done = true;
      window.clearTimeout(startTimer);
      waiters.delete(wake);
      if (error) {
        reject(error);
      } else {
        resolve();
      }
    };
    const wake = () => finish();
    waiters.add(wake);
    utterance.onstart = () => {
      started = true;
    };
    utterance.onend = () => finish();
    utterance.onerror = (event) => {
      if (event.error === 'canceled' || event.error === 'interrupted') {
        finish();
      } else {
        finish(new Error(event.error || 'speech synthesis failed'));
      }
    };
    // Some engines accept the utterance and never start it (no voice for the
    // language, audio device gone). Without this guard the turn would hang in
    // "Speaking…" with no way forward.
    startTimer = window.setTimeout(() => {
      if (!started) {
        window.speechSynthesis.cancel();
        finish(new Error('speech synthesis did not start'));
      }
    }, START_TIMEOUT_MS);
    window.speechSynthesis.speak(utterance);
  });
}

function pickVoice(lang: string): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices();
  if (voices.length === 0) {
    return null;
  }
  const prefix = lang.toLowerCase().split('-')[0];
  const matching = voices.filter((voice) =>
    voice.lang.toLowerCase().startsWith(prefix),
  );
  // A default-language voice reading a Chinese reply produces nothing useful;
  // no voice at all is better than a wrong one, and `utterance.lang` still
  // lets the platform choose.
  if (matching.length === 0) {
    return null;
  }
  return matching.find((voice) => voice.localService) ?? matching[0];
}
