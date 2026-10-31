import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api } from '../api/client';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime, formatDuration } from '../lib/format';
import { useApi } from '../lib/hooks';
import { meetingStatus, sourceLabel } from '../lib/status';

const PAGE_SIZE = 20;

export function MeetingsPage() {
  const [offset, setOffset] = useState(0);
  const result = useApi(() => api.listMeetings(PAGE_SIZE, offset), [offset]);

  const total = result.status === 'success' ? result.data.total : 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = Math.floor(offset / PAGE_SIZE) + 1;
  const firstRow = total === 0 ? 0 : offset + 1;
  const lastRow = Math.min(offset + PAGE_SIZE, total);

  return (
    <>
      <PageHeader title="Meetings" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">{tr('All meetings')}</h3>
              <div className="card-tools">
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  onClick={result.reload}
                  disabled={result.status === 'loading'}
                >
                  <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                  {tr('Refresh')}
                </button>
              </div>
            </div>

            <div className="card-body p-0">
              {result.status === 'loading' ? (
                <div className="p-3">
                  <LoadingBlock label="Loading meetings…" />
                </div>
              ) : result.status === 'error' ? (
                <div className="p-3">
                  <ErrorBlock error={result.error} onRetry={result.reload} />
                </div>
              ) : result.data.items.length === 0 ? (
                <EmptyState message="No meetings have been recorded yet." />
              ) : (
                <div className="table-responsive">
                  <table className="table table-striped table-hover align-middle mb-0">
                    <thead>
                      <tr>
                        <th scope="col">{tr('Meeting')}</th>
                        <th scope="col">{tr('Status')}</th>
                        <th scope="col">{tr('Source')}</th>
                        <th scope="col">{tr('Duration')}</th>
                        <th scope="col">{tr('Updated')}</th>
                        <th scope="col" className="text-end">
                          <span className="visually-hidden">{tr('Actions')}</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.data.items.map((meeting) => {
                        const meta = meetingStatus(meeting.status);
                        return (
                          <tr key={meeting.id}>
                            <td>
                              <Link
                                to={`/meetings/${meeting.id}`}
                                className="fw-semibold text-decoration-none"
                              >
                                {meeting.title}
                              </Link>
                              {meeting.error ? (
                                <div className="small text-danger text-truncate" style={{ maxWidth: '28rem' }}>
                                  {meeting.error}
                                </div>
                              ) : null}
                              <div className="small text-secondary font-monospace">
                                {meeting.id.slice(0, 8)}
                              </div>
                            </td>
                            <td>
                              <span className={`badge ${meta.badge}`}>{meta.label}</span>
                            </td>
                            <td>{sourceLabel(meeting.source)}</td>
                            <td className="text-nowrap">
                              {formatDuration(meeting.audio_duration_ms)}
                            </td>
                            <td className="text-nowrap text-secondary">
                              {formatDateTime(meeting.updated_at)}
                            </td>
                            <td className="text-end">
                              <Link
                                to={`/meetings/${meeting.id}`}
                                className="btn btn-sm btn-outline-primary text-nowrap"
                              >
                                {tr('View')}
                              </Link>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="card-footer">
              <div className="d-flex flex-wrap justify-content-between align-items-center gap-2">
                <span className="small text-secondary">
                  {total === 0
                    ? tr('No meetings')
                    : tr('Showing {{first}}–{{last}} of {{total}}', {
                        first: firstRow,
                        last: lastRow,
                        total,
                      })}
                </span>
                <nav aria-label={tr('Meeting pagination')}>
                  <ul className="pagination pagination-sm mb-0">
                    <li className={`page-item${offset === 0 ? ' disabled' : ''}`}>
                      <button
                        type="button"
                        className="page-link"
                        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                        disabled={offset === 0}
                      >
                        {tr('Previous')}
                      </button>
                    </li>
                    <li className="page-item disabled">
                      <span className="page-link">
                        {tr('Page {{page}} of {{pages}}', { page, pages: pageCount })}
                      </span>
                    </li>
                    <li className={`page-item${offset + PAGE_SIZE >= total ? ' disabled' : ''}`}>
                      <button
                        type="button"
                        className="page-link"
                        onClick={() => setOffset(offset + PAGE_SIZE)}
                        disabled={offset + PAGE_SIZE >= total}
                      >
                        {tr('Next')}
                      </button>
                    </li>
                  </ul>
                </nav>
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
