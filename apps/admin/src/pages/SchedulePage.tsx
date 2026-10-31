import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';

import { api } from '../api/client';
import type {
  ScheduleDay,
  ScheduleEvent,
  ScheduleMutation,
  ScheduleWeekPayload,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { useApi } from '../lib/hooks';

/** Shift an ISO day without going through UTC, which would move it. */
function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split('-').map(Number);
  const shifted = new Date(year, month - 1, day + days);
  return [
    shifted.getFullYear(),
    String(shifted.getMonth() + 1).padStart(2, '0'),
    String(shifted.getDate()).padStart(2, '0'),
  ].join('-');
}

function todayIso(): string {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, '0'),
    String(now.getDate()).padStart(2, '0'),
  ].join('-');
}

function describeError(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function timeLabel(event: ScheduleEvent): string {
  if (event.start && event.end) {
    return `${event.start}~${event.end}`;
  }
  return event.start ?? '';
}

interface Notice {
  kind: 'success' | 'danger';
  text: string;
}

interface Editing {
  id: string;
  text: string;
  time: string;
}

export function SchedulePage() {
  const [anchor, setAnchor] = useState<string>(todayIso);
  const result = useApi(() => api.scheduleWeek(anchor), [anchor]);
  const [override, setOverride] = useState<ScheduleWeekPayload | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);

  const [text, setText] = useState('');
  const [time, setTime] = useState('');
  const [tag, setTag] = useState('');
  const [day, setDay] = useState(anchor);
  const [weekdayTag, setWeekdayTag] = useState(false);
  const [alreadyDone, setAlreadyDone] = useState(false);
  const [weekScope, setWeekScope] = useState(false);

  const [editing, setEditing] = useState<Editing | null>(null);

  // A mutation answers with the refreshed week; show that until the user
  // navigates, instead of spending a second round trip on a reload.
  useEffect(() => {
    setOverride(null);
    setEditing(null);
  }, [anchor]);

  const data = override ?? (result.status === 'success' ? result.data : null);

  useEffect(() => {
    if (!data) {
      return;
    }
    const days = data.days.map((candidate) => candidate.date);
    if (days.length > 0 && !days.includes(day)) {
      setDay(days.includes(anchor) ? anchor : days[0]);
    }
  }, [data, anchor, day]);

  const openCount = useMemo(() => {
    if (!data) {
      return 0;
    }
    const events = [...data.days.flatMap((entry) => entry.events), ...data.sections.flatMap((entry) => entry.events)];
    return events.filter((event) => !event.done).length;
  }, [data]);

  async function mutate(action: () => Promise<ScheduleMutation>, done: string): Promise<void> {
    setBusy(true);
    setNotice(null);
    try {
      const response = await action();
      setOverride(response.week);
      setNotice({
        kind: 'success',
        text: response.changed ? done : tr('Nothing changed.'),
      });
      setEditing(null);
    } catch (error) {
      setNotice({ kind: 'danger', text: describeError(error) });
    } finally {
      setBusy(false);
    }
  }

  async function submitAdd(submitEvent: FormEvent<HTMLFormElement>): Promise<void> {
    submitEvent.preventDefault();
    if (!data) {
      return;
    }
    if (!text.trim()) {
      setNotice({ kind: 'danger', text: tr('Event text is required.') });
      return;
    }
    await mutate(
      () =>
        api.createScheduleEvent({
          date: weekScope ? data.week.monday : day,
          text: text.trim(),
          time: time.trim() || null,
          weekday: weekdayTag,
          tags: tag.trim() ? [tag.trim()] : [],
          done: alreadyDone,
          week_scope: weekScope,
        }),
      tr('Event added.'),
    );
    setText('');
    setTime('');
    setTag('');
  }

  async function toggle(event: ScheduleEvent): Promise<void> {
    if (!data) {
      return;
    }
    await mutate(
      () =>
        api.updateScheduleEvent({
          week: data.week.monday,
          id: event.id,
          done: !event.done,
        }),
      tr('Event updated.'),
    );
  }

  async function saveEdit(): Promise<void> {
    if (!data || !editing) {
      return;
    }
    await mutate(
      () =>
        api.updateScheduleEvent({
          week: data.week.monday,
          id: editing.id,
          text: editing.text,
          time: editing.time,
        }),
      tr('Event updated.'),
    );
  }

  async function remove(event: ScheduleEvent): Promise<void> {
    if (!data) {
      return;
    }
    if (!window.confirm(tr('Delete this event?'))) {
      return;
    }
    await mutate(() => api.deleteScheduleEvent(data.week.monday, event.id), tr('Event deleted.'));
  }

  function renderEvent(event: ScheduleEvent) {
    const isEditing = editing?.id === event.id;
    return (
      <li className="list-group-item d-flex align-items-start gap-2" key={event.id}>
        <input
          type="checkbox"
          className="form-check-input mt-1 flex-shrink-0"
          checked={event.done}
          disabled={busy || !data?.editable}
          onChange={() => void toggle(event)}
          aria-label={tr('Done')}
        />
        <div className="flex-grow-1">
          <div className="d-flex flex-wrap align-items-center gap-1 mb-1">
            {timeLabel(event) ? (
              <span className="badge text-bg-light border font-monospace">{timeLabel(event)}</span>
            ) : null}
            {event.weekday ? <span className="badge text-bg-light border">{event.weekday}</span> : null}
            {event.every ? <span className="badge text-bg-info">{tr('Daily')}</span> : null}
            {event.tags.map((value) => (
              <span className="badge text-bg-secondary" key={value}>
                {value}
              </span>
            ))}
          </div>
          {isEditing ? (
            <div className="vstack gap-2">
              <input
                className="form-control form-control-sm"
                value={editing.text}
                onChange={(input) => setEditing({ ...editing, text: input.target.value })}
                aria-label={tr('Event text')}
              />
              <div className="d-flex gap-2">
                <input
                  className="form-control form-control-sm"
                  value={editing.time}
                  onChange={(input) => setEditing({ ...editing, time: input.target.value })}
                  placeholder="09:30~10:30"
                  aria-label={tr('Time')}
                />
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  disabled={busy}
                  onClick={() => void saveEdit()}
                >
                  {tr('Save')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  onClick={() => setEditing(null)}
                >
                  {tr('Cancel')}
                </button>
              </div>
            </div>
          ) : (
            <div className={event.done ? 'text-secondary text-decoration-line-through' : undefined}>
              {event.text}
            </div>
          )}
          {event.links.length > 0 ? (
            <div className="small text-secondary mt-1">
              <i className="bi bi-paperclip me-1" aria-hidden="true" />
              {event.links.map((link) => link.target).join(', ')}
            </div>
          ) : null}
        </div>
        {!isEditing && data?.editable ? (
          <div className="btn-group btn-group-sm flex-shrink-0" role="group">
            <button
              type="button"
              className="btn btn-outline-secondary"
              title={tr('Edit')}
              aria-label={tr('Edit')}
              disabled={busy}
              onClick={() =>
                setEditing({ id: event.id, text: event.text, time: timeLabel(event) })
              }
            >
              <i className="bi bi-pencil" aria-hidden="true" />
            </button>
            <button
              type="button"
              className="btn btn-outline-danger"
              title={tr('Delete')}
              aria-label={tr('Delete')}
              disabled={busy}
              onClick={() => void remove(event)}
            >
              <i className="bi bi-trash" aria-hidden="true" />
            </button>
          </div>
        ) : null}
      </li>
    );
  }

  function renderDays(days: ScheduleDay[]) {
    if (days.length === 0) {
      return (
        <div className="col-12">
          <div className="card">
            <div className="card-body">
              <EmptyState message="This week has no day sections." />
            </div>
          </div>
        </div>
      );
    }
    return days.map((entry) => (
      <div className="col-12 col-xl-6" key={entry.date}>
        <div className={`card h-100${entry.date === data?.today ? ' border-primary' : ''}`}>
          <div className="card-header d-flex flex-wrap align-items-center gap-2">
            <span className="fw-semibold">
              {entry.date.slice(5)} {entry.label}
            </span>
            {entry.date === data?.today ? (
              <span className="badge text-bg-primary">{tr('Today')}</span>
            ) : null}
            <span className="ms-auto small text-secondary">
              {tr('{{count}} events', { count: entry.events.length })}
            </span>
          </div>
          {entry.events.length === 0 ? (
            <div className="card-body py-3 text-secondary small">{tr('Nothing planned.')}</div>
          ) : (
            <ul className="list-group list-group-flush">{entry.events.map(renderEvent)}</ul>
          )}
        </div>
      </div>
    ));
  }

  return (
    <>
      <PageHeader title="Schedule" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="card mb-3">
            <div className="card-body d-flex flex-wrap align-items-center gap-2">
              <div className="btn-group btn-group-sm" role="group">
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setAnchor(addDays(anchor, -7))}
                >
                  <i className="bi bi-chevron-left me-1" aria-hidden="true" />
                  {tr('Previous week')}
                </button>
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setAnchor(data?.today ?? todayIso())}
                >
                  {tr('This week')}
                </button>
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setAnchor(addDays(anchor, 7))}
                >
                  {tr('Next week')}
                  <i className="bi bi-chevron-right ms-1" aria-hidden="true" />
                </button>
              </div>
              {data ? (
                <>
                  <span className="fw-semibold">
                    {data.week.monday} ~ {data.week.sunday}
                  </span>
                  <span className="badge text-bg-secondary">{data.week.label}</span>
                  <span className="small text-secondary">
                    {tr('{{count}} open items', { count: openCount })}
                  </span>
                  <span className="ms-auto small text-secondary font-monospace">
                    {data.file.replace(`${data.root}/`, '')}
                  </span>
                </>
              ) : null}
            </div>
          </div>

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading the schedule…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                {tr('The console reads the schedule through the Winnie API. It is only available when')}{' '}
                <code>schedule.enabled</code> {tr('is true in')}{' '}
                <code>apps/api/config.yaml</code>.
              </div>
            </>
          ) : data ? (
            <>
              {notice ? (
                <div className={`alert alert-${notice.kind}`} role="alert">
                  {notice.text}
                </div>
              ) : null}

              {!data.editable ? (
                <div className="alert alert-warning" role="alert">
                  <i className="bi bi-lock-fill me-2" aria-hidden="true" />
                  {tr(
                    'This week has no day sections, so the console shows it read-only. Pre-2024 files are never rewritten.',
                  )}
                </div>
              ) : null}

              {data.warnings.length > 0 ? (
                <div className="alert alert-warning" role="alert">
                  <p className="mb-1">
                    <strong>{tr('The file disagrees with itself:')}</strong>
                  </p>
                  <ul className="mb-0 small">
                    {data.warnings.map((warning) => (
                      <li key={warning}>{warning}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {data.editable ? (
                <div className="card mb-3">
                  <div className="card-header fw-semibold">{tr('Add event')}</div>
                  <div className="card-body">
                    <form onSubmit={(submitEvent) => void submitAdd(submitEvent)}>
                      <div className="row g-2">
                        <div className="col-12 col-lg-5">
                          <label className="form-label small" htmlFor="schedule-text">
                            {tr('Event text')}
                          </label>
                          <input
                            id="schedule-text"
                            className="form-control"
                            value={text}
                            onChange={(input) => setText(input.target.value)}
                            placeholder={tr('What needs to happen')}
                          />
                        </div>
                        <div className="col-6 col-lg-2">
                          <label className="form-label small" htmlFor="schedule-day">
                            {tr('Day')}
                          </label>
                          <select
                            id="schedule-day"
                            className="form-select"
                            value={day}
                            onChange={(input) => setDay(input.target.value)}
                            disabled={weekScope || data.days.length === 0}
                          >
                            {data.days.map((entry) => (
                              <option value={entry.date} key={entry.date}>
                                {entry.date.slice(5)} {entry.label}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div className="col-6 col-lg-2">
                          <label className="form-label small" htmlFor="schedule-time">
                            {tr('Time')}
                          </label>
                          <input
                            id="schedule-time"
                            className="form-control"
                            value={time}
                            onChange={(input) => setTime(input.target.value)}
                            placeholder="09:30~10:30"
                          />
                        </div>
                        <div className="col-6 col-lg-2">
                          <label className="form-label small" htmlFor="schedule-tag">
                            {tr('Tag')}
                          </label>
                          <input
                            id="schedule-tag"
                            className="form-control"
                            value={tag}
                            onChange={(input) => setTag(input.target.value)}
                            placeholder="newapi"
                          />
                        </div>
                      </div>
                      <div className="d-flex flex-wrap align-items-center gap-3 mt-3">
                        <div className="form-check">
                          <input
                            id="schedule-weekday"
                            type="checkbox"
                            className="form-check-input"
                            checked={weekdayTag}
                            onChange={(input) => setWeekdayTag(input.target.checked)}
                          />
                          <label className="form-check-label small" htmlFor="schedule-weekday">
                            {tr('Add the weekday tag')}
                          </label>
                        </div>
                        <div className="form-check">
                          <input
                            id="schedule-done"
                            type="checkbox"
                            className="form-check-input"
                            checked={alreadyDone}
                            onChange={(input) => setAlreadyDone(input.target.checked)}
                          />
                          <label className="form-check-label small" htmlFor="schedule-done">
                            {tr('Already done')}
                          </label>
                        </div>
                        <div className="form-check">
                          <input
                            id="schedule-week-scope"
                            type="checkbox"
                            className="form-check-input"
                            checked={weekScope}
                            onChange={(input) => setWeekScope(input.target.checked)}
                          />
                          <label className="form-check-label small" htmlFor="schedule-week-scope">
                            {tr('Week-level item')}
                          </label>
                        </div>
                        <button type="submit" className="btn btn-primary btn-sm ms-auto" disabled={busy}>
                          <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                          {tr('Add')}
                        </button>
                      </div>
                    </form>
                  </div>
                </div>
              ) : null}

              {data.sections
                .filter((section) => section.events.length > 0)
                .map((section) => (
                  <div className="card mb-3" key={section.title}>
                    <div className="card-header fw-semibold">{section.title}</div>
                    <ul className="list-group list-group-flush">
                      {section.events.map(renderEvent)}
                    </ul>
                  </div>
                ))}

              <div className="row g-3">{renderDays(data.days)}</div>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
