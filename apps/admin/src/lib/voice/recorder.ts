import { VoiceEndpointer, levelDbfs } from './endpoint';

/**
 * Records one spoken turn in the browser and hands back a 16 kHz mono WAV.
 *
 * The server's transcription endpoint accepts compressed formats too, but the
 * phone sends WAV and a vendor adapter is most predictable with the format that
 * is already exercised. MediaRecorder would give WebM/Opus, which is a decode
 * risk for a recording-file ASR API, so this path captures PCM directly and
 * encodes the container itself.
 *
 * The end of the turn is decided by the same rules as the phone
 * (`VoiceEndpointer`); `stop()` only exists for the "send now" button and for a
 * caller that has to bail out.
 */

export type VoiceInputErrorCode =
  | 'insecure'
  | 'unsupported'
  | 'permission'
  | 'no-device'
  | 'busy'
  | 'audio';

export class VoiceInputError extends Error {
  readonly code: VoiceInputErrorCode;

  constructor(code: VoiceInputErrorCode, message: string) {
    super(message);
    this.name = 'VoiceInputError';
    this.code = code;
  }
}

export interface RecordingResult {
  /** Null when no speech was heard; there is nothing worth uploading then. */
  blob: Blob | null;
  heardSpeech: boolean;
  durationMs: number;
}

export interface RecordingOptions {
  /** Called exactly once for a completed turn. A cancelled turn never calls it. */
  onDone: (result: RecordingResult) => void;
  /** Normalised 0..1 input level, throttled to about 20 updates per second. */
  onLevel?: (level: number) => void;
}

export interface RecordingHandle {
  /** Finish the turn now and deliver it through `onDone`. */
  stop(): void;
  /** Abandon the turn: the microphone is released and `onDone` is not called. */
  cancel(): void;
}

// The processor lives in `public/` because `audioWorklet.addModule` needs a
// real URL: Vite inlines a small `?url` or `new URL` import as a data: URL, and
// a worklet cannot be relied on to load one. A file in `public/` is served
// untouched in dev and copied as-is into a build.
const CAPTURE_WORKLET_URL = `${import.meta.env.BASE_URL}voice-capture-worklet.js`;

const TARGET_RATE = 16_000;
const LEVEL_UPDATES_PER_SECOND = 20;
/** No audio block for this long means the microphone stopped delivering. */
const STALL_MS = 3_000;
/** A hard cap even if frames trickle in, so a turn cannot run for ever. */
const MAX_TURN_MS = 25_000;

export async function startRecording(
  options: RecordingOptions,
): Promise<RecordingHandle> {
  // getUserMedia is defined only in a secure context; on a plain-HTTP LAN
  // address the API is missing entirely, and "unsupported browser" would send
  // the owner looking in the wrong place.
  if (typeof window !== 'undefined' && !window.isSecureContext) {
    throw new VoiceInputError(
      'insecure',
      'microphone capture needs a secure context (https or localhost)',
    );
  }
  if (
    typeof navigator === 'undefined' ||
    !navigator.mediaDevices?.getUserMedia
  ) {
    throw new VoiceInputError(
      'unsupported',
      'this browser does not expose microphone capture',
    );
  }
  if (typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') {
    throw new VoiceInputError(
      'unsupported',
      'this browser does not support AudioWorklet',
    );
  }

  let stream: MediaStream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (error) {
    throw microphoneError(error);
  }

  let context: AudioContext;
  let source: MediaStreamAudioSourceNode;
  let node: AudioWorkletNode;
  let mute: GainNode;
  try {
    context = new AudioContext();
    await context.resume();
    await context.audioWorklet.addModule(CAPTURE_WORKLET_URL);
    source = context.createMediaStreamSource(stream);
    node = new AudioWorkletNode(context, 'winnie-capture', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      channelCount: 1,
      channelCountMode: 'explicit',
      channelInterpretation: 'speakers',
    });
    // A worklet is pulled only when it reaches the destination; a zero gain
    // keeps the microphone out of the speakers, feedback out of the graph and
    // the analyser fed.
    mute = context.createGain();
    mute.gain.value = 0;
    source.connect(node);
    node.connect(mute);
    mute.connect(context.destination);
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    throw new VoiceInputError(
      'audio',
      error instanceof Error ? error.message : String(error),
    );
  }

  const sampleRate = context.sampleRate;
  const endpointer = new VoiceEndpointer({ sampleRate });
  const chunks: Float32Array[] = [];
  let totalSamples = 0;
  let lastLevelSample = 0;
  let settled = false;
  let guard = 0;
  const startedAt = performance.now();
  let lastFrameAt = startedAt;

  const release = () => {
    window.clearInterval(guard);
    node.port.onmessage = null;
    try {
      source.disconnect();
      node.disconnect();
      mute.disconnect();
    } catch {
      // The graph may already be torn down by a closed context; releasing the
      // tracks below is the part that actually turns the microphone off.
    }
    stream.getTracks().forEach((track) => track.stop());
    void context.close();
  };

  const finish = () => {
    if (settled) {
      return;
    }
    settled = true;
    const durationMs = Math.round((totalSamples / sampleRate) * 1_000);
    const heardSpeech = endpointer.heardSpeech;
    const pcm = concat(chunks, totalSamples);
    release();
    if (!heardSpeech) {
      options.onDone({ blob: null, heardSpeech: false, durationMs });
      return;
    }
    void encodeWav(pcm, sampleRate)
      .then((blob) => options.onDone({ blob, heardSpeech: true, durationMs }))
      .catch(() => options.onDone({ blob: null, heardSpeech: true, durationMs }));
  };

  node.port.onmessage = (event: MessageEvent<Float32Array>) => {
    if (settled) {
      return;
    }
    lastFrameAt = performance.now();
    const chunk = event.data;
    chunks.push(chunk);
    totalSamples += chunk.length;
    if (
      options.onLevel &&
      totalSamples - lastLevelSample >=
        sampleRate / LEVEL_UPDATES_PER_SECOND
    ) {
      lastLevelSample = totalSamples;
      options.onLevel(normaliseLevel(levelDbfs(chunk)));
    }
    if (endpointer.accept(chunk)) {
      finish();
    }
  };

  // The endpointing rules count audio, and audio that never reaches the page
  // would otherwise leave the bar in "Listening…" for ever. The phone has the
  // same guard because a microphone that stops delivering frames (unplugged,
  // suspended by the OS, a stalled graph) must not pin the sheet shut.
  guard = window.setInterval(() => {
    const now = performance.now();
    if (now - lastFrameAt >= STALL_MS || now - startedAt >= MAX_TURN_MS) {
      finish();
    }
  }, 250);

  return {
    stop: finish,
    cancel: () => {
      if (settled) {
        return;
      }
      settled = true;
      release();
    },
  };
}

