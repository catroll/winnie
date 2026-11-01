import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type {
  AssistantConversation,
  AssistantConversationList,
  AssistantMessage,
  AssistantStatus,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { Markdown } from '../components/Markdown';
import { PageHeader } from '../components/PageHeader';
import { VoiceBar } from '../components/VoiceBar';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';
import { conversationChannel } from '../lib/status';
import { useVoiceConversation } from '../lib/voice/useVoiceConversation';

const PAGE_SIZE = 50;
const MAX_PAGE_SIZE = 200;

// `source` says who started a turn (a person or a scheduled task); `channel`
// says where it arrived. The list filter uses the channel so every entry point
// is visible from one place.
const CHANNELS = [
  { value: '', label: 'All channels' },
  { value: 'admin', label: 'Console' },
  { value: 'web', label: 'Mobile web' },
  { value: 'api', label: 'API' },
  { value: 'task', label: 'Scheduled task' },
];

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function Avatar({ assistant }: { assistant: boolean }) {
  return (
    <div
      className={`direct-chat-img d-flex align-items-center justify-content-center ${
        assistant ? 'bg-primary text-white' : 'bg-secondary text-white'
      }`}
      aria-hidden="true"
    >
      <i className={`bi ${assistant ? 'bi-stars' : 'bi-person-fill'}`} />
    </div>
  );
}

function Message({ message }: { message: AssistantMessage }) {
  if (message.role === 'tool') {
    return (
      <details className="border rounded bg-body-tertiary p-2 mb-3">
        <summary className="small text-secondary">
          <i className="bi bi-tools me-1" aria-hidden="true" />
          {message.name || tr('tool')} · {formatDateTime(message.at)}
        </summary>
        <pre
          className="small mt-2 mb-0 text-wrap"
          style={{ maxHeight: '14rem', overflow: 'auto' }}
        >
          {message.text}
        </pre>
      </details>
    );
  }

  const outgoing = message.role === 'user';
  return (
    <div className={`direct-chat-msg${outgoing ? ' end' : ''}`}>
      <div className="direct-chat-infos clearfix">
        <span className={`direct-chat-name ${outgoing ? 'float-end' : 'float-start'}`}>
          {outgoing ? tr('You') : 'Winnie'}
        </span>
        <span className={`direct-chat-timestamp ${outgoing ? 'float-start' : 'float-end'}`}>
          {formatDateTime(message.at)}
        </span>
      </div>
      <Avatar assistant={!outgoing} />
      {outgoing ? (
        // The owner's own turn is shown as typed. A leading `#` or `-` there is
        // a keystroke, not markup, and reinterpreting it would hide the text.
        <div className="direct-chat-text winnie-pre-line text-break">{message.text}</div>
      ) : (
        // The reply is model output that arrives as Markdown. `breaks` keeps a
        // single newline a line, which is how the author typed it.
        <div className="direct-chat-text text-break">
          <Markdown source={message.text} breaks />
        </div>
      )}
    </div>
  );
}

function Thinking() {
  return (
    <div className="direct-chat-msg">
      <div className="direct-chat-infos clearfix">
        <span className="direct-chat-name float-start">Winnie</span>
      </div>
      <Avatar assistant />
      <div className="direct-chat-text text-secondary" role="status">
        <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
        {tr('Thinking…')}
      </div>
    </div>
  );
}

export function ChatPage() {
  const { conversationId } = useParams<{ conversationId: string }>();
  const navigate = useNavigate();

  const [channel, setChannel] = useState('');
  const [pageSize, setPageSize] = useState(PAGE_SIZE);
  const [historyRevision, setHistoryRevision] = useState(0);

  const history = useApi<AssistantConversationList>(
    () =>
      api.assistantConversations({
        limit: pageSize,
        channel: channel || undefined,
      }),
    [pageSize, channel, historyRevision],
  );

  // The status call only decorates the history footer; a failure must not hide
  // the conversations, so it resolves to null instead of rejecting.
  const status = useApi<AssistantStatus | null>(
    () => api.assistantStatus().catch(() => null),
    [],
  );

  const [conversation, setConversation] = useState<AssistantConversation | null>(null);
  const [conversationLoading, setConversationLoading] = useState(false);
  const [conversationError, setConversationError] = useState<Error | null>(null);
  const [conversationRevision, setConversationRevision] = useState(0);

  useEffect(() => {
    if (!conversationId) {
      setConversation(null);
      setConversationError(null);
      setConversationLoading(false);
      return;
    }
    let alive = true;
    setConversationLoading(true);
    setConversationError(null);
    api.assistantConversation(conversationId).then(
      (data) => {
        if (!alive) {
          return;
        }
        setConversation(data);
        setConversationLoading(false);
      },
      (error: unknown) => {
        if (!alive) {
          return;
        }
        setConversation(null);
        setConversationError(
          error instanceof Error ? error : new Error(String(error)),
        );
        setConversationLoading(false);
      },
    );
    return () => {
      alive = false;
    };
  }, [conversationId, conversationRevision]);

  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);

  const messagesRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Keep the newest message visible, both for a fetched transcript and while a
  // reply is pending.
  useEffect(() => {
    const element = messagesRef.current;
    if (element) {
      element.scrollTop = element.scrollHeight;
    }
  }, [conversation?.messages.length, pendingUser, sending, conversationId]);

  useEffect(() => {
    setDraft('');
    setPendingUser(null);
    setSendError(null);
  }, [conversationId]);

  // Grow the composer with its content, up to the CSS max-height.
  useEffect(() => {
    const element = textareaRef.current;
    if (!element) {
      return;
    }
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  }, [draft]);

  const activeConversation =
    conversation && conversation.id === conversationId ? conversation : null;
  const readOnly = activeConversation?.source === 'task';
  // A deep link can still be loading, or point at a transcript that no longer
  // exists. Neither state may accept a message.
  const loadingConversation =
    Boolean(conversationId) && !activeConversation && !conversationError;
  const composerBlocked = sending || (Boolean(conversationId) && !activeConversation);

  const submit = async (text?: string): Promise<string | null> => {
    const message = (text ?? draft).trim();
    if (!message || composerBlocked || readOnly) {
      return null;
    }
    setSending(true);
    setSendError(null);
    setPendingUser(message);
    setDraft('');
    try {
      const response = await api.assistantChat({
        message,
        conversation_id: conversationId,
        channel: 'admin',
      });
      setPendingUser(null);
      setHistoryRevision((value) => value + 1);
      // The footer count comes from /status, which is otherwise fetched once.
      status.reload();
      if (!conversationId) {
        navigate(`/chat/${response.conversation_id}`, { replace: true });
      } else {
        // Re-read the file so tool blocks, which the chat response does not
        // return, appear in the record.
        setConversationRevision((value) => value + 1);
      }
      return response.reply;
    } catch (error) {
      setPendingUser(null);
      setSendError(describe(error));
      setDraft(message);
      return null;
    } finally {
      setSending(false);
    }
  };

  // A voice turn is a normal chat turn whose text came from the microphone, so
  // it goes through the same submit path and the reply comes back for speaking.
  const voice = useVoiceConversation({ onUtterance: submit });

  const loadedHistory = history.status === 'success' ? history.data : null;
  const items = loadedHistory?.items ?? [];
  const total = loadedHistory?.total ?? 0;
  const canLoadMore = items.length < total && pageSize < MAX_PAGE_SIZE;
  const channelMeta = activeConversation
    ? conversationChannel(activeConversation.channel)
    : null;

  return (
    <>
      <PageHeader title="Chat" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="row g-3">
            <div className="col-12 col-lg-4 col-xl-3 order-2 order-lg-1">
              <div className="card mb-3">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">{tr('Conversations')}</h3>
                  <div className="ms-auto d-flex gap-2">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={history.reload}
                      aria-label={tr('Refresh conversations')}
                      title={tr('Refresh conversations')}
                    >
                      <i className="bi bi-arrow-clockwise" aria-hidden="true" />
                    </button>
                    <Link to="/chat" className="btn btn-sm btn-primary">
                      <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                      {tr('New chat')}
                    </Link>
                  </div>
                </div>

                <div className="card-body border-bottom">
                  <label className="form-label small text-secondary" htmlFor="chat-channel">
                    {tr('Channel')}
                  </label>
                  <select
                    id="chat-channel"
                    className="form-select form-select-sm"
                    value={channel}
                    onChange={(event) => {
                      setChannel(event.target.value);
                      setPageSize(PAGE_SIZE);
                    }}
                  >
                    {CHANNELS.map((option) => (
                      <option key={option.value || 'all'} value={option.value}>
                        {tr(option.label)}
                      </option>
                    ))}
                  </select>
                  <p className="small text-secondary mb-0 mt-2">
                    {tr(
                      'Every entry point in one history: console, phone page, API and scheduled task runs.',
                    )}
                  </p>
                </div>

                {history.status === 'loading' ? (
                  <div className="p-3">
                    <LoadingBlock label="Loading conversations…" />
                  </div>
                ) : history.status === 'error' ? (
                  <div className="p-3">
                    <ErrorBlock error={history.error} onRetry={history.reload} />
                    <div className="alert alert-secondary mt-2 mb-0 small" role="note">
                      {tr(
                        'Conversations come from the assistant service. They are available when',
                      )}{' '}
                      <code>assistant.enabled</code> {tr('is true in')}{' '}
                      <code> apps/api/config.yaml</code>.
                    </div>
                  </div>
                ) : items.length === 0 ? (
                  <EmptyState
                    message={
                      channel
                        ? 'No conversations on this channel yet.'
                        : 'No conversations yet. Send the first message.'
                    }
                  />
                ) : (
                  <div className="list-group list-group-flush winnie-chat-history">
                    {items.map((item) => {
                      const meta = conversationChannel(item.channel);
                      const active = item.id === conversationId;
                      return (
                        <Link
                          key={item.id}
                          to={`/chat/${item.id}`}
                          className={`list-group-item list-group-item-action${
                            active ? ' bg-body-secondary' : ''
                          }`}
                          aria-current={active ? 'page' : undefined}
                        >
                          <div className="d-flex align-items-start gap-2">
                            <span className="fw-semibold text-break flex-grow-1">
                              {item.title}
                            </span>
                            <span className={`badge ${meta.badge} flex-shrink-0`}>
                              {meta.label}
                            </span>
                          </div>
                          <div className="small text-secondary text-truncate">
                            {item.preview || tr('No preview')}
                          </div>
                          <div className="small text-secondary mt-1">
                            {formatDateTime(item.created_at)} ·{' '}
                            {item.message_count === 1
                              ? tr('{{count}} message', { count: item.message_count })
                              : tr('{{count}} messages', { count: item.message_count })}
                          </div>
                        </Link>
                      );
                    })}
                  </div>
                )}

                <div className="card-footer d-flex flex-wrap align-items-center gap-2 small text-secondary">
                  <span>
                    {tr('{{loaded}} of {{total}}', { loaded: items.length, total })}
                  </span>
                  {canLoadMore ? (
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary ms-auto"
                      onClick={() =>
                        setPageSize((value) =>
                          Math.min(MAX_PAGE_SIZE, value + PAGE_SIZE),
                        )
                      }
                    >
                      {tr('Load more')}
                    </button>
                  ) : null}
                  <span className={canLoadMore ? '' : 'ms-auto'}>
                    {status.status === 'loading' ? (
                      tr('Loading status…')
                    ) : status.status === 'success' && status.data ? (
                      <>
                        {tr('Model')} <code>{status.data.model}</code> ·{' '}
                        {tr('{{count}} stored', { count: status.data.conversations })}
                      </>
                    ) : (
                      tr('Assistant status unavailable')
                    )}
                  </span>
                </div>
              </div>
            </div>

            <div className="col-12 col-lg-8 col-xl-9 order-1 order-lg-2">
              <div className="card direct-chat direct-chat-primary winnie-chat">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3
                    className="card-title mb-0 text-truncate"
                    style={{ maxWidth: '32rem' }}
                  >
                    {activeConversation?.title ?? tr('New conversation')}
                  </h3>
                  {channelMeta ? (
                    <>
                      <span className={`badge ${channelMeta.badge}`}>
                        {channelMeta.label}
                      </span>
                      {activeConversation?.source === 'task' ? (
                        <span className="badge text-bg-warning">{tr('Task run')}</span>
                      ) : null}
                      <span className="small text-secondary">
                        {formatDateTime(activeConversation?.created_at ?? null)}
                      </span>
                    </>
                  ) : null}
                  <div className="ms-auto small text-secondary">
                    {activeConversation
                      ? activeConversation.messages.length === 1
                        ? tr('{{count}} message', {
                            count: activeConversation.messages.length,
                          })
                        : tr('{{count}} messages', {
                            count: activeConversation.messages.length,
                          })
                      : status.status === 'success' && status.data
                        ? tr('Model {{model}}', { model: status.data.model })
                        : ''}
                  </div>
                </div>

                <div className="card-body">
                  <div
                    className="direct-chat-messages"
                    role="log"
                    aria-live="polite"
                    aria-relevant="additions"
                    tabIndex={0}
                    ref={messagesRef}
                  >
                    {conversationError ? (
                      <>
                        <ErrorBlock
                          error={conversationError}
                          onRetry={() =>
                            setConversationRevision((value) => value + 1)
                          }
                        />
                        <p className="small text-secondary">
                          <Link to="/chat">{tr('Start a new chat')}</Link>{' '}
                          {tr('if this record no longer exists.')}
                        </p>
                      </>
                    ) : !activeConversation && conversationLoading ? (
                      <div className="p-4 text-center" role="status">
                        <span
                          className="spinner-border text-primary"
                          aria-hidden="true"
                        />
                        <p className="mt-3 mb-0 text-secondary">
                          {tr('Loading conversation…')}
                        </p>
                      </div>
                    ) : !activeConversation && !conversationId ? (
                      <div className="text-center text-secondary py-5">
                        <i className="bi bi-chat-dots fs-1 d-block mb-2" aria-hidden="true" />
                        <h2 className="h5 text-body">{tr('Start a conversation')}</h2>
                        <p className="mb-0">
                          {tr(
                            'Winnie answers from local files, web search and the document index. Its history also holds conversations from the phone page, API clients and scheduled tasks.',
                          )}
                        </p>
                      </div>
                    ) : activeConversation && activeConversation.messages.length === 0 ? (
                      <EmptyState message="This conversation has no messages yet." />
                    ) : (
                      activeConversation?.messages.map((message, index) => (
                        <Message
                          key={`${message.at}-${message.role}-${index}`}
                          message={message}
                        />
                      ))
                    )}

                    {pendingUser ? (
                      <Message
                        message={{
                          role: 'user',
                          name: '',
                          text: pendingUser,
                          at: new Date().toISOString(),
                        }}
                      />
                    ) : null}
                    {sending ? <Thinking /> : null}
                  </div>
                </div>

                {sendError ? (
                  <div className="alert alert-danger m-0 rounded-0" role="alert">
                    <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
                    {sendError}
                  </div>
                ) : null}

                {readOnly ? (
                  <div className="card-footer d-flex flex-wrap align-items-center gap-2">
                    <span className="small text-secondary flex-grow-1">
                      {tr(
                        'This conversation is a scheduled task run. It is kept as a record and cannot be continued.',
                      )}
                    </span>
                    <Link to="/chat" className="btn btn-sm btn-primary">
                      {tr('Start a new chat')}
                    </Link>
                  </div>
                ) : conversationError ? (
                  <div className="card-footer d-flex flex-wrap align-items-center gap-2">
                    <span className="small text-secondary flex-grow-1">
                      {tr(
                        'This conversation could not be loaded. Start a new chat to continue.',
                      )}
                    </span>
                    <Link to="/chat" className="btn btn-sm btn-primary">
                      {tr('Start a new chat')}
                    </Link>
                  </div>
                ) : voice.phase !== 'idle' || voice.error ? (
                  <VoiceBar voice={voice} />
                ) : (
                  <form
                    className="card-footer"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void submit();
                    }}
                  >
                    <label className="visually-hidden" htmlFor="chat-message">
                      {tr('Message Winnie')}
                    </label>
                    {/* `.form-control` is `width: 100%`, which as a flex item
                        is a basis the width of the whole row; without
                        `flex-shrink-0` the buttons absorb that overflow and the
                        send label wraps under its icon. */}
                    <div className="d-flex align-items-end gap-2">
                      <textarea
                        id="chat-message"
                        ref={textareaRef}
                        className="form-control flex-grow-1"
                        rows={1}
                        value={draft}
                        placeholder={tr('Message Winnie…')}
                        onChange={(event) => setDraft(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            event.key === 'Enter' &&
                            !event.shiftKey &&
                            !event.nativeEvent.isComposing
                          ) {
                            event.preventDefault();
                            void submit();
                          }
                        }}
                        disabled={composerBlocked}
                        style={{ resize: 'none', maxHeight: '160px' }}
                      />
                      <button
                        type="button"
                        id="chat-voice"
                        className="btn btn-outline-secondary flex-shrink-0"
                        onClick={voice.start}
                        disabled={composerBlocked}
                        title={tr('Voice conversation')}
                        aria-label={tr('Voice conversation')}
                      >
                        <i className="bi bi-mic" aria-hidden="true" />
                      </button>
                      <button
                        type="submit"
                        className="btn btn-primary flex-shrink-0"
                        disabled={composerBlocked || !draft.trim()}
                      >
                        {sending ? (
                          <span
                            className="spinner-border spinner-border-sm"
                            aria-hidden="true"
                          />
                        ) : (
                          <i className="bi bi-send-fill" aria-hidden="true" />
                        )}
                        <span className="ms-2 d-none d-sm-inline">
                          {sending ? tr('Sending') : tr('Send')}
                        </span>
                      </button>
                    </div>
                    <div className="small text-secondary mt-1">
                      {loadingConversation
                        ? tr('Loading the conversation…')
                        : tr('Enter sends · Shift+Enter starts a new line')}
                    </div>
                  </form>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
