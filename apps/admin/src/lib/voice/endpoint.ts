/**
 * Decides when a spoken turn has ended, from the audio alone.
 *
 * This is the console's counterpart of the phone's `EndpointDetector.kt`, and
 * it keeps that detector's rule and numbers: after speech has been seen,
 * trailing silence ends the turn; a turn that never contains speech ends on its
 * own; a turn that never stops is cut at the maximum length. Sharing the values
 * keeps a voice turn feeling the same on both clients, and they were chosen for
 * a microphone at arm's length, which is also how the console is used.
 *
 * The input is Float32 samples in [-1, 1] at whatever rate the audio context
 * runs at, so the frame length is derived from the rate instead of assumed. The
 * class is pure — no Web Audio, no DOM — so a browser is not needed to reason
 * about the rules.
 */
export interface EndpointerOptions {
  sampleRate: number;
  /** Frame length; the unit every threshold below is counted in. */
  frameMs?: number;
  /** RMS above this counts as speech; 20 dB below full scale is quiet room noise. */
  speechFloorDbfs?: number;
  /** Frames above the floor before a burst is accepted as speech. */
  startFrames?: number;
  /** Silence after speech that ends the turn. */
  endSilenceMs?: number;
  /** A turn that keeps talking is cut here. */
  maxSpeechMs?: number;
  /** A turn with no speech at all ends here. */
  maxSilenceMs?: number;
}

const DEFAULTS = {
  frameMs: 20,
  speechFloorDbfs: -38,
  startFrames: 3,
  endSilenceMs: 1_200,
  maxSpeechMs: 20_000,
  maxSilenceMs: 6_000,
} as const;

export class VoiceEndpointer {
  readonly frameSamples: number;
  private readonly frameMs: number;
  private readonly speechFloorDbfs: number;
  private readonly startFrames: number;
  private readonly endSilenceMs: number;
  private readonly maxSpeechMs: number;
  private readonly maxSilenceMs: number;

  private readonly pending: Float32Array;
  private pendingSamples = 0;
  private activeFrames = 0;
  private silenceFrames = 0;
  private elapsedMs = 0;
  private finished = false;

  /** True once the first frames above the floor arrived. */
  heardSpeech = false;

  constructor(options: EndpointerOptions) {
    if (!(options.sampleRate > 0)) {
      throw new Error('the endpointer needs a positive sample rate');
    }
    this.frameMs = options.frameMs ?? DEFAULTS.frameMs;
    this.speechFloorDbfs = options.speechFloorDbfs ?? DEFAULTS.speechFloorDbfs;
    this.startFrames = options.startFrames ?? DEFAULTS.startFrames;
    this.endSilenceMs = options.endSilenceMs ?? DEFAULTS.endSilenceMs;
    this.maxSpeechMs = options.maxSpeechMs ?? DEFAULTS.maxSpeechMs;
    this.maxSilenceMs = options.maxSilenceMs ?? DEFAULTS.maxSilenceMs;
    this.frameSamples = Math.max(
      1,
      Math.round((options.sampleRate * this.frameMs) / 1_000),
    );
    this.pending = new Float32Array(this.frameSamples);
  }

  /**
   * Feed captured samples. Returns true exactly once, when the turn is over;
   * a caller that keeps recording after that is wasting the microphone.
   */
  accept(samples: Float32Array): boolean {
    if (this.finished) {
      return false;
    }
    let offset = 0;
    while (offset < samples.length) {
      const copy = Math.min(
        samples.length - offset,
        this.frameSamples - this.pendingSamples,
      );
      this.pending.set(samples.subarray(offset, offset + copy), this.pendingSamples);
      this.pendingSamples += copy;
      offset += copy;
      if (this.pendingSamples === this.frameSamples) {
        this.pendingSamples = 0;
        if (this.checkFrame()) {
          return true;
        }
      }
    }
    return false;
  }

  /** Elapsed audio in milliseconds; used only for the bar's timer. */
  get elapsed(): number {
    return this.elapsedMs;
  }

  private checkFrame(): boolean {
    this.elapsedMs += this.frameMs;
    const level = levelDbfs(this.pending);
    if (level >= this.speechFloorDbfs) {
      this.activeFrames += 1;
      this.silenceFrames = 0;
      if (this.activeFrames >= this.startFrames) {
        this.heardSpeech = true;
      }
    } else {
      this.activeFrames = 0;
      if (this.heardSpeech) {
        this.silenceFrames += 1;
      }
    }
    const silenceMs = this.silenceFrames * this.frameMs;
    const done =
      (this.heardSpeech && silenceMs >= this.endSilenceMs) ||
      this.elapsedMs >= this.maxSpeechMs ||
      (!this.heardSpeech && this.elapsedMs >= this.maxSilenceMs);
    if (done) {
      this.finished = true;
    }
    return done;
  }
}

/** RMS level of a frame in dBFS, with digital silence floored at -120. */
export function levelDbfs(samples: Float32Array): number {
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    sum += samples[index] * samples[index];
  }
  const rms = Math.sqrt(sum / Math.max(1, samples.length));
  return rms <= 0 ? -120 : 20 * Math.log10(rms);
}
