import { useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import type { ErrorIncident, ErrorIncidentList, ErrorSummary } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

const STATUS_FILTERS = [
  { key: 'open', label: 'Open' },
  { key: 'acknowledged', label: 'Acknowledged' },
  { key: 'ignored', label: 'Ignored' },
  { key: 'resolved', label: 'Resolved' },
  { key: 'all', label: 'All' },
] as const;

const LEVEL_BADGES: Record<string, string> = {
  warning: 'text-bg-warning',
  error: 'text-bg-danger',
  critical: 'text-bg-dark',
};

const STATUS_BADGES: Record<string, string> = {
  open: 'text-bg-danger',
  acknowledged: 'text-bg-info',
  ignored: 'text-bg-secondary',
  resolved: 'text-bg-success',
};

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

interface ErrorsData {
  list: ErrorIncidentList;
  summary: ErrorSummary;
}

/**
 * Everything the program reported, with a read-only diagnosis on demand.
 *
 * The page is a triage surface, not a log viewer: one row per failure shape,
 * ordered by the most recent occurrence, and the detail panel answers "what would
 * I read next" without leaving the console.
 */
export function ErrorsPage() {
  const [status, setStatus] = useState<string>('open');
  const [service, setService] = useState<string>('');
  const [search, setSearch] = useState<string>('');
  const [appliedSearch, setAppliedSearch] = useState<string>('');
  const [revision, setRevision] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [busy, setBusy] = useState<string>('');
  const [actionError, setActionError] = useState<string>('');

  const result = useApi<ErrorsData>(async () => {
    const [list, summary] = await Promise.all([
      api.errors({ status, service, query: appliedSearch, limit: 100 }),
      api.errorSummary(),
    ]);
    return { list, summary };
  }, [status, service, appliedSearch, revision]);

  const data = result.status === 'success' ? result.data : null;
  const items = data?.list.items ?? [];
  const summary = data?.summary ?? null;
  const selected: ErrorIncident | null =
    items.find((item) => item.id === selectedId) ?? null;
  const services = Object.keys(summary?.by_service ?? {}).sort();

  const reload = () => setRevision((value) => value + 1);

  async function act(id: string, action: 'diagnose' | 'status', next?: string) {
    setBusy(`${id}:${action}`);
    setActionError('');
    try {
      if (action === 'diagnose') {
        await api.diagnoseError(id);
      } else if (next) {
        await api.setErrorStatus(id, next);
      }
      reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy('');
    }
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAppliedSearch(search.trim());
  }

  return (
    <>
      <PageHeader title="Errors" />
      <div className="app-content">
        <div className="container-fluid">
          {summary ? (
            <div className="row g-2 mb-3">
              <div className="col-6 col-md-3">
                <div className="small-box text-bg-danger">
                  <div className="inner">
                    <h3>{summary.open}</h3>
                    <p className="mb-0">{tr('Open')}</p>
                  </div>
                  <i className="small-box-icon bi bi-bug" aria-hidden="true" />
                </div>
              </div>
              <div className="col-6 col-md-3">
                <div className="small-box text-bg-warning">
                  <div className="inner">
                    <h3>{summary.last_24h}</h3>
                    <p className="mb-0">{tr('Last 24 hours')}</p>
                  </div>
                  <i className="small-box-icon bi bi-clock-history" aria-hidden="true" />
                </div>
              </div>
              <div className="col-6 col-md-3">
                <div className="small-box text-bg-secondary">
                  <div className="inner">
                    <h3>{summary.total}</h3>
                    <p className="mb-0">{tr('Total collected')}</p>
                  </div>
                  <i className="small-box-icon bi bi-archive" aria-hidden="true" />
                </div>
              </div>
              <div className="col-6 col-md-3">
                <div className="small-box text-bg-info">
                  <div className="inner">
                    <h3>{services.length}</h3>
                    <p className="mb-0">{tr('Services')}</p>
                  </div>
                  <i className="small-box-icon bi bi-hdd-stack" aria-hidden="true" />
                </div>
              </div>
            </div>
          ) : null}

          <div className="card">
            <div className="card-body">
              <div className="d-flex flex-wrap align-items-end gap-2 mb-3">
                <div>
                  <label className="form-label mb-1" htmlFor="error-status">
                    {tr('Status')}
                  </label>
                  <select
                    id="error-status"
                    className="form-select form-select-sm"
                    value={status}
                    onChange={(event) => {
                      setStatus(event.target.value);
                      setSelectedId(null);
                    }}
                  >
                    {STATUS_FILTERS.map((filter) => (
                      <option key={filter.key} value={filter.key}>
                        {tr(filter.label)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="form-label mb-1" htmlFor="error-service">
                    {tr('Service')}
                  </label>
                  <select
                    id="error-service"
                    className="form-select form-select-sm"
                    value={service}
                    onChange={(event) => {
                      setService(event.target.value);
                      setSelectedId(null);
                    }}
                  >
                    <option value="">{tr('All services')}</option>
                    {services.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </div>
                <form className="d-flex align-items-end gap-2" onSubmit={submitSearch}>
                  <div>
                    <label className="form-label mb-1" htmlFor="error-search">
                      {tr('Search messages')}
                    </label>
                    <input
                      id="error-search"
                      className="form-control form-control-sm"
                      style={{ minWidth: '16rem' }}
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder={tr('For example a host or a route')}
                    />
                  </div>
                  <button type="submit" className="btn btn-sm btn-outline-secondary">
                    {tr('Search')}
                  </button>
                </form>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary ms-auto"
                  onClick={reload}
                >
                  <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                  {tr('Refresh')}
                </button>
              </div>

              {result.status === 'loading' ? <LoadingBlock /> : null}
              {result.status === 'error' ? (
                <ErrorBlock error={result.error} onRetry={reload} />
              ) : null}

              {data ? (
                items.length === 0 ? (
                  <EmptyState message="No errors match this filter." />
                ) : (
                  <div className="table-responsive">
                    <table className="table table-sm align-middle mb-0">
                      <thead>
                        <tr>
                          <th scope="col">{tr('Last seen')}</th>
                          <th scope="col">{tr('Level')}</th>
                          <th scope="col">{tr('Service')}</th>
                          <th scope="col">{tr('Message')}</th>
                          <th scope="col" className="text-end">
                            {tr('Count')}
                          </th>
                          <th scope="col">{tr('Status')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {items.map((item) => (
                          <tr
                            key={item.id}
                            role="button"
                            tabIndex={0}
                            className={item.id === selectedId ? 'table-active' : undefined}
                            style={{ cursor: 'pointer' }}
                            onClick={() => setSelectedId(item.id)}
                            onKeyDown={(event) => {
                              if (event.key === 'Enter' || event.key === ' ') {
                                event.preventDefault();
                                setSelectedId(item.id);
                              }
                            }}
                          >
                            <td className="text-nowrap small">
                              {formatDateTime(item.last_seen)}
                            </td>
                            <td>
                              <span className={`badge ${LEVEL_BADGES[item.level] ?? 'text-bg-secondary'}`}>
                                {item.level}
                              </span>
                            </td>
                            <td className="small">{item.service}</td>
                            <td>
                              <div className="text-truncate" style={{ maxWidth: '34rem' }}>
                                {item.message}
                              </div>
                              {item.location ? (
                                <div className="text-secondary small text-truncate">
                                  {item.location}
                                </div>
                              ) : null}
                            </td>
                            <td className="text-end small">{item.occurrences}</td>
                            <td>
                              <span className={`badge ${STATUS_BADGES[item.status] ?? 'text-bg-secondary'}`}>
                                {item.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : null}
              {data && data.list.total > items.length ? (
                <p className="text-secondary small mt-2 mb-0">
                  {tr('Showing {{shown}} of {{total}} incidents.', {
                    shown: items.length,
                    total: data.list.total,
                  })}
                </p>
              ) : null}
            </div>
          </div>

          {selected ? (
            <div className="card mt-3">
              <div className="card-header d-flex flex-wrap align-items-center gap-2">
                <span className={`badge ${LEVEL_BADGES[selected.level] ?? 'text-bg-secondary'}`}>
                  {selected.level}
                </span>
                <strong className="me-auto text-break">{selected.message}</strong>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={busy !== ''}
                  onClick={() => act(selected.id, 'diagnose')}
                >
                  {busy === `${selected.id}:diagnose` ? tr('Diagnosing…') : tr('Diagnose')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  disabled={busy !== ''}
                  onClick={() => act(selected.id, 'status', 'acknowledged')}
                >
                  {tr('Acknowledge')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  disabled={busy !== ''}
                  onClick={() => act(selected.id, 'status', 'ignored')}
                >
                  {tr('Ignore')}
                </button>
                {selected.status === 'open' ? (
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-success"
                    disabled={busy !== ''}
                    onClick={() => act(selected.id, 'status', 'resolved')}
                  >
                    {tr('Resolve')}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary"
                    disabled={busy !== ''}
                    onClick={() => act(selected.id, 'status', 'open')}
                  >
                    {tr('Reopen')}
                  </button>
                )}
              </div>
              <div className="card-body">
                {actionError ? (
                  <div className="alert alert-danger py-2" role="alert">
                    {actionError}
                  </div>
                ) : null}

                <div className="row g-3">
                  <div className="col-md-6">
                    <dl className="row mb-0 small">
                      <dt className="col-4">{tr('Kind')}</dt>
                      <dd className="col-8">{selected.kind}</dd>
                      <dt className="col-4">{tr('Source')}</dt>
                      <dd className="col-8">{selected.source}</dd>
                      <dt className="col-4">{tr('Service')}</dt>
                      <dd className="col-8">{selected.service}</dd>
                      <dt className="col-4">{tr('Location')}</dt>
                      <dd className="col-8 text-break">{selected.location || '—'}</dd>
                      <dt className="col-4">{tr('Occurrences')}</dt>
                      <dd className="col-8">{selected.occurrences}</dd>
                    </dl>
                  </div>
                  <div className="col-md-6">
                    <dl className="row mb-0 small">
                      <dt className="col-4">{tr('First seen')}</dt>
                      <dd className="col-8">{formatDateTime(selected.first_seen)}</dd>
                      <dt className="col-4">{tr('Last seen')}</dt>
                      <dd className="col-8">{formatDateTime(selected.last_seen)}</dd>
                      <dt className="col-4">{tr('Status')}</dt>
                      <dd className="col-8">{selected.status}</dd>
                      <dt className="col-4">{tr('Fingerprint')}</dt>
                      <dd className="col-8 text-break">
                        <code>{selected.fingerprint}</code>
                      </dd>
                    </dl>
                  </div>
                </div>

                {selected.diagnosis ? (
                  <div className="mt-3">
                    <h2 className="h6">{tr('Diagnosis')}</h2>
                    {selected.diagnosis.answer ? (
                      <>
                        <p className="mb-2">{selected.diagnosis.answer.cause || tr('No cause given.')}</p>
                        <dl className="row mb-2 small">
                          <dt className="col-sm-2">{tr('Component')}</dt>
                          <dd className="col-sm-10">
                            {selected.diagnosis.answer.component || '—'}
                          </dd>
                          <dt className="col-sm-2">{tr('File')}</dt>
                          <dd className="col-sm-10 text-break">
                            {selected.diagnosis.answer.file || '—'}
                            {selected.diagnosis.answer.line
                              ? `:${selected.diagnosis.answer.line}`
                              : ''}
                          </dd>
                          <dt className="col-sm-2">{tr('Confidence')}</dt>
                          <dd className="col-sm-10">
                            {Math.round(selected.diagnosis.answer.confidence * 100)}%
                          </dd>
                          <dt className="col-sm-2">{tr('Next steps')}</dt>
                          <dd className="col-sm-10">
                            {selected.diagnosis.answer.next_steps.length > 0 ? (
                              <ol className="mb-0 ps-3">
                                {selected.diagnosis.answer.next_steps.map((step) => (
                                  <li key={step}>
                                    <code>{step}</code>
                                  </li>
                                ))}
                              </ol>
                            ) : (
                              '—'
                            )}
                          </dd>
                          {selected.diagnosis.answer.notes ? (
                            <>
                              <dt className="col-sm-2">{tr('Notes')}</dt>
                              <dd className="col-sm-10">{selected.diagnosis.answer.notes}</dd>
                            </>
                          ) : null}
                        </dl>
                      </>
                    ) : (
                      <p className="text-secondary small">
                        {tr('The model answered without a structured diagnosis.')}
                      </p>
                    )}
                    {selected.diagnosis.answer ? null : (
                      <pre className="small bg-body-tertiary p-2 rounded mb-2">
                        {selected.diagnosis.raw}
                      </pre>
                    )}
                    <p className="text-secondary small mb-0">
                      {tr('Model {{model}}, {{when}}', {
                        model: selected.diagnosis.model,
                        when: formatDateTime(selected.diagnosis.at),
                      })}
                    </p>
                  </div>
                ) : null}

                {selected.traceback ? (
                  <div className="mt-3">
                    <h2 className="h6">{tr('Traceback')}</h2>
                    <pre
                      className="small bg-body-tertiary p-2 rounded mb-0"
                      style={{ maxHeight: '20rem', overflow: 'auto' }}
                    >
                      {selected.traceback}
                    </pre>
                  </div>
                ) : null}

                <div className="mt-3">
                  <h2 className="h6">{tr('Context')}</h2>
                  <pre
                    className="small bg-body-tertiary p-2 rounded mb-0"
                    style={{ maxHeight: '16rem', overflow: 'auto' }}
                  >
                    {JSON.stringify(selected.context ?? {}, null, 2)}
                  </pre>
                </div>
              </div>
            </div>
          ) : data ? (
            <p className="text-secondary small mt-3">{tr('Select an error to see its details.')}</p>
          ) : null}
        </div>
      </div>
    </>
  );
}
