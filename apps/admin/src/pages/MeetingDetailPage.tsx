import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type { Meeting, Minutes, Transcript } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { saveBlob } from '../lib/download';
import {
  formatDateTime,
  formatDuration,
  formatLanguage,
  formatSegmentTime,
} from '../lib/format';
import { useApi } from '../lib/hooks';
import {
  isProcessing,
  meetingStatus,
  sourceLabel,
  stageLabel,
  translationStatus,
} from '../lib/status';

type Tab = 'transcript' | 'minutes';
type Action = 'markdown' | 'txt' | 'regenerate' | 'delete';

interface DetailData {
  meeting: Meeting;
  transcript: Transcript | null;
  transcriptError: string | null;
  minutes: Minutes | null;
  minutesError: string | null;
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

function settledError(result: PromiseSettledResult<unknown>): string | null {
  if (result.status === 'fulfilled') {
    return null;
  }
  return messageOf(result.reason);
}

export function MeetingDetailPage() {
  const { meetingId = '' } = useParams<{ meetingId: string }>();
  const navigate = useNavigate();
  const [tab, setTab] = useState<Tab>('transcript');
  const [action, setAction] = useState<Action | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const result = useApi<DetailData>(async () => {
    if (!meetingId) {
      throw new ApiError(404, tr('No meeting was selected.'));
    }
    const meeting = await api.getMeeting(meetingId);
    const [transcript, minutes] = await Promise.allSettled([
      api.getTranscript(meetingId),
      api.getMinutes(meetingId),
    ]);
    return {
      meeting,
      transcript: transcript.status === 'fulfilled' ? transcript.value : null,
      transcriptError: settledError(transcript),
      minutes: minutes.status === 'fulfilled' ? minutes.value : null,
      minutesError: settledError(minutes),
    };
  }, [meetingId]);

  const title = result.status === 'success' ? result.data.meeting.title : 'Meeting';

  const handleExport = async (format: 'markdown' | 'txt') => {
    if (result.status !== 'success') {
      return;
    }
    setAction(format);
    setActionError(null);
    setNotice(null);
    try {
      const exported = await api.exportMeeting(result.data.meeting.id, format);
      saveBlob(exported.blob, exported.filename);
      setNotice(tr('Downloaded {{filename}}.', { filename: exported.filename }));
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setAction(null);
    }
  };

  const handleRegenerate = async () => {
    if (result.status !== 'success') {
      return;
    }
    const confirmed = window.confirm(
      tr('Regenerate the minutes? This replaces the current minutes and may call the model again.'),
    );
    if (!confirmed) {
      return;
    }
    setAction('regenerate');
    setActionError(null);
    setNotice(null);
    try {
      await api.regenerateMinutes(result.data.meeting.id);
      setNotice(tr('Minutes regenerated.'));
      result.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setAction(null);
    }
  };

  const handleDelete = async () => {
    if (result.status !== 'success') {
      return;
    }
    const confirmed = window.confirm(
      tr('Delete "{{title}}"? This removes its audio, transcript and minutes.', {
        title: result.data.meeting.title,
      }),
    );
    if (!confirmed) {
      return;
    }
    setAction('delete');
    setActionError(null);
    setNotice(null);
    try {
      await api.deleteMeeting(result.data.meeting.id);
      navigate('/meetings', { replace: true });
    } catch (error) {
      setActionError(messageOf(error));
      setAction(null);
    }
  };

  const busy = action !== null;

  return (
    <>
      <PageHeader title={title} crumbs={[{ label: 'Meetings', to: '/meetings' }]} />
      <div className="app-content">
        <div className="container-fluid">
          {result.status === 'loading' ? (
            <LoadingBlock label="Loading meeting…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <Link to="/meetings" className="btn btn-outline-secondary">
                <i className="bi bi-arrow-left me-1" aria-hidden="true" />
                {tr('Back to meetings')}
              </Link>
            </>
          ) : (
            <>
              {actionError ? (
                <div className="alert alert-danger" role="alert">
                  {actionError}
                </div>
              ) : null}
              {notice ? (
                <div className="alert alert-success" role="status">
                  {notice}
                </div>
              ) : null}

              {isProcessing(result.data.meeting.status) ? (
                <div className="alert alert-info d-flex align-items-start gap-2" role="status">
                  <i className="bi bi-hourglass-split mt-1" aria-hidden="true" />
                  <div>
                    {stageLabel(result.data.meeting.stage)
                      ? tr(
                          'Processing is in progress ({{stage}}). Refresh to see the latest state.',
                          {
                            stage: stageLabel(result.data.meeting.stage) ?? '',
                          },
                        )
                      : tr(
                          'Processing is in progress. Refresh to see the latest state.',
                        )}
                  </div>
                </div>
              ) : null}

              <div className="card mb-4">
                <div className="card-header">
                  <h3 className="card-title">{tr('Overview')}</h3>
                  <div className="card-tools">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={result.reload}
                      disabled={busy}
                    >
                      <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                      {tr('Refresh')}
                    </button>
                  </div>
                </div>
                <div className="card-body">
                  <dl className="row mb-0">
                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Status')}</dt>
                    <dd className="col-sm-9">
                      <span className={`badge ${meetingStatus(result.data.meeting.status).badge}`}>
                        {meetingStatus(result.data.meeting.status).label}
                      </span>
                      {stageLabel(result.data.meeting.stage) ? (
                        <span className="ms-2 text-secondary small">
                          {tr('Stage: {{stage}}', {
                            stage: stageLabel(result.data.meeting.stage) ?? '',
                          })}
                        </span>
                      ) : null}
                    </dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Source')}</dt>
                    <dd className="col-sm-9">{sourceLabel(result.data.meeting.source)}</dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Local language')}</dt>
                    <dd className="col-sm-9">{formatLanguage(result.data.meeting.local_language)}</dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Duration')}</dt>
                    <dd className="col-sm-9">
                      {formatDuration(result.data.meeting.audio_duration_ms)}
                    </dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Created')}</dt>
                    <dd className="col-sm-9">{formatDateTime(result.data.meeting.created_at)}</dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Updated')}</dt>
                    <dd className="col-sm-9">{formatDateTime(result.data.meeting.updated_at)}</dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Audio')}</dt>
                    <dd className="col-sm-9">
                      {result.data.meeting.audio_deleted_at
                        ? tr('Deleted {{date}}', {
                            date: formatDateTime(result.data.meeting.audio_deleted_at),
                          })
                        : tr('Retained')}
                    </dd>

                    <dt className="col-sm-3 text-secondary fw-normal">{tr('Meeting ID')}</dt>
                    <dd className="col-sm-9 font-monospace text-break">
                      {result.data.meeting.id}
                    </dd>
                  </dl>

                  {result.data.meeting.error ? (
                    <div className="alert alert-danger mt-3 mb-0" role="alert">
                      <strong>{tr('Processing error:')}</strong> {result.data.meeting.error}
                    </div>
                  ) : null}
                </div>
                <div className="card-footer d-flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    onClick={() => handleExport('markdown')}
                    disabled={busy}
                  >
                    {action === 'markdown' ? (
                      <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" />
                    ) : (
                      <i className="bi bi-file-earmark-text me-1" aria-hidden="true" />
                    )}
                    Markdown
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    onClick={() => handleExport('txt')}
                    disabled={busy}
                  >
                    {action === 'txt' ? (
                      <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" />
                    ) : (
                      <i className="bi bi-filetype-txt me-1" aria-hidden="true" />
                    )}
                    {tr('Plain text')}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-primary"
                    onClick={handleRegenerate}
                    disabled={busy}
                  >
                    {action === 'regenerate' ? (
                      <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" />
                    ) : (
                      <i className="bi bi-arrow-repeat me-1" aria-hidden="true" />
                    )}
                    {tr('Regenerate minutes')}
                  </button>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-danger ms-auto"
                    onClick={handleDelete}
                    disabled={busy}
                  >
                    {action === 'delete' ? (
                      <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" />
                    ) : (
                      <i className="bi bi-trash me-1" aria-hidden="true" />
                    )}
                    {tr('Delete meeting')}
                  </button>
                </div>
              </div>

              <div className="card">
                <div className="card-header p-0 border-bottom-0">
                  <ul className="nav nav-tabs card-header-tabs" role="tablist">
                    <li className="nav-item" role="presentation">
                      <button
                        type="button"
                        className={`nav-link${tab === 'transcript' ? ' active' : ''}`}
                        id="transcript-tab"
                        role="tab"
                        aria-controls="transcript-panel"
                        aria-selected={tab === 'transcript'}
                        onClick={() => setTab('transcript')}
                      >
                        {tr('Transcript')}
                        <span className="badge text-bg-secondary ms-2">
                          {result.data.transcript?.segments.length ?? 0}
                        </span>
                      </button>
                    </li>
                    <li className="nav-item" role="presentation">
                      <button
                        type="button"
                        className={`nav-link${tab === 'minutes' ? ' active' : ''}`}
                        id="minutes-tab"
                        role="tab"
                        aria-controls="minutes-panel"
                        aria-selected={tab === 'minutes'}
                        onClick={() => setTab('minutes')}
                      >
                        {tr('Minutes')}
                      </button>
                    </li>
                  </ul>
                </div>

                <div className="card-body">
                  {tab === 'transcript' ? (
                    <div
                      className="tab-pane fade show active"
                      id="transcript-panel"
                      role="tabpanel"
                      aria-labelledby="transcript-tab"
                      tabIndex={0}
                    >
                      <TranscriptView
                        transcript={result.data.transcript}
                        error={result.data.transcriptError}
                      />
                    </div>
                  ) : (
                    <div
                      className="tab-pane fade show active"
                      id="minutes-panel"
                      role="tabpanel"
                      aria-labelledby="minutes-tab"
                      tabIndex={0}
                    >
                      <MinutesView
                        minutes={result.data.minutes}
                        error={result.data.minutesError}
                      />
                    </div>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}

function TranscriptView({
  transcript,
  error,
}: {
  transcript: Transcript | null;
  error: string | null;
}) {
  if (error) {
    return (
      <div className="alert alert-warning mb-0" role="alert">
        {tr('The transcript could not be loaded: {{error}}', { error })}
      </div>
    );
  }
  if (!transcript || transcript.segments.length === 0) {
    return <EmptyState message="No transcript yet. Process the meeting to produce one." />;
  }

  return (
    <div className="table-responsive">
      <table className="table table-striped align-middle mb-0">
        <thead>
          <tr>
            <th scope="col">{tr('Time')}</th>
            <th scope="col">{tr('Speaker')}</th>
            <th scope="col">{tr('Language')}</th>
            <th scope="col">{tr('Text')}</th>
          </tr>
        </thead>
        <tbody>
          {transcript.segments.map((segment) => {
            const translation = translationStatus(segment.translation_status);
            return (
              <tr key={segment.seq}>
                <td className="text-nowrap font-monospace small">
                  {formatSegmentTime(segment.start_ms)}
                </td>
                <td className="text-nowrap">{segment.speaker ?? '—'}</td>
                <td className="text-nowrap">
                  <div>{formatLanguage(segment.detected_language)}</div>
                  {segment.translated_text ? (
                    <span className={`badge ${translation.badge} mt-1`}>{translation.label}</span>
                  ) : null}
                </td>
                <td>
                  <div className="winnie-pre-line">{segment.text}</div>
                  {segment.translated_text ? (
                    <div className="mt-2 text-secondary winnie-pre-line">
                      <span className="badge text-bg-light text-dark me-2">
                        {tr('Translation')}
                      </span>
                      {segment.translated_text}
                    </div>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MinutesView({ minutes, error }: { minutes: Minutes | null; error: string | null }) {
  if (error) {
    return (
      <div className="alert alert-warning mb-0" role="alert">
        {tr('Minutes are not available yet: {{error}}', { error })}
      </div>
    );
  }
  if (!minutes) {
    return <EmptyState message="No minutes have been generated for this meeting." />;
  }

  return (
    <div>
      <h4 className="h5">{tr('Summary')}</h4>
      <p className="winnie-pre-line">{minutes.summary || tr('No summary was produced.')}</p>

      <h4 className="h5 mt-4">{tr('Topics')}</h4>
      {minutes.topics.length === 0 ? (
        <p className="text-secondary">{tr('No topics were extracted.')}</p>
      ) : (
        <div className="row g-3">
          {minutes.topics.map((topic, index) => (
            <div className="col-md-6" key={`${topic.title}-${index}`}>
              <div className="card h-100 bg-body-tertiary">
                <div className="card-body">
                  <h5 className="h6">{topic.title}</h5>
                  {topic.points.length === 0 ? (
                    <p className="text-secondary mb-0 small">{tr('No points.')}</p>
                  ) : (
                    <ul className="mb-0">
                      {topic.points.map((point, pointIndex) => (
                        <li key={pointIndex}>{point}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <h4 className="h5 mt-4">{tr('Decisions')}</h4>
      {minutes.decisions.length === 0 ? (
        <p className="text-secondary">{tr('No decisions were recorded.')}</p>
      ) : (
        <ul className="list-group">
          {minutes.decisions.map((decision, index) => (
            <li className="list-group-item" key={index}>
              <div>{decision.text}</div>
              {decision.context ? (
                <div className="small text-secondary mt-1">{decision.context}</div>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      <h4 className="h5 mt-4">{tr('Action items')}</h4>
      {minutes.action_items.length === 0 ? (
        <p className="text-secondary">{tr('No action items were recorded.')}</p>
      ) : (
        <div className="table-responsive">
          <table className="table table-sm align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('Action')}</th>
                <th scope="col">{tr('Owner')}</th>
                <th scope="col">{tr('Due')}</th>
                <th scope="col">{tr('Status')}</th>
              </tr>
            </thead>
            <tbody>
              {minutes.action_items.map((item, index) => (
                <tr key={index}>
                  <td>{item.text}</td>
                  <td className="text-nowrap">{item.owner ?? '—'}</td>
                  <td className="text-nowrap">{item.due_date ?? '—'}</td>
                  <td>
                    <span
                      className={`badge ${
                        item.status === 'done'
                          ? 'text-bg-success'
                          : item.status === 'open'
                            ? 'text-bg-secondary'
                            : 'text-bg-info'
                      }`}
                    >
                      {item.status}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="h5 mt-4">{tr('Highlights')}</h4>
      {minutes.highlights.length === 0 ? (
        <p className="text-secondary">{tr('No highlights were recorded.')}</p>
      ) : (
        <ul className="list-group">
          {minutes.highlights.map((highlight, index) => (
            <li className="list-group-item" key={`${highlight.segment_seq}-${index}`}>
              <span className="badge text-bg-light text-dark me-2">
                {tr('Segment {{number}}', { number: highlight.segment_seq })}
              </span>
              {highlight.text}
            </li>
          ))}
        </ul>
      )}

      <p className="small text-secondary mt-4 mb-0">
        {tr('Model {{model}} · prompt {{prompt}} · generated {{date}}', {
          model: minutes.model || '—',
          prompt: minutes.prompt_version,
          date: formatDateTime(minutes.created_at),
        })}
      </p>
    </div>
  );
}
