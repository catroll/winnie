import { useEffect, useRef, type RefObject } from 'react';

import { tr } from '../i18n';
import { stopSpeaking } from '../lib/voice/speech';
import type { VoiceConversation } from '../lib/voice/useVoiceConversation';

/**
 * The composer's replacement while a voice conversation runs.
 *
 * It is a status line with controls rather than a modal: a voice turn takes
 * seconds, the transcript and the reply appear in the message list above, and
 * the owner must be able to stop the microphone or the speech at any point.
 * The "Read replies aloud" switch lives here because it only means anything
 * while the loop is running.
 */
export function VoiceBar({ voice }: { voice: VoiceConversation }) {
  if (voice.error) {
    return (
      <div className="card-footer winnie-voice-bar">
        <div
          className="alert alert-warning d-flex flex-wrap align-items-center gap-2 mb-0"
          role="alert"
        >
          <i className="bi bi-exclamation-triangle-fill" aria-hidden="true" />
          <span className="flex-grow-1">{voice.error}</span>
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={voice.start}
          >
            {tr('Try again')}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={voice.cancel}
          >
            {tr('Dismiss')}
          </button>
        </div>
      </div>
    );
  }

  const label = (() => {
    switch (voice.phase) {
      case 'listening':
        return tr('Listening… speak now.');
      case 'transcribing':
        return tr('Transcribing your turn…');
      case 'thinking':
        return tr('Thinking…');
      case 'speaking':
        return tr('Speaking…');
      default:
        return '';
    }
  })();

  return (
    <div className="card-footer winnie-voice-bar" role="status" aria-live="polite">
      <div className="d-flex flex-wrap align-items-center gap-2">
        <span className="d-inline-flex align-items-center" aria-hidden="true">
          {voice.phase === 'listening' ? (
            <span className="spinner-grow spinner-grow-sm text-danger" />
          ) : voice.phase === 'speaking' ? (
            <i className="bi bi-soundwave text-primary fs-5" />
          ) : (
            <span className="spinner-border spinner-border-sm text-primary" />
          )}
        </span>
        <span className="flex-grow-1 fw-semibold">{label}</span>
        {voice.phase === 'listening' ? (
          <>
            <span className="small text-secondary d-none d-sm-inline">
              {tr('Pause for a moment to send')}
            </span>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={voice.stopListening}
            >
              <i className="bi bi-send-fill me-1" aria-hidden="true" />
              {tr('Send now')}
            </button>
          </>
        ) : null}
        {voice.phase === 'speaking' ? (
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={() => stopSpeaking()}
          >
            <i className="bi bi-stop-fill me-1" aria-hidden="true" />
            {tr('Stop speaking')}
          </button>
        ) : null}
        <button
          type="button"
          className="btn btn-sm btn-outline-secondary"
          onClick={voice.cancel}
        >
          {tr('Cancel')}
        </button>
      </div>

      <div className="d-flex flex-wrap align-items-center gap-3 mt-2">
        <div className="form-check form-switch mb-0">
          <input
            className="form-check-input"
            type="checkbox"
            role="switch"
            id="voice-replies"
            checked={voice.repliesEnabled}
            disabled={!voice.speechAvailable}
            onChange={(event) => voice.setRepliesEnabled(event.target.checked)}
          />
          <label className="form-check-label small" htmlFor="voice-replies">
            {tr('Read replies aloud')}
          </label>
        </div>
        {!voice.speechAvailable ? (
          <span className="small text-secondary">
            {tr('This browser cannot speak replies.')}
          </span>
        ) : null}
        {voice.phase === 'listening' ? <LevelMeter level={voice.level} /> : null}
      </div>
    </div>
  );
}

function LevelMeter({ level }: { level: RefObject<number> }) {
  const bar = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    let frame = 0;
    // The recorder writes the level ~20 times a second; painting it from an
    // animation frame keeps the message list out of that update path.
    const paint = () => {
      const node = bar.current;
      if (node) {
        node.style.width = `${Math.round(Math.min(1, Math.max(0, level.current)) * 100)}%`;
      }
      frame = window.requestAnimationFrame(paint);
    };
    frame = window.requestAnimationFrame(paint);
    return () => window.cancelAnimationFrame(frame);
  }, [level]);
  return (
    <div
      className="progress winnie-voice-level flex-grow-1"
      style={{ height: '0.35rem', maxWidth: '16rem' }}
      role="presentation"
    >
      <span ref={bar} className="progress-bar bg-danger" style={{ width: '0%' }} />
    </div>
  );
}
