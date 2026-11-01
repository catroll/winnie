import { useState } from 'react';

import { api } from '../api/client';
import type { ProactiveItem, ProactiveStatus } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

const FILTERS = [
  { key: 'sent', label: 'Needs an answer' },
  { key: '', label: 'All' },
  { key: 'planned', label: 'Planned' },
  { key: 'acked', label: 'Acknowledged' },
  { key: 'digested', label: 'In a digest' },
  { key: 'muted', label: 'Muted' },
  { key: 'expired', label: 'Expired' },
] as const;

const STAGE_LABELS: Record<string, string> = {
  heads_up: 'heads-up',
  prepare: 'prepare',
  confirm: 'confirm',
  day: 'the day',
  follow_up: 'follow-up',
  conflict: 'conflict',
  leave_by: 'leave by',
  overdue: 'overdue',
  digest: 'digest',
};

const STATUS_BADGES: Record<string, string> = {
  sent: 'text-bg-warning',
  acked: 'text-bg-success',
  planned: 'text-bg-secondary',
  digested: 'text-bg-info',
  snoozed: 'text-bg-secondary',
  muted: 'text-bg-dark',
  expired: 'text-bg-light',
  superseded: 'text-bg-light',
};

interface InboxData {
  items: ProactiveItem[];
  status: ProactiveStatus | null;
}

export function InboxPage() {
  const [filter, setFilter] = useState<string>('sent');
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const result = useApi<InboxData>(async () => {
    const [items, status] = await Promise.all([
      api.proactiveItems({ status: filter, limit: 200 }),
      api.proactiveStatus().catch(() => null),
    ]);
    return { items, status };
  }, [filter, revision]);

  const data = result.status === 'success' ? result.data : null;
  const items = data?.items ?? [];
  const status = data?.status ?? null;

  async function act(id: string, action: 'ack' | 'snooze' | 'mute') {
    setBusy(id);
    setError('');
    try {
      if (action === 'ack') {
        await api.proactiveAck(id);
      } else if (action === 'snooze') {
        await api.proactiveSnooze(id, 60);
      } else {
        await api.proactiveMute(id);
      }
      setRevision((value) => value + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy('');
    }
  }

  async function rescan() {
    setError('');
    try {
      await api.proactiveScan();
      setRevision((value) => value + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function testReminder() {
    setError('');
    setNotice('');
    try {
      const result = await api.proactiveTest();
      if (!result.delivered) {
        setNotice(tr('No channel could deliver the test'));
      } else {
        const channels = (result.channels ?? [])
          .filter((entry) => entry.receipt)
          .map((entry) => entry.name)
          .join(', ');
        setNotice(`${tr('The test was delivered through')} ${channels}`);
      }
      setRevision((value) => value + 1);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <>
      <PageHeader title="Inbox" />
      <div className="app-content">
        <div className="container-fluid">
          {status ? (
            <div className="d-flex flex-wrap gap-3 align-items-center mb-3 small text-body-secondary">
              <span>
                <i className="bi bi-broadcast me-1" aria-hidden="true" />
                {status.channels.length > 0
                  ? status.channels.join(' · ')
                  : tr('no channel is configured')}
              </span>
              <span>
                <i className="bi bi-clock-history me-1" aria-hidden="true" />
                {tr('last scan')}: {formatDateTime(status.last_scan_at || null)}
              </span>
              {Object.entries(status.counts).map(([key, value]) => (
                <span key={key} className="badge text-bg-light text-dark">
                  {key} {value}
                </span>
              ))}
              <button type="button" className="btn btn-sm btn-outline-secondary" onClick={rescan}>
                <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                {tr('Scan now')}
              </button>
              <button type="button" className="btn btn-sm btn-outline-primary" onClick={testReminder}>
                <i className="bi bi-bell me-1" aria-hidden="true" />
                {tr('Test reminder')}
              </button>
            </div>
          ) : null}

          {status?.last_error ? (
            <div className="alert alert-warning" role="alert">
              <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
              {status.last_error}
            </div>
          ) : null}

          {error ? (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          ) : null}

          {notice ? (
            <div className="alert alert-info" role="status">
              {notice}
            </div>
          ) : null}

          <ul className="nav nav-pills mb-3">
            {FILTERS.map((entry) => (
              <li className="nav-item" key={entry.key || 'all'}>
                <button
                  type="button"
                  className={`nav-link ${filter === entry.key ? 'active' : ''}`}
                  onClick={() => setFilter(entry.key)}
                >
                  {tr(entry.label)}
                </button>
              </li>
            ))}
          </ul>

          {result.status === 'loading' ? (
            <LoadingBlock label={tr('Loading the inbox…')} />
          ) : result.status === 'error' ? (
            <ErrorBlock error={result.error} onRetry={result.reload} />
          ) : items.length === 0 ? (
            <>
              <EmptyState message={tr('Nothing needs you right now')} />
              <p className="text-center text-body-secondary small">
                {tr(
                  'Birthdays and work items appear here when the engine thinks they deserve your attention.',
                )}
              </p>
            </>
          ) : (
            <div className="table-responsive">
              <table className="table table-striped align-middle mb-0" style={{ minWidth: '920px' }}>
                <thead>
                  <tr>
                    <th scope="col">{tr('When')}</th>
                    <th scope="col">{tr('What')}</th>
                    <th scope="col">{tr('Stage')}</th>
                    <th scope="col">{tr('Status')}</th>
                    <th scope="col">{tr('Channels')}</th>
                    <th scope="col">{tr('Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="text-nowrap small">{formatDateTime(item.planned_at)}</td>
                      <td style={{ minWidth: '320px' }}>
                        <div className="fw-semibold">{item.title}</div>
                        <div className="small text-body-secondary" style={{ whiteSpace: 'pre-wrap' }}>
                          {item.body}
                        </div>
                      </td>
                      <td className="text-nowrap small">
                        <span className="d-inline-flex align-items-center gap-1">
                          <span className="badge text-bg-light text-dark">
                            {STAGE_LABELS[item.stage] ?? item.stage}
                          </span>
                          <span className="badge text-bg-light text-dark">
                            P{item.priority}
                          </span>
                        </span>
                      </td>
                      <td className="text-nowrap">
                        <span className={`badge ${STATUS_BADGES[item.status] ?? 'text-bg-secondary'}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className="small text-body-secondary">
                        {item.channels.length === 0
                          ? '—'
                          : item.channels
                              .map((entry) =>
                                entry.receipt ? entry.name : `${entry.name} ✕`,
                              )
                              .join(', ')}
                      </td>
                      <td className="text-nowrap">
                        <div className="btn-group btn-group-sm" role="group">
                          <button
                            type="button"
                            className="btn btn-outline-success"
                            disabled={busy === item.id || item.status === 'acked'}
                            onClick={() => act(item.id, 'ack')}
                          >
                            {tr('Received')}
                          </button>
                          <button
                            type="button"
                            className="btn btn-outline-secondary"
                            disabled={busy === item.id}
                            onClick={() => act(item.id, 'snooze')}
                          >
                            {tr('Snooze 1h')}
                          </button>
                          <button
                            type="button"
                            className="btn btn-outline-dark"
                            disabled={busy === item.id}
                            onClick={() => act(item.id, 'mute')}
                          >
                            {tr('Mute')}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
