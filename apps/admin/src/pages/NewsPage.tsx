import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api, ApiError } from '../api/client';
import type {
  NewsDigestFormat,
  NewsDigestResponse,
  NewsDraftDetail,
  NewsRun,
  NewsRunDrafts,
  NewsSourceList,
  NewsStatus,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { Markdown } from '../components/Markdown';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

interface ModelFeatureReadiness {
  feature: string;
  ready: boolean;
  error?: string;
}

/**
 * The API now reports why a binding cannot be used (no binding, removed
 * supplier, missing supplier key). Kept local until the shared type catches up;
 * the field is additive and older responses omit it.
 */
interface ModelReadiness {
  ready: boolean;
  error?: string;
  features?: ModelFeatureReadiness[];
}

interface NewsData {
  status: NewsStatus & { model?: ModelReadiness | null };
  runs: NewsRun[];
  sources: NewsSourceList;
}

const RUN_BADGE: Record<string, string> = {
  running: 'text-bg-warning',
  done: 'text-bg-success',
  partial: 'text-bg-info',
  failed: 'text-bg-danger',
};

const STAGE_BADGE: Record<string, string> = {
  pending: 'text-bg-light text-dark',
  running: 'text-bg-warning',
  ok: 'text-bg-success',
  skipped: 'text-bg-secondary',
  failed: 'text-bg-danger',
};

const REVIEW_BADGE: Record<string, string> = {
  passed: 'text-bg-success',
  retry: 'text-bg-warning',
  needs_human: 'text-bg-danger',
};

const SOURCE_BADGE: Record<string, string> = {
  ok: 'text-bg-success',
  error: 'text-bg-danger',
  unknown: 'text-bg-secondary',
};

const DIGEST_FORMATS: NewsDigestFormat[] = ['standard', 'brief', 'table'];

const DIGEST_LABEL: Record<NewsDigestFormat, string> = {
  standard: 'Standard',
  brief: 'Morning / evening',
  table: 'Table',
};

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function stat(run: NewsRun, key: string): string {
  const value = run.stats?.[key];
  return value === undefined || value === null ? '—' : String(value);
}

export function NewsPage() {
  const result = useApi<NewsData>(async () => {
    const [status, runs, sources] = await Promise.all([
      api.newsStatus(),
      api.newsRuns(20),
      api.newsSources(),
    ]);
    return { status, runs, sources };
  }, []);

  const [selectedRunId, setSelectedRunId] = useState<string>('');
  const [drafts, setDrafts] = useState<NewsRunDrafts | null>(null);
  const [detail, setDetail] = useState<NewsDraftDetail | null>(null);
  const [markdown, setMarkdown] = useState<string>('');
  const [draftText, setDraftText] = useState<string>('');
  const [digest, setDigest] = useState<NewsDigestResponse | null>(null);
  const [digestFormat, setDigestFormat] = useState<NewsDigestFormat>('standard');
  const [digestNote, setDigestNote] = useState<string>('');
  const [digestBusy, setDigestBusy] = useState(false);
  const digestTicket = useRef(0);
  const [message, setMessage] = useState<{ kind: 'success' | 'danger'; text: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const loaded = result.status === 'success' ? result.data : null;
  const runs = loaded?.runs ?? [];
  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId],
  );
  const running = runs.some((run) => run.status === 'running');
  const modelError =
    loaded?.status.model?.features?.find((feature) => feature.error)?.error ??
    loaded?.status.model?.error ??
    '';

  // A run continues in the API process; poll while work is in flight and while
  // the page is open so stage progress is visible without a manual reload.
  useEffect(() => {
    if (!running) {
      return;
    }
    const timer = window.setInterval(() => result.reload(), 3000);
    return () => window.clearInterval(timer);
  }, [running, result.reload]);

  // Load the draft list whenever the selected run changes or its status moves.
  useEffect(() => {
    if (!selectedRun) {
      setDrafts(null);
      return;
    }
    let alive = true;
    api.newsDrafts(selectedRun.id).then(
      (value) => {
        if (alive) {
          setDrafts(value);
        }
      },
      (error: unknown) => {
        if (alive) {
          setMessage({ kind: 'danger', text: describe(error) });
        }
      },
    );
    return () => {
      alive = false;
    };
  }, [selectedRun?.id, selectedRun?.updated_at]);

  // The digest is stored with the run and rendered on the server in three
  // formats, so switching a format refetches rather than re-rendering locally.
  const loadDigest = useCallback(
    async (runId: string, format: NewsDigestFormat) => {
      const ticket = ++digestTicket.current;
      setDigestBusy(true);
      try {
        const value = await api.newsDigest(runId, format);
        if (ticket !== digestTicket.current) {
          return;
        }
        setDigest(value);
        setDigestNote('');
      } catch (error) {
        if (ticket !== digestTicket.current) {
          return;
        }
        setDigest(null);
        setDigestNote(
          error instanceof ApiError && error.status === 404
            ? tr('No digest for this run.')
            : describe(error),
        );
      } finally {
        if (ticket === digestTicket.current) {
          setDigestBusy(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    if (!selectedRun) {
      setDigest(null);
      setDigestNote('');
      return;
    }
    void loadDigest(selectedRun.id, digestFormat);
  }, [selectedRun?.id, selectedRun?.updated_at, digestFormat, loadDigest]);

  async function copy(text: string, done: string): Promise<void> {
    if (!text) {
      return;
    }
    if (!navigator.clipboard) {
      setMessage({
        kind: 'danger',
        text: tr('Clipboard is unavailable; select the text and copy it.'),
      });
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setMessage({ kind: 'success', text: done });
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    }
  }

  async function startRun(): Promise<void> {
    setBusy(true);
    setMessage(null);
    try {
      const run = await api.startNewsRun();
      setMessage({ kind: 'success', text: tr('Started run {{id}}.', { id: run.id }) });
      setSelectedRunId(run.id);
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(false);
    }
  }

  async function openDraft(eventId: string): Promise<void> {
    if (!selectedRun) {
      return;
    }
    setMessage(null);
    try {
      const [draft, text, markdownText] = await Promise.all([
        api.newsDraft(selectedRun.id, eventId),
        api.newsDraftText(selectedRun.id, eventId),
        api.newsDraftMarkdown(selectedRun.id, eventId),
      ]);
      setDetail(draft);
      setDraftText(text);
      setMarkdown(markdownText);
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    }
  }

  async function regenerate(): Promise<void> {
    if (!selectedRun || !detail) {
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const updated = await api.regenerateNewsDraft(
        selectedRun.id,
        detail.draft.event_id,
      );
      setDetail(updated);
      const [text, markdownText] = await Promise.all([
        api.newsDraftText(selectedRun.id, detail.draft.event_id),
        api.newsDraftMarkdown(selectedRun.id, detail.draft.event_id),
      ]);
      setDraftText(text);
      setMarkdown(markdownText);
      void loadDigest(selectedRun.id, digestFormat);
      setMessage({
        kind: 'success',
        text: tr('Regenerated {{id}} (revision {{revision}}).', {
          id: detail.draft.event_id,
          revision: updated.draft.revision,
        }),
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(false);
    }
  }

  // The detail card renders below the tables; bring it into view so opening a
  // draft while scrolled down the list does not look like nothing happened.
  useEffect(() => {
    if (!detail) {
      return;
    }
    document
      .getElementById(`draft-${detail.draft.event_id}`)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [detail]);

  const reviews = new Map((drafts?.reviews ?? []).map((review) => [review.event_id, review]));

  return (
    <>
      <PageHeader title="News" />
      <div className="app-content">
        <div className="container-fluid">
          {result.status === 'loading' ? (
            <LoadingBlock label="Loading news pipeline…" />
          ) : result.status === 'error' ? (
            <ErrorBlock error={result.error} onRetry={result.reload} />
          ) : loaded ? (
            <>
              {message ? (
                <div className={`alert alert-${message.kind}`} role="alert">
                  {message.text}
                </div>
              ) : null}

              {!loaded.status.ready ? (
                <div className="alert alert-warning" role="alert">
                  <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
                  {tr('News cannot run yet. Bind')} <code>news.extract</code>,{' '}
                  <code>news.write</code> {tr('and')} <code>news.review</code>{' '}
                  {tr('to a supplier on the')}{' '}
                  <a href="/ai">{tr('AI providers')}</a>{' '}
                  {tr('page and set that supplier’s API key.')}
                  {modelError ? (
                    <p className="mb-0 mt-1 small font-monospace">{modelError}</p>
                  ) : null}
                </div>
              ) : null}

              <div className="row g-3 mb-3">
                <div className="col-sm-6 col-xl-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{loaded.status.events}</h3>
                      <p>{tr('Events stored')}</p>
                    </div>
                    <i className="small-box-icon bi bi-collection" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-sm-6 col-xl-3">
                  <div className="small-box text-bg-info">
                    <div className="inner">
                      <h3>{loaded.status.runs}</h3>
                      <p>{tr('Runs kept')}</p>
                    </div>
                    <i className="small-box-icon bi bi-clock-history" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-sm-6 col-xl-3">
                  <div className="small-box text-bg-success">
                    <div className="inner">
                      <h3 className="fs-5">{loaded.status.llm || tr('none')}</h3>
                      <p>{tr('Model binding')}</p>
                    </div>
                    <i className="small-box-icon bi bi-cpu" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-sm-6 col-xl-3">
                  <div className="small-box text-bg-secondary">
                    <div className="inner">
                      <h3 className="fs-5">{loaded.status.search || tr('disabled')}</h3>
                      <p>{tr('Search provider')}</p>
                    </div>
                    <i className="small-box-icon bi bi-search" aria-hidden="true" />
                  </div>
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header d-flex align-items-center justify-content-between">
                  <h2 className="card-title mb-0">{tr('Runs')}</h2>
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => void startRun()}
                    disabled={busy || !loaded.status.ready || loaded.status.active_run !== null}
                  >
                    <i className="bi bi-play-fill me-1" aria-hidden="true" />
                    {loaded.status.active_run ? tr('Run in progress') : tr('New run')}
                  </button>
                </div>
                <div className="card-body p-0">
                  {runs.length === 0 ? (
                    <EmptyState message="No runs yet. A run collects the configured sources, groups reports into events, then writes and reviews one draft per event." />
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-sm table-hover mb-0 align-middle">
                        <thead>
                          <tr>
                            <th scope="col">{tr('Run')}</th>
                            <th scope="col">{tr('Status')}</th>
                            <th scope="col">{tr('Started')}</th>
                            <th scope="col">{tr('Materials')}</th>
                            <th scope="col">{tr('Events')}</th>
                            <th scope="col">{tr('Drafts')}</th>
                            <th scope="col">{tr('Passed')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {runs.map((run) => (
                            <tr
                              key={run.id}
                              onClick={() => {
                                setSelectedRunId(run.id);
                                setDetail(null);
                                setMarkdown('');
                                setDraftText('');
                              }}
                              style={{ cursor: 'pointer' }}
                              className={selectedRun?.id === run.id ? 'table-active' : undefined}
                            >
                              <td className="font-monospace small">{run.id}</td>
                              <td>
                                <span className={`badge ${RUN_BADGE[run.status] ?? 'text-bg-secondary'}`}>
                                  {run.status}
                                </span>
                              </td>
                              <td className="small">{formatDateTime(run.created_at)}</td>
                              <td>{stat(run, 'materials')}</td>
                              <td>{stat(run, 'events')}</td>
                              <td>{stat(run, 'drafts')}</td>
                              <td>{stat(run, 'passed')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header d-flex align-items-center justify-content-between">
                  <h2 className="card-title mb-0">{tr('Sources')}</h2>
                  <span className="text-secondary small">
                    {tr('updated {{date}}', {
                      date: formatDateTime(loaded.sources.updated_at),
                    })}
                  </span>
                </div>
                <div className="card-body p-0">
                  {loaded.sources.sources.length === 0 ? (
                    <EmptyState message="No source has been fetched yet. Start a run to record their health." />
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-sm mb-0 align-middle">
                        <thead>
                          <tr>
                            <th scope="col">{tr('Source')}</th>
                            <th scope="col">{tr('Status')}</th>
                            <th scope="col">{tr('Items')}</th>
                            <th scope="col">{tr('Last success')}</th>
                            <th scope="col">{tr('Failures')}</th>
                            <th scope="col">{tr('Last error')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {loaded.sources.sources.map((source) => (
                            <tr key={source.key}>
                              <td>
                                <div className="fw-medium">{source.name}</div>
                                <div className="text-secondary small font-monospace">
                                  {source.key}
                                </div>
                              </td>
                              <td>
                                <span className={`badge ${SOURCE_BADGE[source.last_status] ?? 'text-bg-secondary'}`}>
                                  {source.last_status}
                                </span>
                              </td>
                              <td>{source.last_count}</td>
                              <td className="small">{formatDateTime(source.last_success)}</td>
                              <td>{source.consecutive_failures}</td>
                              <td className="small text-break" style={{ maxWidth: '28rem' }}>
                                {source.last_error || '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

              {selectedRun ? (
                <div className="card mb-3">
                  <div className="card-header d-flex flex-wrap align-items-center justify-content-between gap-2">
                    <h2 className="card-title mb-0">
                      {tr('Run {{id}}', { id: selectedRun.id })}
                    </h2>
                    <span className={`badge ${RUN_BADGE[selectedRun.status] ?? 'text-bg-secondary'}`}>
                      {selectedRun.status}
                    </span>
                  </div>
                  <div className="card-body">
                    {selectedRun.error ? (
                      <div className="alert alert-danger" role="alert">
                        {selectedRun.error}
                      </div>
                    ) : null}
                    <ul className="list-inline mb-3">
                      {selectedRun.stages.map((stage) => (
                        <li className="list-inline-item mb-1" key={stage.name}>
                          <span className={`badge ${STAGE_BADGE[stage.status] ?? 'text-bg-secondary'}`}>
                            {stage.name}
                            {stage.count !== null ? ` · ${stage.count}` : ''}
                          </span>
                          {stage.detail ? (
                            <span className="text-secondary small ms-1">{stage.detail}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>

                    {!drafts || drafts.drafts.length === 0 ? (
                      <EmptyState message="No drafts in this run. Cleaning may have dropped every item, or the articles were already part of existing events." />
                    ) : (
                      <div className="table-responsive">
                        <table className="table table-sm align-middle mb-0">
                          <thead>
                            <tr>
                              <th scope="col">{tr('Draft')}</th>
                              <th scope="col">{tr('Revision')}</th>
                              <th scope="col">{tr('Sources')}</th>
                              <th scope="col">{tr('Review')}</th>
                              <th scope="col">{tr('Score')}</th>
                              <th scope="col" />
                            </tr>
                          </thead>
                          <tbody>
                            {drafts.drafts.map((entry) => {
                              const review = reviews.get(entry.event_id);
                              return (
                                <tr key={entry.event_id}>
                                  <td>
                                    <div className="fw-medium">{entry.title}</div>
                                    <div className="text-secondary small font-monospace">
                                      {entry.event_id}
                                    </div>
                                  </td>
                                  <td>{entry.revision}</td>
                                  <td>{entry.sources}</td>
                                  <td>
                                    {review ? (
                                      <span className={`badge ${REVIEW_BADGE[review.status] ?? 'text-bg-secondary'}`}>
                                        {review.status}
                                      </span>
                                    ) : (
                                      '—'
                                    )}
                                  </td>
                                  <td>{review ? review.score.toFixed(2) : '—'}</td>
                                  <td className="text-end">
                                    <button
                                      type="button"
                                      className="btn btn-sm btn-outline-primary"
                                      onClick={() => void openDraft(entry.event_id)}
                                    >
                                      {tr('Open')}
                                    </button>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              ) : null}

              {selectedRun ? (
                <div className="card mb-3">
                  <div className="card-header d-flex flex-wrap align-items-center justify-content-between gap-2">
                    <h2 className="card-title mb-0">{tr('Digest')}</h2>
                    <div className="d-flex flex-wrap align-items-center gap-2">
                      <div
                        className="btn-group btn-group-sm"
                        role="group"
                        aria-label={tr('Digest format')}
                      >
                        {DIGEST_FORMATS.map((format) => (
                          <button
                            key={format}
                            type="button"
                            className={`btn ${
                              digestFormat === format
                                ? 'btn-primary'
                                : 'btn-outline-primary'
                            }`}
                            aria-pressed={digestFormat === format}
                            onClick={() => setDigestFormat(format)}
                          >
                            {tr(DIGEST_LABEL[format])}
                          </button>
                        ))}
                      </div>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        onClick={() =>
                          void copy(
                            digest?.text ?? '',
                            tr('Copied the digest to the clipboard.'),
                          )
                        }
                        disabled={!digest}
                      >
                        <i className="bi bi-clipboard me-1" aria-hidden="true" />
                        {tr('Copy Markdown')}
                      </button>
                    </div>
                  </div>
                  <div className="card-body">
                    {digestBusy ? (
                      <p className="text-secondary mb-0">{tr('Loading digest…')}</p>
                    ) : digest ? (
                      <>
                        <p className="text-secondary small mb-2">
                          {tr('{{items}} items · window {{days}} days', {
                            items: digest.digest.count,
                            days: digest.digest.window_days,
                          })}
                        </p>
                        <Markdown source={digest.text} />
                      </>
                    ) : (
                      <p className="text-secondary mb-0">
                        {digestNote || tr('No digest for this run.')}
                      </p>
                    )}
                  </div>
                </div>
              ) : null}

              {detail ? (
                <div className="card">
                  <div
                    className="card-header d-flex flex-wrap align-items-center justify-content-between gap-2"
                    id={`draft-${detail.draft.event_id}`}
                  >
                    <h2 className="card-title mb-0">{detail.draft.title}</h2>
                    <div className="d-flex gap-2">
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-primary"
                        onClick={() => void regenerate()}
                        disabled={busy || running}
                        title={
                          running
                            ? tr('Wait for the active run to finish')
                            : tr(
                                'Rewrite this draft from the run material and review it again',
                              )
                        }
                      >
                        <i className="bi bi-arrow-repeat me-1" aria-hidden="true" />
                        {tr('Regenerate')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        onClick={() => {
                          setDetail(null);
                          setMarkdown('');
                          setDraftText('');
                        }}
                      >
                        {tr('Close')}
                      </button>
                    </div>
                  </div>
                  <div className="card-body">
                    {detail.review ? (
                      <div
                        className={`alert ${
                          detail.review.passed ? 'alert-success' : 'alert-warning'
                        }`}
                        role="status"
                      >
                        <p className="mb-1">
                          <strong>{tr('Review:')}</strong> {detail.review.status} ·{' '}
                          {tr('score {{score}}', {
                            score: detail.review.score.toFixed(2),
                          })}{' '}
                          ·{' '}
                          {tr('retry {{retry}}/{{max}}', {
                            retry: detail.review.retry,
                            max: detail.review.max_retries,
                          })}
                        </p>
                        {detail.review.problems.length > 0 ? (
                          <ul className="mb-1 small">
                            {detail.review.problems.map((problem, index) => (
                              <li key={`${problem.type}-${index}`}>
                                <span className="badge text-bg-secondary me-1">
                                  {problem.severity}
                                </span>
                                {problem.type}: {problem.detail}
                              </li>
                            ))}
                          </ul>
                        ) : null}
                        {detail.review.feedback ? (
                          <pre className="small mb-0" style={{ whiteSpace: 'pre-wrap' }}>
                            {detail.review.feedback}
                          </pre>
                        ) : null}
                      </div>
                    ) : null}
                    <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-primary"
                        onClick={() =>
                          void copy(
                            draftText,
                            tr('Copied the draft to the clipboard.'),
                          )
                        }
                        disabled={!draftText}
                      >
                        <i className="bi bi-clipboard me-1" aria-hidden="true" />
                        {tr('Copy draft')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        onClick={() =>
                          void copy(
                            markdown,
                            tr('Copied the Markdown to the clipboard.'),
                          )
                        }
                        disabled={!markdown}
                      >
                        <i className="bi bi-markdown me-1" aria-hidden="true" />
                        {tr('Copy Markdown')}
                      </button>
                      <span className="text-secondary small">
                        {tr('The draft is the plugin’s title / content / tags output.')}
                      </span>
                    </div>
                    <pre
                      className="mb-0"
                      style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                    >
                      {draftText}
                    </pre>
                  </div>
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
