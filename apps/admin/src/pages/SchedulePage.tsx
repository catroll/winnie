import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';

import { api } from '../api/client';
import type {
  ScheduleEvent,
  ScheduleEventInput,
  ScheduleMutation,
  ScheduleWeekPayload,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { Markdown } from '../components/Markdown';
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

/**
 * The only week-level block the editor writes to. Week items are not tied to a
 * day, so their form lives with that section instead of inside a day tab.
 */
const WEEK_ITEM_SECTION = '工作事项';

interface AddEventFormProps {
  /** The ISO day it files under; the week's Monday for a week-level item. */
  date: string;
  weekScope: boolean;
  busy: boolean;
  /** Resolves false when the write failed, so the draft is not thrown away. */
  onAdd: (input: ScheduleEventInput) => Promise<boolean>;
}

/**
 * One "add event" form, used by every day tab and by the week-level section.
 *
 * The content is a textarea because an event is not one line: the first line is
 * the title and every following line becomes the note under it, which is what
 * the file format already supports (`editor._text_lines` indents the rest).
 */
function AddEventForm({ date, weekScope, busy, onAdd }: AddEventFormProps) {
  const [text, setText] = useState('');
  const [time, setTime] = useState('');
  const [tag, setTag] = useState('');
  const [weekdayTag, setWeekdayTag] = useState(false);
  const [alreadyDone, setAlreadyDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scope = weekScope ? 'week' : date;

  async function submit(submitEvent: FormEvent<HTMLFormElement>): Promise<void> {
    submitEvent.preventDefault();
    if (!text.trim()) {
      setError(tr('Event text is required.'));
      return;
    }
    setError(null);
    const saved = await onAdd({
      date,
      text: text.trim(),
      time: time.trim() || null,
      weekday: !weekScope && weekdayTag,
      tags: tag.trim() ? [tag.trim()] : [],
      done: alreadyDone,
      week_scope: weekScope,
    });
    if (saved) {
      setText('');
      setTime('');
      setTag('');
      setAlreadyDone(false);
    }
  }

  return (
    <form onSubmit={(submitEvent) => void submit(submitEvent)}>
      <div className="row g-2">
        <div className="col-12 col-lg-7">
          <label className="form-label small" htmlFor={`schedule-text-${scope}`}>
            {tr('Event text')}
          </label>
          <textarea
            id={`schedule-text-${scope}`}
            className="form-control"
            rows={3}
            value={text}
            onChange={(input) => setText(input.target.value)}
            placeholder={tr('What needs to happen')}
          />
          <div className="form-text">
            {tr('The first line is the title; further lines become the note.')}
          </div>
          {error ? (
            <div className="text-danger small" role="alert">
              {error}
            </div>
          ) : null}
        </div>
        <div className="col-6 col-lg-2">
          <label className="form-label small" htmlFor={`schedule-time-${scope}`}>
            {tr('Time')}
          </label>
          <input
            id={`schedule-time-${scope}`}
            className="form-control"
            value={time}
            onChange={(input) => setTime(input.target.value)}
            placeholder="09:30~10:30"
          />
        </div>
        <div className="col-6 col-lg-3">
          <label className="form-label small" htmlFor={`schedule-tag-${scope}`}>
            {tr('Tag')}
          </label>
          <input
            id={`schedule-tag-${scope}`}
            className="form-control"
            value={tag}
            onChange={(input) => setTag(input.target.value)}
            placeholder="newapi"
          />
        </div>
      </div>
      <div className="d-flex flex-wrap align-items-center gap-3 mt-3">
        {weekScope ? null : (
          <div className="form-check">
            <input
              id={`schedule-weekday-${scope}`}
              type="checkbox"
              className="form-check-input"
              checked={weekdayTag}
              onChange={(input) => setWeekdayTag(input.target.checked)}
            />
            <label className="form-check-label small" htmlFor={`schedule-weekday-${scope}`}>
              {tr('Add the weekday tag')}
            </label>
          </div>
        )}
        <div className="form-check">
          <input
            id={`schedule-done-${scope}`}
            type="checkbox"
            className="form-check-input"
            checked={alreadyDone}
            onChange={(input) => setAlreadyDone(input.target.checked)}
          />
          <label className="form-check-label small" htmlFor={`schedule-done-${scope}`}>
            {tr('Already done')}
          </label>
        </div>
        <button type="submit" className="btn btn-primary btn-sm ms-auto" disabled={busy}>
          <i className="bi bi-plus-lg me-1" aria-hidden="true" />
          {tr('Add')}
        </button>
      </div>
    </form>
  );
}

export function SchedulePage() {
  const [anchor, setAnchor] = useState<string>(todayIso);
  const result = useApi(() => api.scheduleWeek(anchor), [anchor]);
  const [override, setOverride] = useState<ScheduleWeekPayload | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  // The day tab the owner clicked, resolved against the loaded week below.
  const [activeDate, setActiveDate] = useState<string>(todayIso);

  const [editing, setEditing] = useState<Editing | null>(null);

  // A mutation answers with the refreshed week; show that until the user
  // navigates, instead of spending a second round trip on a reload.
  useEffect(() => {
    setOverride(null);
    setEditing(null);
  }, [anchor]);

  const data = override ?? (result.status === 'success' ? result.data : null);

  // A date that is not in the loaded week — after navigating, or before the
  // first payload — falls back to the anchor day, then to the first day. That
  // keeps the panel non-empty without a second render pass to fix the state.
  const activeDay =
    data?.days.find((entry) => entry.date === activeDate) ??
    data?.days.find((entry) => entry.date === anchor) ??
    data?.days[0] ??
    null;

  const openCount = useMemo(() => {
    if (!data) {
      return 0;
    }
    const events = [...data.days.flatMap((entry) => entry.events), ...data.sections.flatMap((entry) => entry.events)];
    return events.filter((event) => !event.done).length;
  }, [data]);

  // Returns whether the change landed, so a form can keep its input on failure.
  async function mutate(
    action: () => Promise<ScheduleMutation>,
    done: string,
  ): Promise<boolean> {
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
      return true;
    } catch (error) {
      setNotice({ kind: 'danger', text: describeError(error) });
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function submitAdd(input: ScheduleEventInput): Promise<boolean> {
    return mutate(() => api.createScheduleEvent(input), tr('Event added.'));
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
            <>
              <div className={event.done ? 'text-secondary text-decoration-line-through' : undefined}>
                {event.text}
              </div>
              {event.note ? (
                // The event's indented block, already de-indented. `breaks`
                // keeps one line per line: the author writes one point per
                // line and only sometimes separates them with a blank line.
                <div className={event.done ? 'schedule-body text-secondary mt-1' : 'schedule-body mt-1'}>
                  <Markdown source={event.note} breaks />
                </div>
              ) : null}
            </>
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

              {data.sections.map((section) => {
                // The week-level list keeps a form of its own: those items have
                // no day, so no day tab could host it.
                const isWeekList = section.title === WEEK_ITEM_SECTION;
                if (section.events.length === 0 && !(isWeekList && data.editable)) {
                  return null;
                }
                return (
                  <div className="card mb-3" key={section.title}>
                    <div className="card-header fw-semibold">{section.title}</div>
                    {section.events.length === 0 ? (
                      <div className="card-body py-3 text-secondary small">
                        {tr('Nothing planned.')}
                      </div>
                    ) : (
                      <ul className="list-group list-group-flush">
                        {section.events.map(renderEvent)}
                      </ul>
                    )}
                    {isWeekList && data.editable ? (
                      <div className="card-body border-top">
                        <AddEventForm
                          date={data.week.monday}
                          weekScope
                          busy={busy}
                          onAdd={submitAdd}
                        />
                      </div>
                    ) : null}
                  </div>
                );
              })}

              {data.days.length === 0 ? (
                <div className="card">
                  <div className="card-body">
                    <EmptyState message="This week has no day sections." />
                  </div>
                </div>
              ) : (
                <>
                  <ul
                    className="nav nav-tabs mb-0"
                    role="tablist"
                    aria-label={tr('Days of the week')}
                  >
                    {data.days.map((entry) => {
                      const current = entry.date === activeDay?.date;
                      return (
                        <li className="nav-item" role="presentation" key={entry.date}>
                          <button
                            type="button"
                            role="tab"
                            id={`schedule-tab-${entry.date}`}
                            aria-controls={`schedule-day-${entry.date}`}
                            aria-selected={current}
                            className={`nav-link${current ? ' active' : ''}`}
                            onClick={() => setActiveDate(entry.date)}
                          >
                            <span className="font-monospace">{entry.date.slice(5)}</span>{' '}
                            {entry.label}
                            {entry.date === data.today ? (
                              <span className="badge text-bg-primary ms-1">{tr('Today')}</span>
                            ) : null}
                            <span className="badge text-bg-secondary ms-1">
                              {entry.events.length}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                  <div className="card border-top-0 rounded-top-0 mb-3">
                    {activeDay ? (
                      <div
                        role="tabpanel"
                        id={`schedule-day-${activeDay.date}`}
                        aria-labelledby={`schedule-tab-${activeDay.date}`}
                      >
                        {activeDay.events.length === 0 ? (
                          <div className="card-body py-3 text-secondary small">
                            {tr('Nothing planned.')}
                          </div>
                        ) : (
                          <ul className="list-group list-group-flush">
                            {activeDay.events.map(renderEvent)}
                          </ul>
                        )}
                        {data.editable ? (
                          <div className="card-body border-top">
                            <AddEventForm
                              date={activeDay.date}
                              weekScope={false}
                              busy={busy}
                              onAdd={submitAdd}
                            />
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </>
              )}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
