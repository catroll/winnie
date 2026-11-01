import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from 'react';

import { ApiError, api } from '../../api/client';
import { tr } from '../../i18n';
import {
  startRecording,
  VoiceInputError,
  type RecordingHandle,
  type RecordingResult,
} from './recorder';
import { canSpeak, speak, stopSpeaking } from './speech';

/**
 * Drives one voice conversation in the chat page.
 *
 * A conversation, not a single dictation: listen, transcribe, send, speak the
 * reply, and listen again until the owner stops it. That loop is the reason the
 * composer is replaced by a status bar while it runs — the owner is talking,
 * not typing, and every state in the loop needs a name they can see.
 *
 * The page owns the conversation; this hook only feeds it text through
 * `onUtterance` and receives the reply to speak. `onUtterance` returning null
 * means the message could not be sent; the page has already shown why, so the
 * loop ends quietly instead of repeating the error.
 */

export type VoicePhase =
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking';

export interface VoiceConversation {
  phase: VoicePhase;
  /** A failure that ended the loop; the bar shows it until dismissed. */
  error: string | null;
  /** Live 0..1 input level, written without a render per audio block. */
  level: RefObject<number>;
  repliesEnabled: boolean;
  setRepliesEnabled: (value: boolean) => void;
  /** False when the browser has no speech synthesiser at all. */
  speechAvailable: boolean;
  start: () => void;
  /** Finish the current turn now, as if the pause had been heard. */
  stopListening: () => void;
  cancel: () => void;
}

const REPLIES_KEY = 'winnie.admin.voice.replies';

interface Options {
  onUtterance: (text: string) => Promise<string | null>;
}

export function useVoiceConversation({ onUtterance }: Options): VoiceConversation {
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [repliesEnabled, setRepliesEnabledState] = useState(readReplies);
  const level = useRef(0);
  const active = useRef(false);
  const session = useRef(0);
  const handle = useRef<RecordingHandle | null>(null);
  const utterance = useRef(onUtterance);
  const replies = useRef(repliesEnabled);
  const speechAvailable = canSpeak();

  useEffect(() => {
    utterance.current = onUtterance;
  }, [onUtterance]);
  useEffect(() => {
    replies.current = repliesEnabled;
  }, [repliesEnabled]);

  const setRepliesEnabled = useCallback((value: boolean) => {
    setRepliesEnabledState(value);
    try {
      window.localStorage.setItem(REPLIES_KEY, value ? 'true' : 'false');
    } catch {
      // A browser that refuses storage still keeps the toggle for this session.
    }
  }, []);

  /** End the loop and release everything it holds. */
  const reset = useCallback((message: string | null) => {
    active.current = false;
    session.current += 1;
    handle.current?.cancel();
    handle.current = null;
    level.current = 0;
    stopSpeaking();
    setPhase('idle');
    setError(message);
  }, []);

  const fail = useCallback(
    (message: string) => {
      reset(message);
    },
    [reset],
  );

  const start = useCallback(() => {
    if (active.current) {
      return;
    }
    active.current = true;
    const token = ++session.current;
    setError(null);
    setPhase('listening');

    async function turn(): Promise<void> {
      if (token !== session.current) {
        return;
      }
      setPhase('listening');
      let recording: RecordingHandle;
      try {
        recording = await startRecording({
          onLevel: (value) => {
            level.current = value;
          },
          onDone: (result) => {
            void consume(result);
          },
        });
      } catch (failure) {
        // A Cancel while the browser was opening the device must not surface
        // as an error after the owner has already stopped the conversation.
        if (token !== session.current) {
          return;
        }
        fail(microphoneMessage(failure));
        return;
      }
      if (token !== session.current) {
        recording.cancel();
        return;
      }
      handle.current = recording;
    }

    async function consume(result: RecordingResult): Promise<void> {
      handle.current = null;
      level.current = 0;
      if (token !== session.current) {
        return;
      }
      if (!result.heardSpeech || !result.blob) {
        fail(tr('Nothing was recognized. Try again.'));
        return;
      }
      setPhase('transcribing');
      let text: string;
      try {
        text = (await api.assistantTranscribe(result.blob)).text.trim();
      } catch (failure) {
        if (token !== session.current) {
          return;
        }
        fail(transcriptionMessage(failure));
        return;
      }
      if (token !== session.current) {
        return;
      }
      if (!text) {
        fail(tr('Nothing was recognized. Try again.'));
        return;
      }
      setPhase('thinking');
      let reply: string | null = null;
      try {
        reply = await utterance.current(text);
      } catch {
        reply = null;
      }
      if (token !== session.current) {
        return;
      }
      if (reply === null) {
        // The page has shown its own send error; a voice loop that retried
        // would only fail again in the same way.
        active.current = false;
        setPhase('idle');
        return;
      }
      if (reply && replies.current) {
        setPhase('speaking');
        try {
          await speak(reply);
        } catch {
          if (token !== session.current) {
            return;
          }
          fail(tr('The reply could not be spoken aloud.'));
          return;
        }
      }
      if (token !== session.current) {
        return;
      }
      await turn();
    }

    void turn();
  }, [fail]);

  const stopListening = useCallback(() => {
    handle.current?.stop();
  }, []);

  const cancel = useCallback(() => {
    reset(null);
  }, [reset]);

  // A voice turn holds the microphone; leaving the page must release it.
  useEffect(() => {
    return () => {
      active.current = false;
      session.current += 1;
      handle.current?.cancel();
      handle.current = null;
      stopSpeaking();
    };
  }, []);

  return {
    phase,
    error,
    level,
    repliesEnabled,
    setRepliesEnabled,
    speechAvailable,
    start,
    stopListening,
    cancel,
  };
}

function readReplies(): boolean {
  if (!canSpeak()) {
    return false;
  }
  try {
    return window.localStorage.getItem(REPLIES_KEY) !== 'false';
  } catch {
    return true;
  }
}

function microphoneMessage(failure: unknown): string {
  if (failure instanceof VoiceInputError) {
    switch (failure.code) {
      case 'permission':
        return tr(
          'Microphone access was refused. Allow it for this site, then try again.',
        );
      case 'no-device':
        return tr('No microphone was found.');
      case 'busy':
        return tr('The microphone is in use by another application.');
      case 'insecure':
        return tr('Voice input needs a secure connection (https or localhost).');
      case 'unsupported':
        return tr('Voice input is not supported in this browser.');
      default:
        return tr('The microphone could not be opened.');
    }
  }
  return tr('The microphone could not be opened.');
}

function transcriptionMessage(failure: unknown): string {
  if (failure instanceof ApiError) {
    if (failure.status === 422) {
      return tr('Nothing was recognized. Try again.');
    }
    if (failure.status === 502 || failure.status === 503) {
      return tr('Speech recognition is not available right now.');
    }
    return failure.detail;
  }
  return tr('The recording could not be transcribed.');
}
