import { Fragment, useCallback, useEffect, useState } from 'react';

import { api, ApiError } from '../api/client';
import type {
  UsageBalanceList,
  UsageOverview,
  UsageRecord,
  UsageTotals,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

const PERIODS = [
  { days: 1, label: '24 hours' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
];

const PAGE_SIZE = 25;

const CAPABILITY_LABELS: Record<string, string> = {
  'assistant.chat': 'Assistant chat',
  'meetings.translate': 'Meeting translation',
  'meetings.summarize': 'Meeting minutes',
  gateway: 'Gateway proxy',
};

const STATUS_LABELS: Record<string, string> = {
  ok: 'OK',
  error: 'Failed',
};

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function capabilityLabel(key: string): string {
  return CAPABILITY_LABELS[key] ?? key;
}

function formatNumber(value: number): string {
  return value.toLocaleString();
}

function formatCompact(value: number): string {
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value);
}

/** Tokens are `null` when the provider did not report them, not zero. */
function formatTokens(value: number | null): string {
  return value === null ? '—' : value.toLocaleString();
}

function formatCost(micros: number | null, currency: string): string {
  if (micros === null) {
    return '—';
  }
  const value = micros / 1_000_000;
  const digits = value === 0 ? 2 : value < 0.01 ? 6 : value < 1 ? 4 : 2;
  return `${value.toFixed(digits)} ${currency}`;
}

function formatPrice(value: number | null): string {
  return value === null ? '—' : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function formatLatency(milliseconds: number | null): string {
  if (milliseconds === null) {
    return '—';
  }
  if (milliseconds < 1000) {
    return `${milliseconds} ms`;
  }
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

function formatDay(value: string): string {
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

function pricedRequests(totals: UsageTotals): number {
  return totals.provider_priced_requests + totals.estimated_requests;
}


function statusBadge(status: string) {
  return STATUS_LABELS[status] === 'Failed' ? (
    <span className="badge text-bg-danger">Failed</span>
  ) : (
    <span className="badge text-bg-success">OK</span>
  );
}

function Metric({
  value,
  label,
  icon,
  tone,
  footer,
}: {
  value: string;
  label: string;
  icon: string;
  tone: string;
  footer: string;
}) {
  return (
    <div className={`small-box ${tone}`}>
      <div className="inner">
        <h3>{value}</h3>
        <p>{label}</p>
      </div>
      <i className={`small-box-icon bi ${icon}`} aria-hidden="true" />
      <span className="small-box-footer">{footer}</span>
    </div>
  );
}

/** A row with no priced call shows a dash instead of a misleading zero. */
function formatRowCost(row: UsageTotals, currency: string): string {
  return pricedRequests(row) === 0 ? '—' : formatCost(row.cost_micros, currency);
}

function hasDetails(record: UsageRecord): boolean {
  return Boolean(record.error) || Object.keys(record.detail ?? {}).length > 0;
}

/** One row below the call, so opening it cannot squeeze the table's columns. */
function RecordDetailRow({ record, columns }: { record: UsageRecord; columns: number }) {
  return (
    <tr className="table-light">
      <td colSpan={columns}>
        <div className="small d-flex flex-wrap gap-4">
          <div>
            <span className="text-secondary">Cost: </span>
            {record.cost_source} — {record.cost_reason}
          </div>
          {record.error ? (
            <div className="text-danger text-break">
              <span className="text-secondary">Error: </span>
              {record.error}
            </div>
          ) : null}
          {Object.keys(record.detail ?? {}).length > 0 ? (
            <div>
              <span className="text-secondary">Detail: </span>
              <code>{JSON.stringify(record.detail)}</code>
            </div>
          ) : null}
        </div>
      </td>
    </tr>
  );
}

export function UsagePage() {
  const [days, setDays] = useState(7);
  const [capability, setCapability] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [expanded, setExpanded] = useState<string | null>(null);

  const overview = useApi(() => api.usageOverview(days), [days]);
  const records = useApi(
    () =>
      api.usageRecords({
        days,
        limit: PAGE_SIZE,
        offset,
        capability: capability || undefined,
        status: status || undefined,
      }),
    [days, capability, status, offset],
  );

  const [balances, setBalances] = useState<UsageBalanceList | null>(null);
  const [balanceError, setBalanceError] = useState<string | null>(null);
  const [balanceBusy, setBalanceBusy] = useState(false);

  const loadBalances = useCallback(async () => {
    setBalanceError(null);
    try {
      setBalances(await api.usageBalances());
    } catch (error) {
      setBalances(null);
      setBalanceError(describe(error));
    }
  }, []);

  useEffect(() => {
    void loadBalances();
  }, [loadBalances]);

  const refreshBalances = async () => {
    setBalanceBusy(true);
    setBalanceError(null);
    try {
      setBalances(await api.refreshUsageBalances());
    } catch (error) {
      setBalanceError(describe(error));
    } finally {
      setBalanceBusy(false);
    }
  };

  const choosePeriod = (value: number) => {
    setDays(value);
    setOffset(0);
  };

  const changeFilter = (update: () => void) => {
    update();
    setOffset(0);
  };

  const data: UsageOverview | null =
    overview.status === 'success' ? overview.data : null;
  const totals = data?.totals ?? null;
  const page = records.status === 'success' ? records.data : null;

  const capabilityOptions = Array.from(
    new Set([
      ...Object.keys(CAPABILITY_LABELS),
      ...(data?.by_capability.map((row) => row.capability) ?? []),
    ]),
  );

  const usageDisabled =
    overview.status === 'error' &&
    overview.error instanceof ApiError &&
    overview.error.status === 503;

  return (
    <>
      <PageHeader title="LLM usage" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
            <div className="btn-group" role="group" aria-label="Usage period">
              {PERIODS.map((period) => (
                <button
                  type="button"
                  key={period.days}
                  className={`btn btn-sm ${
                    days === period.days ? 'btn-primary' : 'btn-outline-primary'
                  }`}
                  aria-pressed={days === period.days}
                  onClick={() => choosePeriod(period.days)}
                >
                  {period.label}
                </button>
              ))}
            </div>
            {data ? (
              <span className="small text-secondary">
                {formatDateTime(data.period.from)} → {formatDateTime(data.period.to)}
              </span>
            ) : null}
            <button
              type="button"
              className="btn btn-sm btn-outline-secondary ms-auto"
              onClick={() => {
                overview.reload();
                records.reload();
                void loadBalances();
              }}
            >
              <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
              Refresh
            </button>
          </div>

          {overview.status === 'loading' ? (
            <LoadingBlock label="Loading recorded usage…" />
          ) : overview.status === 'error' ? (
            <>
              <ErrorBlock error={overview.error} onRetry={overview.reload} />
              {usageDisabled ? (
                <div className="alert alert-secondary" role="note">
                  Usage recording is off. Set <code>usage.enabled: true</code> in
                  <code> apps/api/config.yaml</code> and restart the API; calls made
                  while it is off cannot be recovered.
                </div>
              ) : (
                <div className="alert alert-secondary" role="note">
                  The console reads usage from the Winnie API. Check that the API is
                  running and that <code>usage.enabled</code> is true in
                  <code> apps/api/config.yaml</code>.
                </div>
              )}
            </>
          ) : !data || !totals ? (
            <EmptyState message="The usage endpoint returned no report." />
          ) : (
            <>
              <div className="row g-3 mb-3">
                <div className="col-6 col-xl-3">
                  <Metric
                    value={formatNumber(totals.requests)}
                    label="Model calls"
                    icon="bi-activity"
                    tone="text-bg-primary"
                    footer={`${formatNumber(totals.errors)} failed`}
                  />
                </div>
                <div className="col-6 col-xl-3">
                  <Metric
                    value={formatCompact(totals.total_tokens)}
                    label="Tokens"
                    icon="bi-123"
                    tone="text-bg-info"
                    footer={`in ${formatCompact(totals.input_tokens)} · out ${formatCompact(
                      totals.output_tokens,
                    )}`}
                  />
                </div>
                <div className="col-6 col-xl-3">
                  <Metric
                    value={
                      pricedRequests(totals) === 0
                        ? '—'
                        : formatCost(totals.cost_micros, data.currency)
                    }
                    label="Estimated cost"
                    icon="bi-coin"
                    tone="text-bg-success"
                    footer={[
                      `${formatNumber(pricedRequests(totals))} priced`,
                      totals.unpriced_requests
                        ? `${formatNumber(totals.unpriced_requests)} unpriced`
                        : '',
                      totals.unreported_requests
                        ? `${formatNumber(totals.unreported_requests)} no usage`
                        : '',
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  />
                </div>
                <div className="col-6 col-xl-3">
                  <Metric
                    value={formatCompact(totals.cached_input_tokens)}
                    label="Cached input"
                    icon="bi-database-check"
                    tone="text-bg-warning"
                    footer={`reasoning ${formatCompact(
                      totals.reasoning_tokens,
                    )} · cache writes ${formatCompact(totals.cache_write_tokens)}`}
                  />
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">Provider balances</h3>
                  {balances ? (
                    <span className="small text-secondary">
                      checked {formatDateTime(balances.balances[0]?.fetched_at ?? null)}
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-primary ms-auto"
                    onClick={() => void refreshBalances()}
                    disabled={balanceBusy}
                  >
                    {balanceBusy ? 'Checking…' : 'Refresh balances'}
                  </button>
                </div>
                {balanceError ? (
                  <div className="card-body">
                    <div className="alert alert-warning mb-0" role="alert">
                      {balanceError}
                    </div>
                  </div>
                ) : balances === null ? (
                  <div className="card-body text-center text-secondary py-4" role="status">
                    <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                    Checking provider balances…
                  </div>
                ) : balances.balances.length === 0 ? (
                  <div className="card-body">
                    <EmptyState message="No providers are configured in the AI registry." />
                  </div>
                ) : (
                  <div className="card-body p-0">
                    <div className="table-responsive">
                      <table className="table table-sm align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Provider</th>
                            <th scope="col">Kind</th>
                            <th scope="col" className="text-end">
                              Balance
                            </th>
                            <th scope="col">Breakdown</th>
                            <th scope="col">Checked</th>
                          </tr>
                        </thead>
                        <tbody>
                          {balances.balances.map((balance) => (
                            <tr key={balance.integration_id}>
                              <td>
                                <div className="fw-semibold">{balance.label}</div>
                                <div className="small text-secondary">{balance.name}</div>
                                {balance.endpoint ? (
                                  <div className="small text-secondary text-break">
                                    {balance.endpoint}
                                  </div>
                                ) : null}
                              </td>
                              <td>
                                <span className="badge text-bg-secondary">
                                  {balance.kind}
                                </span>
                              </td>
                              <td className="text-end text-nowrap">
                                {balance.supported ? (
                                  balance.error ? (
                                    <span className="text-secondary">—</span>
                                  ) : (
                                    <span className="fw-semibold">
                                      {balance.total} {balance.currency}
                                    </span>
                                  )
                                ) : (
                                  <span className="badge text-bg-light border">not exposed</span>
                                )}
                              </td>
                              <td className="small">
                                {balance.lines.map((line) => (
                                  <div key={`${line.label}-${line.value}`}>
                                    <span className="text-secondary">{line.label}:</span>{' '}
                                    {line.value}
                                  </div>
                                ))}
                                {balance.error ? (
                                  <div
                                    className={`text-break ${
                                      balance.supported ? 'text-danger' : 'text-secondary'
                                    } ${balance.lines.length > 0 ? 'mt-1' : ''}`}
                                  >
                                    {balance.error}
                                  </div>
                                ) : balance.lines.length === 0 ? (
                                  <span className="text-secondary">—</span>
                                ) : null}
                              </td>
                              <td className="small text-nowrap">
                                {formatDateTime(balance.fetched_at)}
                                {balance.cached ? (
                                  <span className="badge text-bg-light border ms-2">
                                    cached
                                  </span>
                                ) : null}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                <div className="card-footer small text-secondary">
                  Balances come from the server-side keys of internal integrations. A
                  gateway route forwards the client's own key, so proxied traffic does
                  not appear here.
                </div>
              </div>

              <div className="row g-3 mb-3">
                <div className="col-xl-7">
                  <div className="card h-100">
                    <div className="card-header">
                      <h3 className="card-title mb-0">Usage by day</h3>
                      <span className="small text-secondary ms-2">
                        UTC days · tokens and estimated cost
                      </span>
                    </div>
                    <div className="card-body p-0">
                      <div className="table-responsive">
                        <table className="table table-sm table-striped align-middle mb-0">
                          <thead>
                            <tr>
                              <th scope="col">Day</th>
                              <th scope="col" className="text-end">
                                Calls
                              </th>
                              <th scope="col" className="text-end">
                                In
                              </th>
                              <th scope="col" className="text-end">
                                Out
                              </th>
                              <th scope="col" className="text-end">
                                Cached
                              </th>
                              <th scope="col" className="text-end">
                                Total
                              </th>
                              <th scope="col" className="text-end">
                                Cost
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {data.by_day.map((row) => (
                              <tr key={row.date}>
                                <td className="text-nowrap">{formatDay(row.date)}</td>
                                <td className="text-end">{formatNumber(row.requests)}</td>
                                <td className="text-end">{formatTokens(row.input_tokens)}</td>
                                <td className="text-end">{formatTokens(row.output_tokens)}</td>
                                <td className="text-end">
                                  {formatTokens(row.cached_input_tokens)}
                                </td>
                                <td className="text-end fw-semibold">
                                  {formatTokens(row.total_tokens)}
                                </td>
                                <td className="text-end text-nowrap">
                                  {formatRowCost(row, data.currency)}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                </div>

                <div className="col-xl-5">
                  <div className="card h-100">
                    <div className="card-header">
                      <h3 className="card-title mb-0">By feature</h3>
                    </div>
                    <div className="card-body p-0">
                      {data.by_capability.length === 0 ? (
                        <EmptyState message="No calls in this period." />
                      ) : (
                        <div className="table-responsive">
                          <table className="table table-sm align-middle mb-0">
                            <thead>
                              <tr>
                                <th scope="col">Feature</th>
                                <th scope="col" className="text-end">
                                  Calls
                                </th>
                                <th scope="col" className="text-end">
                                  Tokens
                                </th>
                                <th scope="col" className="text-end">
                                  Cost
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {data.by_capability.map((row) => (
                                <tr key={row.capability}>
                                  <td>
                                    {capabilityLabel(row.capability)}
                                    {row.errors > 0 ? (
                                      <span className="badge text-bg-danger ms-2">
                                        {row.errors} failed
                                      </span>
                                    ) : null}
                                  </td>
                                  <td className="text-end">{formatNumber(row.requests)}</td>
                                  <td className="text-end">{formatCompact(row.total_tokens)}</td>
                                  <td className="text-end">
                                    {formatRowCost(row, data.currency)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header">
                  <h3 className="card-title mb-0">By model</h3>
                </div>
                <div className="card-body p-0">
                  {data.by_model.length === 0 ? (
                    <EmptyState message="No calls in this period." />
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-sm table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Provider</th>
                            <th scope="col">Model</th>
                            <th scope="col" className="text-end">
                              Calls
                            </th>
                            <th scope="col" className="text-end">
                              In
                            </th>
                            <th scope="col" className="text-end">
                              Out
                            </th>
                            <th scope="col" className="text-end">
                              Cached
                            </th>
                            <th scope="col" className="text-end">
                              Total
                            </th>
                            <th scope="col" className="text-end">
                              Cost
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.by_model.map((row) => (
                            <tr key={`${row.provider}:${row.model}`}>
                              <td>{row.provider}</td>
                              <td>
                                <span className="fw-semibold text-break">{row.model}</span>
                              </td>
                              <td className="text-end">{formatNumber(row.requests)}</td>
                              <td className="text-end">{formatTokens(row.input_tokens)}</td>
                              <td className="text-end">{formatTokens(row.output_tokens)}</td>
                              <td className="text-end">{formatTokens(row.cached_input_tokens)}</td>
                              <td className="text-end fw-semibold">
                                {formatTokens(row.total_tokens)}
                              </td>
                              <td className="text-end">
                                {formatRowCost(row, data.currency)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">Recent calls</h3>
                  <div className="ms-auto d-flex flex-wrap gap-2">
                    <select
                      className="form-select form-select-sm w-auto"
                      aria-label="Filter by feature"
                      value={capability}
                      onChange={(event) =>
                        changeFilter(() => setCapability(event.target.value))
                      }
                    >
                      <option value="">All features</option>
                      {capabilityOptions.map((key) => (
                        <option key={key} value={key}>
                          {capabilityLabel(key)}
                        </option>
                      ))}
                    </select>
                    <select
                      className="form-select form-select-sm w-auto"
                      aria-label="Filter by status"
                      value={status}
                      onChange={(event) =>
                        changeFilter(() => setStatus(event.target.value))
                      }
                    >
                      <option value="">All statuses</option>
                      <option value="ok">OK</option>
                      <option value="error">Failed</option>
                    </select>
                  </div>
                </div>
                {records.status === 'loading' ? (
                  <div className="card-body text-center text-secondary py-4" role="status">
                    <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                    Loading calls…
                  </div>
                ) : records.status === 'error' ? (
                  <div className="card-body">
                    <ErrorBlock error={records.error} onRetry={records.reload} />
                  </div>
                ) : !page || page.items.length === 0 ? (
                  <div className="card-body">
                    <EmptyState
                      message={
                        capability || status
                          ? 'No call matches these filters.'
                          : 'No model calls were recorded in this period.'
                      }
                    />
                  </div>
                ) : (
                  <>
                    <div className="card-body p-0">
                      <div className="table-responsive">
                        <table className="table table-sm align-middle mb-0">
                          <thead>
                            <tr>
                              <th scope="col">Time</th>
                              <th scope="col">Feature</th>
                              <th scope="col">Provider / model</th>
                              <th scope="col" className="text-end">
                                In
                              </th>
                              <th scope="col" className="text-end">
                                Out
                              </th>
                              <th scope="col" className="text-end">
                                Cached
                              </th>
                              <th scope="col" className="text-end">
                                Total
                              </th>
                              <th scope="col" className="text-end">
                                Latency
                              </th>
                              <th scope="col" className="text-end">
                                Cost
                              </th>
                              <th scope="col">Status</th>
                              <th scope="col">Details</th>
                            </tr>
                          </thead>
                          <tbody>
                            {page.items.map((record) => (
                              <Fragment key={record.id}>
                              <tr>
                                <td className="text-nowrap small">
                                  {formatDateTime(record.at)}
                                </td>
                                <td className="small">
                                  {capabilityLabel(record.capability)}
                                  <div className="text-secondary">{record.source}</div>
                                </td>
                                <td className="small">
                                  <div className="fw-semibold text-break">
                                    {record.model || '—'}
                                  </div>
                                  <div className="text-secondary text-break">
                                    {record.provider || '—'}
                                  </div>
                                </td>
                                <td className="text-end">{formatTokens(record.input_tokens)}</td>
                                <td className="text-end">
                                  {formatTokens(record.output_tokens)}
                                </td>
                                <td className="text-end">
                                  {formatTokens(record.cached_input_tokens)}
                                </td>
                                <td className="text-end fw-semibold">
                                  {formatTokens(record.total_tokens)}
                                </td>
                                <td className="text-end text-nowrap small">
                                  {formatLatency(record.latency_ms)}
                                </td>
                                <td className="text-end text-nowrap small">
                                  {formatCost(record.cost_micros, record.cost_currency)}
                                  {record.cost_source === 'provider' ? (
                                    <div className="text-secondary">provider</div>
                                  ) : null}
                                </td>
                                <td>{statusBadge(record.status)}</td>
                                <td>
                                  {hasDetails(record) ? (
                                    <button
                                      type="button"
                                      className="btn btn-sm btn-link p-0"
                                      aria-expanded={expanded === record.id}
                                      onClick={() =>
                                        setExpanded(
                                          expanded === record.id ? null : record.id,
                                        )
                                      }
                                    >
                                      {expanded === record.id ? 'hide' : 'view'}
                                    </button>
                                  ) : (
                                    <span className="text-secondary">—</span>
                                  )}
                                </td>
                              </tr>
                              {expanded === record.id ? (
                                <RecordDetailRow record={record} columns={11} />
                              ) : null}
                              </Fragment>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                    <div className="card-footer d-flex flex-wrap align-items-center gap-2">
                      <span className="small text-secondary">
                        {formatNumber(page.offset + 1)}–
                        {formatNumber(page.offset + page.items.length)} of{' '}
                        {formatNumber(page.total)}
                      </span>
                      <div className="ms-auto btn-group btn-group-sm">
                        <button
                          type="button"
                          className="btn btn-outline-secondary"
                          disabled={page.offset === 0}
                          onClick={() => setOffset(Math.max(page.offset - PAGE_SIZE, 0))}
                        >
                          Previous
                        </button>
                        <button
                          type="button"
                          className="btn btn-outline-secondary"
                          disabled={page.offset + page.items.length >= page.total}
                          onClick={() => setOffset(page.offset + PAGE_SIZE)}
                        >
                          Next
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </div>

              <div className="card">
                <div className="card-header">
                  <h3 className="card-title mb-0">Prices</h3>
                  <span className="small text-secondary ms-2">
                    {data.currency} per 1M tokens · read from <code>usage.prices</code>
                  </span>
                </div>
                <div className="card-body">
                  {data.prices.length === 0 ? (
                    <div className="alert alert-warning mb-0" role="note">
                      <p className="mb-2">
                        No prices are configured, so calls are counted but no cost is
                        estimated. Add model prices to <code>usage.prices</code> in
                        <code> apps/api/config.yaml</code>; prices are applied when the
                        page is read, so history is re-priced too.
                      </p>
                      <pre className="small mb-0">{`usage:
  prices:
    deepseek-flash:
      input: 0.30
      output: 1.20
      cached_input: 0.006`}</pre>
                    </div>
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-sm table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Model match</th>
                            <th scope="col" className="text-end">
                              Input
                            </th>
                            <th scope="col" className="text-end">
                              Output
                            </th>
                            <th scope="col" className="text-end">
                              Cached input
                            </th>
                            <th scope="col" className="text-end">
                              Cache write
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.prices.map((price) => (
                            <tr key={price.match}>
                              <td>
                                <code>{price.match}</code>
                              </td>
                              <td className="text-end">{formatPrice(price.input_per_million)}</td>
                              <td className="text-end">{formatPrice(price.output_per_million)}</td>
                              <td className="text-end">
                                {formatPrice(price.cached_input_per_million)}
                              </td>
                              <td className="text-end">
                                {formatPrice(price.cache_write_per_million)}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
                <div className="card-footer small text-secondary">
                  Cost prefers a provider-reported value (for example OpenRouter's
                  <code> usage.cost</code>) and otherwise applies these prices. Peak/off-peak
                  vendor pricing cannot be expressed here, so estimates can differ from the
                  invoice; provider balances are the authoritative number.
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