/** Map a dBFS level onto the meter's 0..1 range; room noise sits near zero. */
function normaliseLevel(dbfs: number): number {
  const level = (dbfs + 60) / 55;
  return Math.max(0, Math.min(1, level));
}

function microphoneError(error: unknown): VoiceInputError {
  if (error instanceof DOMException) {
    switch (error.name) {
      case 'NotAllowedError':
      case 'SecurityError':
        return new VoiceInputError(
          'permission',
          'microphone access was refused',
        );
      case 'NotFoundError':
      case 'OverconstrainedError':
        return new VoiceInputError('no-device', 'no microphone was found');
      case 'NotReadableError':
      case 'AbortError':
        return new VoiceInputError(
          'busy',
          'the microphone is held by another application',
        );
      default:
        break;
    }
  }
  return new VoiceInputError(
    'audio',
    error instanceof Error ? error.message : String(error),
  );
}

function concat(chunks: Float32Array[], total: number): Float32Array {
  const pcm = new Float32Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    pcm.set(chunk, offset);
    offset += chunk.length;
  }
  return pcm;
}

async function encodeWav(pcm: Float32Array, sampleRate: number): Promise<Blob> {
  const samples =
    sampleRate === TARGET_RATE ? pcm : await resample(pcm, sampleRate);
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  writeAscii(view, 0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeAscii(view, 8, 'WAVE');
  writeAscii(view, 12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, TARGET_RATE, true);
  view.setUint32(28, TARGET_RATE * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(view, 36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }
  return new Blob([bytes], { type: 'audio/wav' });
}

/**
 * Resample to 16 kHz through an OfflineAudioContext.
 *
 * The browser's own resampler is better than a hand-written linear
 * interpolation and costs one promise; the 16 kHz mono contract is what the
 * ASR adapter expects, so the sample rate is not negotiable.
 */
async function resample(
  pcm: Float32Array,
  sampleRate: number,
): Promise<Float32Array> {
  const length = Math.max(1, Math.ceil((pcm.length * TARGET_RATE) / sampleRate));
  const offline = new OfflineAudioContext(1, length, TARGET_RATE);
  const buffer = offline.createBuffer(1, pcm.length, sampleRate);
  // `copyToChannel` narrows its argument to Float32Array<ArrayBuffer>, which a
  // Float32Array typed as ArrayBufferLike does not satisfy; writing through the
  // channel view accepts any Float32Array and copies exactly as much.
  buffer.getChannelData(0).set(pcm);
  const source = offline.createBufferSource();
  source.buffer = buffer;
  source.connect(offline.destination);
  source.start();
  const rendered = await offline.startRendering();
  return rendered.getChannelData(0);
}

function writeAscii(view: DataView, offset: number, text: string): void {
  for (let index = 0; index < text.length; index += 1) {
    view.setUint8(offset + index, text.charCodeAt(index));
  }
}
