import { api } from '../api/client';
import type { AssistantStatus, AssistantTaskList } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';
import { taskRunStatus } from '../lib/status';

interface TasksData {
  tasks: AssistantTaskList;
  /** Null when the status route is unavailable; the task list still renders. */
  status: AssistantStatus | null;
}

export function TasksPage() {
  const result = useApi<TasksData>(async () => {
    const [tasks, status] = await Promise.all([
      api.assistantTasks(),
      api.assistantStatus().catch(() => null),
    ]);
    return { tasks, status };
  }, []);

  const loaded = result.status === 'success' ? result.data : null;
  const tasks = loaded?.tasks.tasks ?? [];
  const errors = loaded?.tasks.errors ?? [];
  const active = tasks.filter((task) => task.enabled).length;
  const failing = tasks.filter((task) => task.last_status === 'error').length;
  const schedulerRunning = loaded?.status?.scheduler.running ?? null;

  return (
    <>
      <PageHeader title="Scheduled tasks" />
      <div className="app-content">
        <div className="container-fluid">
          {result.status === 'loading' ? (
            <LoadingBlock label="Loading scheduled tasks…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                The console reads scheduled tasks through the Winnie API. They are only
                available when <code>assistant.enabled</code> is true in
                <code> apps/api/config.yaml</code>.
              </div>
            </>
          ) : loaded ? (
            <>
              {schedulerRunning === false ? (
                <div className="alert alert-warning" role="alert">
                  <i className="bi bi-pause-circle-fill me-2" aria-hidden="true" />
                  The scheduler is disabled (<code>assistant.tasks.enabled: false</code>).
                  The definitions below are readable, but none of them will fire.
                </div>
              ) : null}

              {errors.length > 0 ? (
                <div className="alert alert-warning" role="alert">
                  <p className="mb-1">
                    <strong>
                      {errors.length} task definition{errors.length === 1 ? '' : 's'} ignored.
                    </strong>{' '}
                    The scheduler cannot use these entries.
                  </p>
                  <ul className="mb-0 small">
                    {errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="row g-3 mb-3">
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{tasks.length}</h3>
                      <p>Scheduled tasks</p>
                    </div>
                    <i className="small-box-icon bi bi-alarm" aria-hidden="true" />
                    <span className="small-box-footer">
                      {active} active · {tasks.length - active} paused
                    </span>
                  </div>
                </div>

                <div className="col-6 col-lg-3">
                  <div
                    className={`small-box ${failing > 0 ? 'text-bg-danger' : 'text-bg-success'}`}
                  >
                    <div className="inner">
                      <h3>{failing}</h3>
                      <p>Last run failed</p>
                    </div>
                    <i className="small-box-icon bi bi-exclamation-octagon" aria-hidden="true" />
                    <span className="small-box-footer">
                      {failing > 0
                        ? 'Open the task row for the recorded error'
                        : 'Every last run succeeded'}
                    </span>
                  </div>
                </div>

                <div className="col-6 col-lg-3">
                  <div
                    className={`small-box ${
                      schedulerRunning === null
                        ? 'text-bg-secondary'
                        : schedulerRunning
                          ? 'text-bg-success'
                          : 'text-bg-warning'
                    }`}
                  >
                    <div className="inner">
                      <h3>{schedulerRunning === null ? '—' : schedulerRunning ? 'On' : 'Off'}</h3>
                      <p>Scheduler</p>
                    </div>
                    <i className="small-box-icon bi bi-clock-history" aria-hidden="true" />
                    <span className="small-box-footer">
                      {schedulerRunning === null
                        ? 'Scheduler status unavailable'
                        : schedulerRunning
                          ? 'Runs on the API process'
                          : 'Disabled in configuration'}
                    </span>
                  </div>
                </div>

                <div className="col-6 col-lg-3">
                  <div
                    className={`small-box ${
                      errors.length > 0 ? 'text-bg-warning' : 'text-bg-info'
                    }`}
                  >
                    <div className="inner">
                      <h3>{errors.length}</h3>
                      <p>Ignored definitions</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-x" aria-hidden="true" />
                    <span className="small-box-footer">
                      {errors.length > 0 ? 'Fix the entries listed above' : 'All entries are valid'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">Tasks</h3>
                  <div className="ms-auto">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={result.reload}
                    >
                      <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                      Refresh
                    </button>
                  </div>
                </div>

                <div className="card-body p-0">
                  {tasks.length === 0 ? (
                    <div className="p-3">
                      <EmptyState message="No scheduled tasks yet. Ask the assistant in chat to create one, then refresh this page." />
                    </div>
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Task</th>
                            <th scope="col">Schedule</th>
                            <th scope="col">State</th>
                            <th scope="col">Next run</th>
                            <th scope="col">Last run</th>
                            <th scope="col">Last result</th>
                          </tr>
                        </thead>
                        <tbody>
                          {tasks.map((task) => {
                            const meta = taskRunStatus(task.last_status);
                            return (
                              <tr key={task.id}>
                                <td style={{ minWidth: '18rem' }}>
                                  <div className="fw-semibold">{task.title}</div>
                                  <code className="small text-secondary">{task.id}</code>
                                  <details className="mt-1">
                                    <summary className="small text-secondary">
                                      Prompt and delivery
                                    </summary>
                                    <pre className="small mt-2 mb-1 text-wrap">{task.prompt}</pre>
                                    <div className="small text-secondary text-break">
                                      Webhook:{' '}
                                      {task.webhook ? (
                                        <code>{task.webhook}</code>
                                      ) : (
                                        'none (stored conversation only)'
                                      )}
                                    </div>
                                  </details>
                                </td>
                                <td className="text-nowrap">
                                  <code>{task.schedule}</code>
                                </td>
                                <td className="text-nowrap">
                                  {task.enabled ? (
                                    <span className="badge text-bg-success">Active</span>
                                  ) : (
                                    <span className="badge text-bg-secondary">Paused</span>
                                  )}
                                </td>
                                <td className="text-nowrap">{formatDateTime(task.next_run)}</td>
                                <td className="text-nowrap">{formatDateTime(task.last_run)}</td>
                                <td style={{ minWidth: '10rem' }}>
                                  <span className={`badge ${meta.badge}`}>{meta.label}</span>
                                  {task.last_error ? (
                                    <div className="small text-danger text-break mt-1">
                                      {task.last_error}
                                    </div>
                                  ) : null}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>

                <div className="card-footer small text-secondary">
                  Times are shown in the assistant&apos;s timezone
                  {loaded.status ? (
                    <>
                      , <code>{loaded.status.timezone}</code>
                    </>
                  ) : null}
                  . Tasks are created and cancelled by chatting with the assistant; this page is
                  read-only. Only the last persisted result is shown; the full run history stays in
                  <code> tasks/runs.jsonl</code>.
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
