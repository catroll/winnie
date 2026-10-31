import { Link } from 'react-router-dom';

import { api } from '../api/client';
import type { Health, MeetingList, Readiness, RouteList } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { tr } from '../i18n';
import { EmptyState } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { formatDateTime, formatDuration } from '../lib/format';
import { useApi } from '../lib/hooks';
import { meetingStatus } from '../lib/status';

interface DashboardData {
  health: Health | null;
  readiness: Readiness | null;
  routes: RouteList | null;
  meetings: MeetingList | null;
}

export function DashboardPage() {
  const { user } = useAuth();
  const result = useApi<DashboardData>(async () => {
    const [health, readiness, routes, meetings] = await Promise.all([
      api.health().catch(() => null),
      api.readiness().catch(() => null),
      api.routes().catch(() => null),
      api.listMeetings(5, 0).catch(() => null),
    ]);
    return { health, readiness, routes, meetings };
  }, []);

  const data = result.data;
  const ready = data?.readiness?.status === 'ok';

  const meetingsTone = data?.meetings ? 'text-bg-primary' : 'text-bg-secondary';
  const readyTone = ready
    ? 'text-bg-success'
    : data?.readiness
      ? 'text-bg-warning'
      : 'text-bg-secondary';

  return (
    <>
      <PageHeader title="Dashboard" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="row">
            <div className="col-lg-3 col-6">
              <div className={`small-box ${meetingsTone}`}>
                <div className="inner">
                  <h3>{data?.meetings?.total ?? '—'}</h3>
                  <p>{tr('Meetings')}</p>
                </div>
                <i className="small-box-icon bi bi-camera-video-fill" aria-hidden="true" />
                <Link
                  to="/meetings"
                  className="small-box-footer link-light link-underline-opacity-0 link-underline-opacity-50-hover"
                >
                  {tr('Open meetings')} <i className="bi bi-arrow-right-circle" aria-hidden="true" />
                </Link>
              </div>
            </div>

            <div className="col-lg-3 col-6">
              <div className={`small-box ${data?.routes ? 'text-bg-success' : 'text-bg-secondary'}`}>
                <div className="inner">
                  <h3>{data?.routes?.routes.length ?? '—'}</h3>
                  <p>{tr('Gateway routes')}</p>
                </div>
                <i className="small-box-icon bi bi-signpost-split-fill" aria-hidden="true" />
                <Link
                  to="/gateway"
                  className="small-box-footer link-light link-underline-opacity-0 link-underline-opacity-50-hover"
                >
                  {tr('Inspect routes')} <i className="bi bi-arrow-right-circle" aria-hidden="true" />
                </Link>
              </div>
            </div>

            <div className="col-lg-3 col-6">
              <div className="small-box text-bg-info">
                <div className="inner">
                  <h3 className="fs-2">{data?.health?.version ?? '—'}</h3>
                  <p>{tr('API version')}</p>
                </div>
                <i className="small-box-icon bi bi-hdd-network-fill" aria-hidden="true" />
                <span className="small-box-footer">
                  {data?.health ? tr('Health check passed') : tr('API unreachable')}
                </span>
              </div>
            </div>

            <div className="col-lg-3 col-6">
              <div className={`small-box ${readyTone}`}>
                <div className="inner">
                  <h3>{data?.readiness ? (ready ? tr('Ready') : tr('Degraded')) : '—'}</h3>
                  <p>{tr('Readiness')}</p>
                </div>
                <i className="small-box-icon bi bi-heart-pulse-fill" aria-hidden="true" />
                <span className="small-box-footer">
                  {data?.readiness
                    ? tr('Database {{database}} · queue {{queue}}', {
                        database: data.readiness.database,
                        queue: data.readiness.queue,
                      })
                    : tr('Readiness check unavailable')}
                </span>
              </div>
            </div>
          </div>

          <div className="row">
            <div className="col-lg-8">
              <div className="card mb-4">
                <div className="card-header">
                  <h3 className="card-title">{tr('Recent meetings')}</h3>
                  <div className="card-tools">
                    <Link to="/meetings" className="btn btn-sm btn-outline-secondary">
                      {tr('View all')}
                    </Link>
                  </div>
                </div>
                <div className="card-body p-0">
                  {!data?.meetings ? (
                    <div className="alert alert-warning m-3 mb-3" role="alert">
                      {tr(
                        'The meetings service is unavailable. Check that it is enabled in the API configuration.',
                      )}
                    </div>
                  ) : data.meetings.items.length === 0 ? (
                    <EmptyState message="No meetings yet. Record one from the Android client." />
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-striped table-hover align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">{tr('Meeting')}</th>
                            <th scope="col">{tr('Status')}</th>
                            <th scope="col">{tr('Duration')}</th>
                            <th scope="col">{tr('Updated')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {data.meetings.items.map((meeting) => {
                            const meta = meetingStatus(meeting.status);
                            return (
                              <tr key={meeting.id}>
                                <td>
                                  <Link to={`/meetings/${meeting.id}`} className="fw-semibold">
                                    {meeting.title}
                                  </Link>
                                </td>
                                <td>
                                  <span className={`badge ${meta.badge}`}>{meta.label}</span>
                                </td>
                                <td className="text-nowrap">
                                  {formatDuration(meeting.audio_duration_ms)}
                                </td>
                                <td className="text-nowrap text-secondary">
                                  {formatDateTime(meeting.updated_at)}
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
            </div>

            <div className="col-lg-4">
              <div className="card mb-4">
                <div className="card-header">
                  <h3 className="card-title">{tr('Instance status')}</h3>
                </div>
                <div className="card-body">
                  <dl className="row mb-0">
                    <dt className="col-5 text-secondary fw-normal">{tr('Signed in as')}</dt>
                    <dd className="col-7 text-end text-break">
                      {user?.display_name || user?.username}
                    </dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('API')}</dt>
                    <dd className="col-7 text-end">
                      {data?.health ? (
                        <span className="text-success">
                          <i className="bi bi-check-circle-fill me-1" aria-hidden="true" />
                          {data.health.version}
                        </span>
                      ) : (
                        <span className="text-danger">{tr('Unreachable')}</span>
                      )}
                    </dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Database')}</dt>
                    <dd className="col-7 text-end">{data?.readiness?.database ?? '—'}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Job queue')}</dt>
                    <dd className="col-7 text-end">{data?.readiness?.queue ?? '—'}</dd>

                    <dt className="col-5 text-secondary fw-normal">{tr('Routes')}</dt>
                    <dd className="col-7 text-end">
                      {data?.routes
                        ? tr('{{count}} configured', { count: data.routes.routes.length })
                        : tr('Unavailable')}
                    </dd>
                  </dl>
                </div>
              </div>

              <div className="alert alert-info" role="note">
                <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
                {tr(
                  'This console reads the same API as the Android client. Vendor API keys are never stored here; the gateway expects them on each request.',
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
