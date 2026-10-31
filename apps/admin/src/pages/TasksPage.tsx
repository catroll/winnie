import { useState } from 'react';

import { api } from '../api/client';
import type {
  AssistantStatus,
  AssistantTask,
  AssistantTaskList,
  AssistantTaskRunList,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime, formatElapsed } from '../lib/format';
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
                {tr(
                  'The console reads scheduled tasks through the Winnie API. They are only available when',
                )}{' '}
                <code>assistant.enabled</code> {tr('is true in')}{' '}
                <code> apps/api/config.yaml</code>.
              </div>
            </>
          ) : loaded ? (
            <>
              {schedulerRunning === false ? (
                <div className="alert alert-warning" role="alert">
                  <i className="bi bi-pause-circle-fill me-2" aria-hidden="true" />
                  {tr('The scheduler is disabled (')}
                  <code>assistant.tasks.enabled: false</code>
                  {tr(
                    '). The definitions below are readable, but none of them will fire.',
                  )}
                </div>
              ) : null}

              {errors.length > 0 ? (
                <div className="alert alert-warning" role="alert">
                  <p className="mb-1">
                    <strong>
                      {errors.length === 1
                        ? tr('{{count}} task definition ignored.', {
                            count: errors.length,
                          })
                        : tr('{{count}} task definitions ignored.', {
                            count: errors.length,
                          })}
                    </strong>{' '}
                    {tr('The scheduler cannot use these entries.')}
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
                      <p>{tr('Scheduled tasks')}</p>
                    </div>
                    <i className="small-box-icon bi bi-alarm" aria-hidden="true" />
                    <span className="small-box-footer">
                      {tr('{{active}} active · {{paused}} paused', {
                        active,
                        paused: tasks.length - active,
                      })}
                    </span>
                  </div>
                </div>

                <div className="col-6 col-lg-3">
                  <div
                    className={`small-box ${failing > 0 ? 'text-bg-danger' : 'text-bg-success'}`}
                  >
                    <div className="inner">
                      <h3>{failing}</h3>
                      <p>{tr('Last run failed')}</p>
                    </div>
                    <i className="small-box-icon bi bi-exclamation-octagon" aria-hidden="true" />
                    <span className="small-box-footer">
                      {failing > 0
                        ? tr('Open the task row for the recorded error')
                        : tr('Every last run succeeded')}
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
                      <h3>{schedulerRunning === null ? '—' : schedulerRunning ? tr('On') : tr('Off')}</h3>
                      <p>{tr('Scheduler')}</p>
                    </div>
                    <i className="small-box-icon bi bi-clock-history" aria-hidden="true" />
                    <span className="small-box-footer">
                      {schedulerRunning === null
                        ? tr('Scheduler status unavailable')
                        : schedulerRunning
                          ? tr('Runs on the API process')
                          : tr('Disabled in configuration')}
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
                      <p>{tr('Ignored definitions')}</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-x" aria-hidden="true" />
                    <span className="small-box-footer">
                      {errors.length > 0
                        ? tr('Fix the entries listed above')
                        : tr('All entries are valid')}
                    </span>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">{tr('Tasks')}</h3>
                  <div className="ms-auto">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={result.reload}
                    >
                      <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                      {tr('Refresh')}
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
                            <th scope="col">{tr('Task')}</th>
                            <th scope="col">{tr('Schedule')}</th>
                            <th scope="col">{tr('State')}</th>
                            <th scope="col">{tr('Next run')}</th>
                            <th scope="col">{tr('Last run')}</th>
                            <th scope="col">{tr('Last result')}</th>
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
                                      {tr('Prompt and delivery')}
                                    </summary>
                                    <pre className="small mt-2 mb-1 text-wrap">{task.prompt}</pre>
                                    <div className="small text-secondary text-break">
                                      {tr('Webhook')}:{' '}
                                      {task.webhook ? (
                                        <code>{task.webhook}</code>
                                      ) : (
                                        tr('none (stored conversation only)')
                                      )}
                                    </div>
                                  </details>
                                </td>
                                <td className="text-nowrap">
                                  <code>{task.schedule}</code>
                                </td>
                                <td className="text-nowrap">
                                  {task.enabled ? (
                                    <span className="badge text-bg-success">{tr('Active')}</span>
                                  ) : (
                                    <span className="badge text-bg-secondary">{tr('Paused')}</span>
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
                  {tr("Times are shown in the assistant's timezone")}
                  {loaded.status ? (
                    <>
                      , <code>{loaded.status.timezone}</code>
                    </>
                  ) : null}
                  {tr(
                    '. Tasks are created and cancelled by chatting with the assistant; this page is read-only. The execution history is below; the raw append-only log stays in',
                  )}
                  <code> tasks/runs.jsonl</code>.
                </div>
              </div>

              <RunHistory tasks={tasks} />
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}

const RUN_PAGE_SIZE = 25;

function RunHistory({ tasks }: { tasks: AssistantTask[] }) {
  const [taskId, setTaskId] = useState('');
  const [offset, setOffset] = useState(0);
  const result = useApi(
    () =>
      api.assistantTaskRuns({
        task_id: taskId || undefined,
        limit: RUN_PAGE_SIZE,
        offset,
      }),
    [taskId, offset],
  );

  const data: AssistantTaskRunList | null =
    result.status === 'success' ? result.data : null;
  const titles = new Map(tasks.map((task) => [task.id, task.title]));
  const first = data && data.runs.length > 0 ? data.offset + 1 : 0;
  const last = data ? data.offset + data.runs.length : 0;
  const hasNewer = Boolean(data && data.offset > 0);
  const hasOlder = Boolean(data && data.offset + data.limit < data.total);

  return (
    <div className="card mt-3">
      <div className="card-header d-flex flex-wrap align-items-center gap-2">
        <h3 className="card-title mb-0">{tr('Run history')}</h3>
        <div className="ms-auto d-flex flex-wrap align-items-center gap-2">
          <label className="visually-hidden" htmlFor="run-task-filter">
            {tr('Filter runs by task')}
          </label>
          <select
            id="run-task-filter"
            className="form-select form-select-sm"
            style={{ width: 'auto' }}
            value={taskId}
            onChange={(event) => {
              setTaskId(event.target.value);
              setOffset(0);
            }}
          >
            <option value="">{tr('All tasks')}</option>
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.title}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-sm btn-outline-secondary"
            onClick={result.reload}
          >
            <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
            {tr('Refresh')}
          </button>
        </div>
      </div>

      <div className="card-body p-0">
        {result.status === 'loading' ? (
          <div className="p-4 text-center" role="status" aria-live="polite">
            <div className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
            <span className="ms-2 text-secondary">{tr('Loading run history…')}</span>
          </div>
        ) : result.status === 'error' ? (
          <div className="p-3">
            <ErrorBlock error={result.error} onRetry={result.reload} />
          </div>
        ) : !data || data.runs.length === 0 ? (
          <div className="p-3">
            <EmptyState
              message={
                taskId
                  ? 'No runs recorded for this task yet.'
                  : 'No task runs recorded yet. A task that fires, or a manual run through the API, appends a line here.'
              }
            />
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-striped align-middle mb-0">
              <thead>
                <tr>
                  <th scope="col">{tr('Started')}</th>
                  <th scope="col">{tr('Task')}</th>
                  <th scope="col">{tr('Result')}</th>
                  <th scope="col">{tr('Duration')}</th>
                  <th scope="col">{tr('Conversation')}</th>
                  <th scope="col">{tr('Details')}</th>
                </tr>
              </thead>
              <tbody>
                {data.runs.map((run, index) => {
                  const meta = taskRunStatus(run.status);
                  return (
                    <tr key={`${run.task_id}-${run.started_at}-${index}`}>
                      <td className="text-nowrap">{formatDateTime(run.started_at)}</td>
                      <td style={{ minWidth: '12rem' }}>
                        <div className="fw-semibold">
                          {titles.get(run.task_id) ?? run.task_id}
                        </div>
                        <code className="small text-secondary">{run.task_id}</code>
                      </td>
                      <td>
                        <span className={`badge ${meta.badge}`}>{meta.label}</span>
                      </td>
                      <td className="text-nowrap">
                        {formatElapsed(run.started_at, run.finished_at)}
                      </td>
                      <td>
                        <code className="small text-break">{run.conversation_id || '—'}</code>
                      </td>
                      <td style={{ minWidth: '14rem' }}>
                        {run.error ? (
                          <div className="small text-danger text-break">{run.error}</div>
                        ) : null}
                        {run.delivery_error ? (
                          <div className="small text-warning-emphasis text-break">
                            {tr('Delivery: {{error}}', { error: run.delivery_error })}
                          </div>
                        ) : null}
                        {!run.error && !run.delivery_error ? (
                          <span className="text-secondary">—</span>
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

      <div className="card-footer d-flex flex-wrap align-items-center gap-2 small text-secondary">
        <span>
          {data ? (
            data.total === 1 ? (
              tr('Showing {{first}}–{{last}} of {{total}} run · newest first', {
                first,
                last,
                total: data.total,
              })
            ) : (
              tr('Showing {{first}}–{{last}} of {{total}} runs · newest first', {
                first,
                last,
                total: data.total,
              })
            )
          ) : (
            tr('Run history')
          )}
          {data && data.skipped > 0 ? (
            <>
              {' · '}
              {data.skipped === 1
                ? tr('{{count}} unreadable line skipped', {
                    count: data.skipped,
                  })
                : tr('{{count}} unreadable lines skipped', {
                    count: data.skipped,
                  })}
            </>
          ) : null}
          {data ? (
            <>
              {' '}
              · {tr('raw log')} <code>tasks/runs.jsonl</code>
            </>
          ) : null}
        </span>
        <div
          className="ms-auto btn-group btn-group-sm"
          role="group"
          aria-label={tr('Run history pages')}
        >
          <button
            type="button"
            className="btn btn-outline-secondary"
            disabled={!hasNewer}
            onClick={() => setOffset(Math.max(0, offset - RUN_PAGE_SIZE))}
          >
            {tr('Newer')}
          </button>
          <button
            type="button"
            className="btn btn-outline-secondary"
            disabled={!hasOlder}
            onClick={() => setOffset(offset + RUN_PAGE_SIZE)}
          >
            {tr('Older')}
          </button>
        </div>
      </div>
    </div>
  );
}
