import { Fragment, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';

import { api } from '../api/client';
import type {
  PhoneApp,
  PhoneCalendarEvent,
  PhoneCall,
  PhoneCommand,
  PhoneContact,
  PhoneDevice,
  PhoneLocation,
  PhoneMessage,
  PhoneMessageSender,
  PhoneNotification,
  PhoneSearchResponse,
  PhoneSummary,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime, formatDuration } from '../lib/format';
import { useApi } from '../lib/hooks';

type TabKey =
  | 'calendar'
  | 'call'
  | 'message'
  | 'contact'
  | 'notification'
  | 'location'
  | 'app'
  | 'command';

type TabData =
  | { kind: 'calendar'; items: PhoneCalendarEvent[] }
  | { kind: 'call'; items: PhoneCall[] }
  | { kind: 'message'; items: PhoneMessage[] }
  | { kind: 'contact'; items: PhoneContact[] }
  | { kind: 'notification'; items: PhoneNotification[] }
  | { kind: 'location'; items: PhoneLocation[] }
  | { kind: 'app'; items: PhoneApp[] }
  | { kind: 'command'; items: PhoneCommand[] };

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: 'calendar', label: 'Calendar', icon: 'bi-calendar3' },
  { key: 'call', label: 'Call log', icon: 'bi-telephone' },
  { key: 'message', label: 'SMS', icon: 'bi-chat-text' },
  { key: 'contact', label: 'Contacts', icon: 'bi-person-lines-fill' },
  { key: 'notification', label: 'Notifications', icon: 'bi-bell' },
  { key: 'location', label: 'Location', icon: 'bi-geo-alt' },
  { key: 'app', label: 'Apps', icon: 'bi-grid-3x3-gap' },
  { key: 'command', label: 'Assistant actions', icon: 'bi-terminal' },
];

const COMMAND_STATUSES = [
  'proposed',
  'approved',
  'dispatched',
  'succeeded',
  'failed',
  'cancelled',
  'expired',
];

const COMMAND_BADGES: Record<string, string> = {
  proposed: 'text-bg-secondary',
  approved: 'text-bg-info',
  dispatched: 'text-bg-primary',
  succeeded: 'text-bg-success',
  failed: 'text-bg-danger',
  cancelled: 'text-bg-dark',
  expired: 'text-bg-warning',
};

const EMPTY_SEARCH: PhoneSearchResponse = {
  query: '',
  hits: [],
  device_names: {},
};

function isTimeBased(tab: TabKey): boolean {
  return (
    tab === 'calendar' ||
    tab === 'call' ||
    tab === 'message' ||
    tab === 'notification' ||
    tab === 'location'
  );
}

function isSearchable(tab: TabKey): boolean {
  return tab !== 'location' && tab !== 'command';
}

function emptyMessage(tab: TabKey): string {
  switch (tab) {
    case 'calendar':
      return 'No calendar event in this window.';
    case 'call':
      return 'No call in this window.';
    case 'message':
      return 'No SMS in this window.';
    case 'contact':
      return 'No contact has synced.';
    case 'notification':
      return 'No notification in this window.';
    case 'location':
      return 'No location fix in this window.';
    case 'app':
      return 'No installed application has synced.';
    case 'command':
      return 'No assistant action has been proposed yet.';
  }
}

function staleLabel(minutes: number | null): string {
  if (minutes === null) return tr('never synced');
  if (minutes < 1) return tr('just now');
  if (minutes < 60) return tr('{{count}} min ago', { count: minutes });
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return tr('{{hours}} h ago', { hours });
  return tr('{{days}} d ago', { days: Math.floor(hours / 24) });
}

function snippet(text: string, limit = 160): string {
  const clean = text.replace(/\s+/g, ' ').trim();
  if (clean.length <= limit) return clean;
  return `${clean.slice(0, limit - 1)}…`;
}

/**
 * Directions the phone writes for an outgoing message. `sent` is the ordinary
 * one; the others are a send that is still queued, failed or only a draft.
 */
const OUTGOING_DIRECTIONS = new Set(['sent', 'outbox', 'queued', 'failed', 'draft']);

function isOutgoingMessage(message: PhoneMessage): boolean {
  return OUTGOING_DIRECTIONS.has(message.direction);
}

function messageDirectionLabel(direction: string): string {
  if (direction === 'inbox') return tr('Received');
  if (direction === 'sent') return tr('Sent');
  return direction;
}

function infoText(device: PhoneDevice, key: string): string {
  const value = device.info[key];
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return '';
}

function infoList(device: PhoneDevice, key: string): string {
  const value = device.info[key];
  if (Array.isArray(value)) return value.map(String).filter(Boolean).join(', ');
  return infoText(device, key);
}

function formatBytes(value: unknown): string {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

interface ScopeRead {
  permission?: string;
  rows?: number;
  error?: string;
}

/**
 * What the four provider scopes answered on the phone, as it reported them.
 *
 * A phone whose ROM keeps the grant but hands back blank rows looks identical
 * to a phone with no data unless the device says so; this is what lets the page
 * explain an empty tab instead of implying the owner never had contacts.
 */
function scopeReads(device: PhoneDevice): { scope: string; read: ScopeRead }[] {
  const raw = device.info['scope_read'];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
  return Object.entries(raw as Record<string, unknown>)
    .filter(([scope, value]) => scope !== 'uid' && scope !== 'package'
      && scope !== 'data_dir' && Boolean(value) && typeof value === 'object')
    .map(([scope, value]) => ({ scope, read: value as ScopeRead }));
}

const SCOPE_READ_LABELS: Record<string, string> = {
  message: 'SMS',
  call: 'Call log',
  contact: 'Contacts',
  calendar: 'Calendar',
  calendars: 'Calendars',
};

function scopeReadValue(read: ScopeRead): string {
  if (read.error === 'null_cursor') return tr('the system returned nothing');
  if (read.error) return read.error;
  if (read.permission === 'denied') return tr('no permission');
  return tr('{{count}} rows', { count: read.rows ?? 0 });
}

function scopeReadBlocked(read: ScopeRead): boolean {
  return Boolean(read.error) || read.permission === 'denied';
}

function commandDevices(summary: PhoneSummary | null): Record<string, string> {
  const names: Record<string, string> = {};
  for (const entry of summary?.devices ?? []) {
    names[entry.device.id] =
      entry.device.name || entry.device.model || tr('Android phone');
  }
  return names;
}

export function PhonePage() {
  const [tab, setTab] = useState<TabKey>('calendar');
  const [deviceId, setDeviceId] = useState('all');
  const [days, setDays] = useState(30);
  const [limit, setLimit] = useState(100);
  const [queryInput, setQueryInput] = useState('');
  const [appliedQuery, setAppliedQuery] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [direction, setDirection] = useState('');
  const [messageDirection, setMessageDirection] = useState('');
  const [messageAddresses, setMessageAddresses] = useState<string[]>([]);
  const [addressInput, setAddressInput] = useState('');
  const [openedMessage, setOpenedMessage] = useState<PhoneMessage | null>(null);
  const [commandStatus, setCommandStatus] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [notice, setNotice] = useState<{ kind: 'success' | 'danger'; text: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);

  const summary = useApi(() => api.phoneSummary(), []);

  // The same window the rows use; the sender picker filters by it too.
  const windowSince =
    isTimeBased(tab) && days > 0
      ? new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
      : undefined;

  const loadTab = async (): Promise<TabData> => {
    const device = deviceId === 'all' ? undefined : deviceId;
    const shared = { deviceId: device, since: windowSince, limit };
    switch (tab) {
      case 'calendar':
        return {
          kind: 'calendar',
          items: await api.phoneCalendar({ ...shared, q: appliedQuery }),
        };
      case 'call':
        return {
          kind: 'call',
          items: await api.phoneCalls({ ...shared, direction, q: appliedQuery }),
        };
      case 'message':
        return {
          kind: 'message',
          items: await api.phoneMessages({
            ...shared,
            q: appliedQuery,
            unreadOnly,
            direction: messageDirection || undefined,
            addresses: messageAddresses.length > 0 ? messageAddresses : undefined,
          }),
        };
      case 'contact':
        return {
          kind: 'contact',
          items: await api.phoneContacts({ deviceId: device, q: appliedQuery, limit }),
        };
      case 'notification':
        return {
          kind: 'notification',
          items: await api.phoneNotifications({ ...shared, q: appliedQuery }),
        };
      case 'location':
        return { kind: 'location', items: await api.phoneLocations(shared) };
      case 'app':
        return {
          kind: 'app',
          items: await api.phoneApps({ deviceId: device, q: appliedQuery, limit }),
        };
      case 'command':
        return {
          kind: 'command',
          items: await api.phoneCommands(commandStatus, limit),
        };
    }
  };

  const rows = useApi(loadTab, [
    tab,
    deviceId,
    days,
    limit,
    appliedQuery,
    unreadOnly,
    direction,
    messageDirection,
    messageAddresses,
    commandStatus,
  ]);

  // The quick picker only makes sense for SMS; on other tabs it is empty.
  const senders = useApi<PhoneMessageSender[]>(
    () =>
      tab === 'message'
        ? api.phoneMessageSenders({
            since: windowSince,
            deviceId: deviceId === 'all' ? undefined : deviceId,
            limit: 12,
          })
        : Promise.resolve([]),
    [tab, deviceId, days],
  );

  const search = useApi(
    () =>
      searchQuery.trim()
        ? api.phoneSearch(searchQuery.trim())
        : Promise.resolve<PhoneSearchResponse>(EMPTY_SEARCH),
    [searchQuery],
  );

  const summaryData = summary.status === 'success' ? summary.data : null;
  const devices = summaryData?.devices ?? [];
  const deviceNames = commandDevices(summaryData);
  const activeTab = TABS.find((item) => item.key === tab) ?? TABS[0];

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setNotice({ kind: 'success', text: label });
      summary.reload();
      rows.reload();
    } catch (error) {
      setNotice({
        kind: 'danger',
        text: error instanceof Error ? error.message : tr('The action failed.'),
      });
    } finally {
      setBusy(false);
    }
  };

  const wipeDevice = (device: PhoneDevice) => {
    const name = device.name || device.model || tr('this phone');
    if (
      !window.confirm(
        tr(
          'Delete every synced record for {{name}}?\n\nThe phone stays registered, and its next sync uploads the selected scopes again.',
          { name },
        ),
      )
    ) {
      return;
    }
    void runAction(tr('Deleted the server copies for {{name}}.', { name }), () =>
      api.wipePhoneDevice(device.id),
    );
  };

  const revokeDevice = (device: PhoneDevice) => {
    const name = device.name || device.model || tr('this phone');
    if (
      !window.confirm(
        tr(
          'Revoke {{name}}?\n\nThe phone is refused at registration and cannot sync until you restore it.',
          { name },
        ),
      )
    ) {
      return;
    }
    void runAction(tr('Revoked {{name}}.', { name }), () =>
      api.revokePhoneDevice(device.id),
    );
  };

  const restoreDevice = (device: PhoneDevice) => {
    const name = device.name || device.model || tr('this phone');
    void runAction(tr('Restored {{name}}.', { name }), () =>
      api.restorePhoneDevice(device.id),
    );
  };

  const fillAddresses = () => {
    void runAction(tr('Addresses filled in where the provider could resolve them.'), async () => {
      const since =
        days > 0 ? new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString() : undefined;
      const result = await api.geocodePhoneLocations({
        since,
        deviceId: deviceId === 'all' ? undefined : deviceId,
        limit,
      });
      if (!result.configured) {
        throw new Error(
          tr(
            'Reverse geocoding is not configured: set phone.geocode.enabled and the provider key in the API environment.',
          ),
        );
      }
      return result;
    });
  };

  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSearchQuery(searchInput.trim());
  };

  const submitFilter = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAppliedQuery(queryInput.trim());
  };

  const addMessageAddresses = (values: string[]) => {
    setMessageAddresses((current) => {
      const next = [...current];
      for (const raw of values) {
        const value = raw.trim();
        if (!value) continue;
        if (next.some((entry) => entry.toLowerCase() === value.toLowerCase())) {
          continue;
        }
        next.push(value);
      }
      return next.length === current.length ? current : next;
    });
  };

  const toggleMessageAddress = (value: string) => {
    const key = value.trim();
    if (!key) return;
    setMessageAddresses((current) =>
      current.some((entry) => entry.toLowerCase() === key.toLowerCase())
        ? current.filter((entry) => entry.toLowerCase() !== key.toLowerCase())
        : [...current, key],
    );
  };

  const submitMessageAddresses = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // One line may carry several numbers: "10086, 10010 106".
    addMessageAddresses(addressInput.split(/[,，;；\s]+/));
    setAddressInput('');
  };

  const commandDeviceName = (command: PhoneCommand) =>
    deviceNames[command.device_id] ?? command.device_id.slice(0, 8);

  return (
    <>
      <PageHeader title="Phone data" />
      <div className="app-content">
        <div className="container-fluid">
          {notice ? (
            <div className={`alert alert-${notice.kind} alert-dismissible`} role="alert">
              {notice.text}
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Dismiss')}
                onClick={() => setNotice(null)}
              />
            </div>
          ) : null}

          <div className="card">
            <div className="card-header">
              <h3 className="card-title">{tr('Devices')}</h3>
              <div className="card-tools">
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  onClick={summary.reload}
                  disabled={summary.status === 'loading'}
                >
                  <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                  {tr('Refresh')}
                </button>
              </div>
            </div>
            <div className="card-body">
              {summary.status === 'loading' ? (
                <LoadingBlock label="Loading phone devices…" />
              ) : summary.status === 'error' ? (
                <ErrorBlock error={summary.error} onRetry={summary.reload} />
              ) : devices.length === 0 ? (
                <EmptyState message="No phone has synced. Open the Winnie app, sign in and enable phone data." />
              ) : (
                <>
                  <div className="row g-3">
                    {devices.map((entry) => (
                      <DeviceCard
                        key={entry.device.id}
                        entry={entry}
                        busy={busy}
                        onRevoke={() => revokeDevice(entry.device)}
                        onRestore={() => restoreDevice(entry.device)}
                        onWipe={() => wipeDevice(entry.device)}
                      />
                    ))}
                  </div>
                  <p className="small text-secondary mb-0 mt-3">
                    {tr(
                      'Last sync overall {{when}} · {{count}} pending assistant action(s)',
                      {
                        when: staleLabel(summaryData?.stale_minutes ?? null),
                        count: summaryData?.pending_commands ?? 0,
                      },
                    )}
                  </p>
                </>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3 className="card-title">
                <i className="bi bi-search me-1" aria-hidden="true" />
                {tr('Search across everything')}
              </h3>
            </div>
            <div className="card-body">
              <form className="row g-2 align-items-end" onSubmit={submitSearch}>
                <div className="col-12 col-md-8">
                  <label className="form-label small mb-1" htmlFor="phone-search">
                    {tr('Text')}
                  </label>
                  <input
                    id="phone-search"
                    className="form-control form-control-sm"
                    placeholder={tr(
                      'A name, a number, words from a message, a title…',
                    )}
                    value={searchInput}
                    onChange={(event) => setSearchInput(event.target.value)}
                  />
                </div>
                <div className="col-auto">
                  <button type="submit" className="btn btn-sm btn-primary">
                    {tr('Search')}
                  </button>
                  {searchQuery ? (
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary ms-1"
                      onClick={() => {
                        setSearchInput('');
                        setSearchQuery('');
                      }}
                    >
                      {tr('Clear')}
                    </button>
                  ) : null}
                </div>
              </form>

              {searchQuery ? (
                search.status === 'loading' ? (
                  <p className="small text-secondary mb-0 mt-3">{tr('Searching…')}</p>
                ) : search.status === 'error' ? (
                  <div className="mt-3">
                    <ErrorBlock error={search.error} onRetry={search.reload} />
                  </div>
                ) : search.data.hits.length === 0 ? (
                  <p className="small text-secondary mb-0 mt-3">
                    {tr('Nothing matched “{{query}}”.', { query: searchQuery })}
                  </p>
                ) : (
                  <div className="table-responsive mt-3">
                    <table className="table table-sm table-striped align-middle mb-0">
                      <thead>
                        <tr>
                          <th scope="col">{tr('Kind')}</th>
                          <th scope="col">{tr('When')}</th>
                          <th scope="col">{tr('Record')}</th>
                          <th scope="col">{tr('Text')}</th>
                          <th scope="col">{tr('Device')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {search.data.hits.map((hit) => (
                          <tr key={`${hit.kind}-${hit.record_id}-${hit.at ?? ''}`}>
                            <td>
                              <span className="badge text-bg-light">{hit.kind}</span>
                            </td>
                            <td className="text-nowrap text-secondary">
                              {formatDateTime(hit.at)}
                            </td>
                            <td>{hit.title}</td>
                            <td className="small text-secondary">
                              <span
                                className="d-inline-block text-truncate"
                                style={{ maxWidth: '30rem' }}
                              >
                                {snippet(hit.text, 180)}
                              </span>
                            </td>
                            <td className="text-nowrap">{hit.device_name}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )
              ) : (
                <p className="small text-secondary mb-0 mt-2">
                  {tr(
                    'Searches calendar, calls, SMS, contacts, notifications, location and apps in one query.',
                  )}
                </p>
              )}
            </div>
          </div>

          <ul className="nav nav-tabs">
            {TABS.map((item) => (
              <li className="nav-item" key={item.key}>
                <button
                  type="button"
                  className={`nav-link${tab === item.key ? ' active' : ''}`}
                  onClick={() => setTab(item.key)}
                >
                  <i className={`bi ${item.icon} me-1`} aria-hidden="true" />
                  {tr(item.label)}
                </button>
              </li>
            ))}
          </ul>

          <div className="card card-outline card-primary">
            <div className="card-header">
              <h3 className="card-title">{tr(activeTab.label)}</h3>
              <div className="card-tools">
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary"
                  onClick={rows.reload}
                  disabled={rows.status === 'loading'}
                >
                  <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                  {tr('Refresh')}
                </button>
              </div>
            </div>

            <div className="card-body border-bottom py-2">
              <div className="row g-2 align-items-end">
                {isTimeBased(tab) ? (
                  <div className="col-auto">
                    <label className="form-label small mb-1" htmlFor="phone-window">
                      {tr('Window')}
                    </label>
                    <select
                      id="phone-window"
                      className="form-select form-select-sm"
                      value={days}
                      onChange={(event) => setDays(Number(event.target.value))}
                    >
                      <option value={1}>{tr('Last 24 hours')}</option>
                      <option value={7}>{tr('Last 7 days')}</option>
                      <option value={30}>{tr('Last 30 days')}</option>
                      <option value={90}>{tr('Last 90 days')}</option>
                      <option value={365}>{tr('Last year')}</option>
                      <option value={0}>{tr('All time')}</option>
                    </select>
                  </div>
                ) : null}

                {tab === 'call' ? (
                  <div className="col-auto">
                    <label className="form-label small mb-1" htmlFor="phone-direction">
                      {tr('Direction')}
                    </label>
                    <select
                      id="phone-direction"
                      className="form-select form-select-sm"
                      value={direction}
                      onChange={(event) => setDirection(event.target.value)}
                    >
                      <option value="">{tr('Any')}</option>
                      <option value="incoming">{tr('Incoming')}</option>
                      <option value="outgoing">{tr('Outgoing')}</option>
                      <option value="missed">{tr('Missed')}</option>
                      <option value="rejected">{tr('Rejected')}</option>
                      <option value="blocked">{tr('Blocked')}</option>
                      <option value="voicemail">{tr('Voicemail')}</option>
                    </select>
                  </div>
                ) : null}

                {tab === 'message' ? (
                  <div className="col-auto">
                    <label className="form-label small mb-1" htmlFor="phone-message-direction">
                      {tr('Direction')}
                    </label>
                    <select
                      id="phone-message-direction"
                      className="form-select form-select-sm"
                      value={messageDirection}
                      onChange={(event) => setMessageDirection(event.target.value)}
                    >
                      <option value="">{tr('Any')}</option>
                      <option value="inbox">{tr('Received')}</option>
                      <option value="sent">{tr('Sent')}</option>
                    </select>
                  </div>
                ) : null}

                {isSearchable(tab) ? (
                  <div className="col-12 col-md">
                    <label className="form-label small mb-1" htmlFor="phone-filter">
                      {tab === 'message'
                        ? tr('Number or text')
                        : tab === 'notification'
                          ? tr('App or text')
                          : tab === 'contact'
                            ? tr('Name or organisation')
                            : tab === 'app'
                              ? tr('Label or package')
                              : tr('Title, number or text')}
                    </label>
                    <form className="input-group input-group-sm" onSubmit={submitFilter}>
                      <input
                        id="phone-filter"
                        className="form-control"
                        value={queryInput}
                        onChange={(event) => setQueryInput(event.target.value)}
                        placeholder={tr('Optional filter')}
                      />
                      <button type="submit" className="btn btn-outline-secondary">
                        {tr('Apply')}
                      </button>
                    </form>
                  </div>
                ) : null}

                {tab === 'message' ? (
                  <div className="col-auto">
                    <div className="form-check mb-1">
                      <input
                        id="phone-unread"
                        className="form-check-input"
                        type="checkbox"
                        checked={unreadOnly}
                        onChange={(event) => setUnreadOnly(event.target.checked)}
                      />
                      <label className="form-check-label small" htmlFor="phone-unread">
                        {tr('Unread only')}
                      </label>
                    </div>
                  </div>
                ) : null}

                {tab === 'message' ? (
                  <div className="col-12">
                    <label className="form-label small mb-1" htmlFor="phone-numbers">
                      {tr('Numbers')}
                    </label>
                    <div className="d-flex flex-wrap align-items-center gap-2">
                      <form
                        className="input-group input-group-sm"
                        style={{ maxWidth: '26rem' }}
                        onSubmit={submitMessageAddresses}
                      >
                        <input
                          id="phone-numbers"
                          className="form-control"
                          placeholder={tr(
                            'A number or part of one, e.g. 106 or 10086',
                          )}
                          value={addressInput}
                          onChange={(event) => setAddressInput(event.target.value)}
                        />
                        <button type="submit" className="btn btn-outline-secondary">
                          {tr('Add')}
                        </button>
                      </form>
                      {messageAddresses.map((address) => (
                        <span
                          key={address}
                          className="badge text-bg-primary d-inline-flex align-items-center gap-1"
                        >
                          <span className="font-monospace">{address}</span>
                          <button
                            type="button"
                            className="btn-close btn-close-white"
                            style={{ fontSize: '0.55rem' }}
                            aria-label={tr('Remove {{value}}', { value: address })}
                            onClick={() => toggleMessageAddress(address)}
                          />
                        </span>
                      ))}
                      {messageAddresses.length > 0 ? (
                        <button
                          type="button"
                          className="btn btn-sm btn-link p-0"
                          onClick={() => setMessageAddresses([])}
                        >
                          {tr('Clear')}
                        </button>
                      ) : null}
                    </div>
                    <div className="small text-secondary mt-1 d-flex flex-wrap align-items-center gap-1">
                      <span>{tr('Recent senders:')}</span>
                      {senders.status === 'loading' ? (
                        <span>{tr('Loading…')}</span>
                      ) : senders.status === 'error' ? (
                        <span>{tr('Could not load the senders.')}</span>
                      ) : senders.data.length === 0 ? (
                        <span>{tr('none')}</span>
                      ) : (
                        senders.data.map((sender) => {
                          const selected = messageAddresses.some(
                            (entry) =>
                              entry.toLowerCase() === sender.address.toLowerCase(),
                          );
                          return (
                            <button
                              key={sender.address}
                              type="button"
                              className={`winnie-sms-sender btn btn-sm py-0 ${
                                selected ? 'btn-primary' : 'btn-outline-secondary'
                              }`}
                              aria-pressed={selected}
                              title={tr('Last message {{when}}', {
                                when: formatDateTime(sender.last_at),
                              })}
                              onClick={() => toggleMessageAddress(sender.address)}
                            >
                              <span className="font-monospace">{sender.address}</span>
                              <span className="ms-1 opacity-75">· {sender.count}</span>
                            </button>
                          );
                        })
                      )}
                    </div>
                  </div>
                ) : null}

                {tab === 'command' ? (
                  <div className="col-auto">
                    <label className="form-label small mb-1" htmlFor="phone-command-status">
                      {tr('Status')}
                    </label>
                    <select
                      id="phone-command-status"
                      className="form-select form-select-sm"
                      value={commandStatus}
                      onChange={(event) => setCommandStatus(event.target.value)}
                    >
                      <option value="">{tr('Any')}</option>
                      {COMMAND_STATUSES.map((status) => (
                        <option key={status} value={status}>
                          {status}
                        </option>
                      ))}
                    </select>
                  </div>
                ) : null}

                {tab === 'location' ? (
                  <div className="col-auto">
                    <label className="form-label small mb-1 d-block">&nbsp;</label>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-primary"
                      onClick={fillAddresses}
                      disabled={busy}
                    >
                      <i className="bi bi-geo me-1" aria-hidden="true" />
                      {tr('Fill addresses')}
                    </button>
                  </div>
                ) : null}

                <div className="col-auto">
                  <label className="form-label small mb-1" htmlFor="phone-device">
                    {tr('Device')}
                  </label>
                  <select
                    id="phone-device"
                    className="form-select form-select-sm"
                    value={deviceId}
                    onChange={(event) => setDeviceId(event.target.value)}
                    disabled={tab === 'command'}
                  >
                    <option value="all">{tr('All devices')}</option>
                    {devices.map((entry) => (
                      <option key={entry.device.id} value={entry.device.id}>
                        {entry.device.name || entry.device.model || entry.device.id.slice(0, 8)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="col-auto">
                  <label className="form-label small mb-1" htmlFor="phone-limit">
                    {tr('Limit')}
                  </label>
                  <select
                    id="phone-limit"
                    className="form-select form-select-sm"
                    value={limit}
                    onChange={(event) => setLimit(Number(event.target.value))}
                  >
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={200}>200</option>
                    <option value={500}>500</option>
                  </select>
                </div>
              </div>
            </div>

            <div className="card-body p-0">
              {rows.status === 'loading' ? (
                <div className="p-3">
                  <LoadingBlock
                    label={tr('Loading {{what}}…', {
                      what: tr(activeTab.label).toLowerCase(),
                    })}
                  />
                </div>
              ) : rows.status === 'error' ? (
                <div className="p-3">
                  <ErrorBlock error={rows.error} onRetry={rows.reload} />
                </div>
              ) : rows.data.items.length === 0 ? (
                <EmptyState message={emptyMessage(tab)} />
              ) : (
                <PhoneTable
                  data={rows.data}
                  commandDeviceName={commandDeviceName}
                  onOpenMessage={setOpenedMessage}
                />
              )}
            </div>

            <div className="card-footer small text-secondary">
              {rows.status === 'success'
                ? tr(
                    '{{count}} record(s) · newest first · the server returns {{limit}} at most per request',
                    { count: rows.data.items.length, limit },
                  )
                : ' '}
            </div>
          </div>
        </div>
      </div>

      {openedMessage ? (
        <MessageThreadDialog
          message={openedMessage}
          deviceId={deviceId === 'all' ? undefined : deviceId}
          onClose={() => setOpenedMessage(null)}
        />
      ) : null}
    </>
  );
}

interface DeviceCardProps {
  entry: { device: PhoneDevice; counts: { kind: string; count: number }[] };
  busy: boolean;
  onRevoke: () => void;
  onRestore: () => void;
  onWipe: () => void;
}

function DeviceCard({ entry, busy, onRevoke, onRestore, onWipe }: DeviceCardProps) {
  const { device, counts } = entry;
  const battery = infoText(device, 'battery_percent');
  const network = infoText(device, 'network');
  const storage = formatBytes(device.info['storage_free_bytes']);
  const charging = infoText(device, 'battery_charging') === 'yes';
  const label = device.name || device.model || tr('Android phone');

  return (
    <div className="col-12 col-xl-6">
      <div className="card h-100 mb-0">
        <div className="card-header d-flex flex-wrap align-items-center gap-2">
          <h3 className="card-title mb-0">{label}</h3>
          {device.revoked ? (
            <span className="badge text-bg-danger">{tr('Revoked')}</span>
          ) : (
            <span className="badge text-bg-success">{tr('Active')}</span>
          )}
        </div>
        <div className="card-body">
          <div className="small text-secondary">
            {[device.manufacturer, device.model].filter(Boolean).join(' ') || device.platform}
            {device.os_version ? (
              <>
                {' · '}
                {tr('Android {{version}}', { version: device.os_version })}
              </>
            ) : null}
            {device.app_version ? (
              <>
                {' · '}
                {tr('app {{version}}', { version: device.app_version })}
              </>
            ) : null}
          </div>
          <div className="small text-secondary">
            {tr('Last sync: {{sync}} · seen {{seen}}', {
              sync: formatDateTime(device.last_sync_at),
              seen: formatDateTime(device.last_seen_at),
            })}
          </div>

          <div className="mt-2">
            <span className="small text-secondary me-1">{tr('Scopes:')}</span>
            {device.scopes.length === 0 ? (
              <span className="small text-secondary">{tr('none')}</span>
            ) : (
              device.scopes.map((scope) => (
                <span key={scope} className="badge text-bg-info me-1">
                  {scope}
                </span>
              ))
            )}
          </div>

          <div className="mt-2">
            <span className="small text-secondary me-1">{tr('Records:')}</span>
            {counts.map((count) => (
              <span key={count.kind} className="badge text-bg-light me-1">
                {count.kind} {count.count}
              </span>
            ))}
          </div>

          {scopeReads(device).length > 0 ? (
            <div className="mt-2">
              <span className="small text-secondary me-1">{tr('Scope reads:')}</span>
              {scopeReads(device).map(({ scope, read }) => (
                <span
                  key={scope}
                  className={`badge me-1 ${
                    scopeReadBlocked(read) ? 'text-bg-warning' : 'text-bg-light'
                  }`}
                  title={read.error ?? ''}
                >
                  {tr(SCOPE_READ_LABELS[scope] ?? scope)}: {scopeReadValue(read)}
                </span>
              ))}
            </div>
          ) : null}

          <div className="small text-secondary mt-2">
            {[
              battery
                ? tr('battery {{percent}}%{{charging}}', {
                    percent: battery,
                    charging: charging ? tr(' (charging)') : '',
                  })
                : '',
              network ? tr('network {{network}}', { network }) : '',
              storage ? tr('storage {{size}} free', { size: storage }) : '',
            ]
              .filter(Boolean)
              .join(' · ') || tr('No device details beyond the model.')}
          </div>

          <dl className="row small mb-0 mt-3">
            {[
              [tr('Phone number'), infoText(device, 'phone_number')],
              [
                tr('Carrier'),
                [infoText(device, 'carrier') || infoText(device, 'sim_operator'), infoText(device, 'network_type')]
                  .filter(Boolean)
                  .join(' · '),
              ],
              [tr('WiFi'), infoText(device, 'wifi_ssid')],
              [
                tr('IP address'),
                [infoText(device, 'wifi_ip') || infoList(device, 'ipv4'), infoText(device, 'mobile_ip')]
                  .filter(Boolean)
                  .join(' · '),
              ],
              [
                tr('SIM'),
                [infoText(device, 'sim_state'), infoText(device, 'sim_country')]
                  .filter(Boolean)
                  .join(' · '),
              ],
              [tr('Security patch'), infoText(device, 'security_patch')],
              [tr('Android ID'), infoText(device, 'android_id')],
            ]
              .filter(([, value]) => Boolean(value))
              .map(([label, value]) => (
                <Fragment key={label}>
                  <dt className="col-5 col-xl-4 fw-normal text-secondary">{label}</dt>
                  <dd className="col-7 col-xl-8 mb-1 font-monospace">{value}</dd>
                </Fragment>
              ))}
          </dl>

          {device.states.some((state) => state.last_error) ? (
            <div className="small text-danger mt-2">
              {device.states
                .filter((state) => state.last_error)
                .map((state) => `${state.kind}: ${state.last_error}`)
                .join(' · ')}
            </div>
          ) : null}
        </div>
        <div className="card-footer d-flex flex-wrap gap-2">
          {device.revoked ? (
            <button
              type="button"
              className="btn btn-sm btn-outline-success"
              onClick={onRestore}
              disabled={busy}
            >
              {tr('Restore')}
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-outline-warning"
              onClick={onRevoke}
              disabled={busy}
            >
              {tr('Revoke')}
            </button>
          )}
          <button
            type="button"
            className="btn btn-sm btn-outline-danger"
            onClick={onWipe}
            disabled={busy}
          >
            <i className="bi bi-trash me-1" aria-hidden="true" />
            {tr('Delete server data')}
          </button>
        </div>
      </div>
    </div>
  );
}

interface PhoneTableProps {
  data: TabData;
  commandDeviceName: (command: PhoneCommand) => string;
  onOpenMessage: (message: PhoneMessage) => void;
}

function PhoneTable({ data, commandDeviceName, onOpenMessage }: PhoneTableProps) {
  switch (data.kind) {
    case 'calendar':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('When')}</th>
                <th scope="col">{tr('Event')}</th>
                <th scope="col">{tr('Calendar')}</th>
                <th scope="col">{tr('Location')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((event) => (
                <tr key={`${event.device_id}-${event.id}-${event.starts_at ?? ''}`}>
                  <td className="text-nowrap">
                    {formatDateTime(event.starts_at)}
                    <div className="small text-secondary">
                      {event.all_day
                        ? tr('All day')
                        : tr('to {{date}}', {
                            date: formatDateTime(event.ends_at),
                          })}
                    </div>
                  </td>
                  <td>
                    <div className="fw-semibold">{event.title || tr('(untitled)')}</div>
                    {event.description ? (
                      <div className="small text-secondary">
                        {snippet(event.description)}
                      </div>
                    ) : null}
                    <div className="small text-secondary font-monospace">
                      {tr('id {{id}}', { id: event.id })}
                      {event.recurrence ? ` · ${event.recurrence}` : ''}
                    </div>
                  </td>
                  <td>{event.calendar_name || '—'}</td>
                  <td>{event.location || '—'}</td>
                  <td className="text-nowrap">{event.device_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'call':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('When')}</th>
                <th scope="col">{tr('Direction')}</th>
                <th scope="col">{tr('Who')}</th>
                <th scope="col">{tr('Duration')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((call) => (
                <tr key={`${call.device_id}-${call.id}`}>
                  <td className="text-nowrap">{formatDateTime(call.started_at)}</td>
                  <td>
                    <span
                      className={`badge ${
                        call.direction === 'missed' || call.direction === 'rejected'
                          ? 'text-bg-danger'
                          : call.direction === 'outgoing'
                            ? 'text-bg-primary'
                            : 'text-bg-secondary'
                      }`}
                    >
                      {call.direction}
                    </span>
                    {call.is_new ? (
                      <span className="badge text-bg-info ms-1">{tr('new')}</span>
                    ) : null}
                  </td>
                  <td>
                    <div>{call.cached_name || call.number || tr('unknown')}</div>
                    {call.cached_name && call.number ? (
                      <div className="small text-secondary font-monospace">{call.number}</div>
                    ) : null}
                  </td>
                  <td className="text-nowrap">
                    {formatDuration((call.duration_seconds ?? 0) * 1000)}
                  </td>
                  <td className="text-nowrap">{call.device_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'message':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('When')}</th>
                <th scope="col">{tr('Address')}</th>
                <th scope="col">{tr('Direction')}</th>
                <th scope="col">{tr('Message')}</th>
                <th scope="col">{tr('State')}</th>
                <th scope="col">{tr('Device')}</th>
                <th scope="col" style={{ width: '1%' }}>
                  <span className="visually-hidden">{tr('Open')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((message) => (
                <tr
                  key={`${message.device_id}-${message.id}`}
                  className="winnie-sms-row"
                  onClick={() => {
                    // A drag to copy the number must not open the dialog.
                    if (window.getSelection()?.toString()) return;
                    onOpenMessage(message);
                  }}
                >
                  <td className="text-nowrap">{formatDateTime(message.received_at)}</td>
                  <td className="font-monospace">{message.address || tr('unknown')}</td>
                  <td>{messageDirectionLabel(message.direction)}</td>
                  <td>
                    <span
                      className="d-inline-block text-truncate"
                      style={{ maxWidth: '22rem' }}
                      title={message.body}
                    >
                      {snippet(message.body, 200) || tr('(empty)')}
                    </span>
                    {message.subject ? (
                      <div className="small text-secondary">
                        {tr('subject: {{subject}}', { subject: message.subject })}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    <span
                      className={`badge ${
                        message.is_read ? 'text-bg-light' : 'text-bg-warning'
                      }`}
                    >
                      {message.is_read ? tr('read') : tr('unread')}
                    </span>
                  </td>
                  <td className="text-nowrap">{message.device_name}</td>
                  <td className="text-end text-nowrap">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      aria-label={tr('Open every message exchanged with this number')}
                      title={tr('Open every message exchanged with this number')}
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenMessage(message);
                      }}
                    >
                      <i className="bi bi-chat-square-text" aria-hidden="true" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'contact':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('Name')}</th>
                <th scope="col">{tr('Numbers')}</th>
                <th scope="col">{tr('Email')}</th>
                <th scope="col">{tr('Organisation')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((contact) => (
                <tr key={`${contact.device_id}-${contact.id}`}>
                  <td>
                    <span className="fw-semibold">{contact.display_name || tr('(no name)')}</span>
                    {contact.starred ? (
                      <span className="badge text-bg-warning ms-1">{tr('starred')}</span>
                    ) : null}
                  </td>
                  <td className="font-monospace small">
                    {contact.numbers.length === 0 ? '—' : contact.numbers.join(', ')}
                  </td>
                  <td className="small">
                    {contact.emails.length === 0 ? '—' : contact.emails.join(', ')}
                  </td>
                  <td>{contact.organization || '—'}</td>
                  <td className="text-nowrap">{contact.device_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'notification':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('When')}</th>
                <th scope="col">{tr('App')}</th>
                <th scope="col">{tr('Notification')}</th>
                <th scope="col">{tr('Category')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((notification) => (
                <tr key={`${notification.device_id}-${notification.id}`}>
                  <td className="text-nowrap">{formatDateTime(notification.posted_at)}</td>
                  <td>
                    <div>{notification.app_label || notification.package_name}</div>
                    <div className="small text-secondary font-monospace">
                      {notification.package_name}
                    </div>
                  </td>
                  <td>
                    <div className="fw-semibold">{notification.title || tr('(no title)')}</div>
                    <span
                      className="d-inline-block text-truncate small text-secondary"
                      style={{ maxWidth: '34rem' }}
                      title={notification.text}
                    >
                      {snippet(notification.text, 200)}
                    </span>
                  </td>
                  <td>{notification.category || '—'}</td>
                  <td className="text-nowrap">{notification.device_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'location':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('When')}</th>
                <th scope="col">{tr('Address')}</th>
                <th scope="col">{tr('Coordinates')}</th>
                <th scope="col">{tr('Accuracy')}</th>
                <th scope="col">{tr('Provider')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((fix) => {
                const lat = Number(fix.latitude).toFixed(5);
                const lon = Number(fix.longitude).toFixed(5);
                const href = `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=16/${lat}/${lon}`;
                return (
                  <tr key={`${fix.device_id}-${fix.id}`}>
                    <td className="text-nowrap">{formatDateTime(fix.captured_at)}</td>
                    <td style={{ minWidth: '18rem' }}>
                      {fix.address ? (
                        <>
                          <div>{fix.address}</div>
                          <div className="small text-secondary">
                            {[fix.province, fix.city !== fix.province ? fix.city : '', fix.district]
                              .filter(Boolean)
                              .join(' · ')}
                          </div>
                        </>
                      ) : (
                        <span className="text-secondary">{tr('not resolved')}</span>
                      )}
                    </td>
                    <td className="font-monospace">
                      <a href={href} target="_blank" rel="noreferrer">
                        {lat}, {lon}
                      </a>
                    </td>
                    <td className="text-nowrap">
                      {fix.accuracy_m === null ? '—' : `${Math.round(fix.accuracy_m)} m`}
                    </td>
                    <td>{fix.provider || '—'}</td>
                    <td className="text-nowrap">{fix.device_name}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      );
    case 'app':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('App')}</th>
                <th scope="col">{tr('Package')}</th>
                <th scope="col">{tr('Version')}</th>
                <th scope="col">{tr('Kind')}</th>
                <th scope="col">{tr('Updated')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((app) => (
                <tr key={`${app.device_id}-${app.id}`}>
                  <td>{app.label || app.package_name}</td>
                  <td className="small font-monospace">{app.package_name}</td>
                  <td>{app.version_name || '—'}</td>
                  <td>
                    <span className={`badge ${app.is_system ? 'text-bg-secondary' : 'text-bg-light'}`}>
                      {app.is_system ? tr('system') : tr('user')}
                    </span>
                    {app.is_enabled ? null : (
                      <span className="badge text-bg-warning ms-1">{tr('disabled')}</span>
                    )}
                  </td>
                  <td className="text-nowrap text-secondary">
                    {formatDateTime(app.updated_at)}
                  </td>
                  <td className="text-nowrap">{app.device_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'command':
      return (
        <div className="table-responsive">
          <table className="table table-striped table-hover align-middle mb-0">
            <thead>
              <tr>
                <th scope="col">{tr('Created')}</th>
                <th scope="col">{tr('Action')}</th>
                <th scope="col">{tr('Status')}</th>
                <th scope="col">{tr('Result')}</th>
                <th scope="col">{tr('Device')}</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((command) => (
                <tr key={command.id}>
                  <td className="text-nowrap">{formatDateTime(command.created_at)}</td>
                  <td>
                    <div>{command.summary || command.kind}</div>
                    <div className="small text-secondary font-monospace">{command.kind}</div>
                  </td>
                  <td>
                    <span
                      className={`badge ${COMMAND_BADGES[command.status] ?? 'text-bg-secondary'}`}
                    >
                      {command.status}
                    </span>
                  </td>
                  <td className="small">
                    {command.error ? (
                      <span className="text-danger">{command.error}</span>
                    ) : command.result ? (
                      command.result
                    ) : (
                      <span className="text-secondary">—</span>
                    )}
                    <div className="text-secondary">
                      {command.completed_at
                        ? tr('completed {{date}}', {
                            date: formatDateTime(command.completed_at),
                          })
                        : command.approved_at
                          ? tr('approved {{date}}', {
                              date: formatDateTime(command.approved_at),
                            })
                          : tr('expires {{date}}', {
                              date: formatDateTime(command.expires_at),
                            })}
                    </div>
                  </td>
                  <td className="text-nowrap">{commandDeviceName(command)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    default:
      return null;
  }
}

interface MessageThreadDialogProps {
  message: PhoneMessage;
  deviceId?: string;
  onClose: () => void;
}

/**
 * Everything exchanged with one number, oldest first.
 *
 * The clicked row is opened in place: the dialog scrolls to that message and
 * rings its bubble once the thread has loaded, so a conversation of hundreds
 * of messages still answers "which one did I click". The server matches the
 * number in its normalised form, so an inbox row stored as `+86…` and its
 * reply stored without the country code are one conversation.
 */
function MessageThreadDialog({ message, deviceId, onClose }: MessageThreadDialogProps) {
  const thread = useApi(
    () =>
      api.phoneMessageThread({
        address: message.address,
        deviceId,
        limit: 500,
      }),
    [message.address, message.device_id, deviceId],
  );
  const target = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (thread.status !== 'success' || target.current === null) return;
    target.current.scrollIntoView({ block: 'center' });
  }, [thread.status, thread.data]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const items = thread.status === 'success' ? thread.data : [];
  const targetFound = items.some(
    (entry) => entry.id === message.id && entry.device_id === message.device_id,
  );

  return (
    <>
      <div
        className="modal fade show d-block"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="sms-thread-title"
      >
        <div className="modal-dialog modal-lg modal-dialog-centered modal-dialog-scrollable">
          <div className="modal-content">
            <div className="modal-header">
              <div>
                <h5 className="modal-title" id="sms-thread-title">
                  {tr('Conversation with {{address}}', { address: message.address })}
                </h5>
                <div className="small text-secondary">
                  {thread.status === 'success'
                    ? thread.data.length === 1
                      ? tr('{{count}} message, oldest first', { count: 1 })
                      : tr('{{count}} messages, oldest first', {
                          count: thread.data.length,
                        })
                    : tr('Every message exchanged with this number, in both directions.')}
                </div>
              </div>
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Close')}
                onClick={onClose}
              />
            </div>
            <div className="modal-body">
              {thread.status === 'loading' ? (
                <LoadingBlock label={tr('Loading conversation…')} />
              ) : thread.status === 'error' ? (
                <ErrorBlock error={thread.error} onRetry={thread.reload} />
              ) : thread.data.length === 0 ? (
                <EmptyState message={tr('No message with this number.')} />
              ) : (
                <div className="direct-chat direct-chat-primary winnie-sms-thread">
                  {thread.data.map((entry) => {
                    const outgoing = isOutgoingMessage(entry);
                    const current =
                      entry.id === message.id && entry.device_id === message.device_id;
                    return (
                      <div
                        key={`${entry.device_id}-${entry.id}`}
                        ref={current ? target : undefined}
                        className={`direct-chat-msg${outgoing ? ' end' : ''}${
                          current ? ' winnie-sms-target' : ''
                        }`}
                        aria-current={current ? 'true' : undefined}
                      >
                        <div className="direct-chat-infos clearfix">
                          <span
                            className={`direct-chat-name ${
                              outgoing ? 'float-end' : 'float-start'
                            }`}
                          >
                            {messageDirectionLabel(entry.direction)}
                          </span>
                          <span
                            className={`direct-chat-timestamp ${
                              outgoing ? 'float-start' : 'float-end'
                            }`}
                          >
                            {formatDateTime(entry.received_at)}
                            {entry.device_name ? ` · ${entry.device_name}` : ''}
                          </span>
                        </div>
                        <div
                          className={`direct-chat-img d-flex align-items-center justify-content-center ${
                            outgoing ? 'bg-primary text-white' : 'bg-secondary text-white'
                          }`}
                          aria-hidden="true"
                        >
                          <i
                            className={`bi ${
                              outgoing ? 'bi-person-fill' : 'bi-chat-left-text'
                            }`}
                          />
                        </div>
                        <div className="direct-chat-text text-break winnie-pre-line">
                          {entry.body || tr('(empty)')}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              {thread.status === 'success' && items.length > 0 && !targetFound ? (
                <p className="small text-secondary mb-0 mt-3">
                  {tr(
                    'The opened message is older than the {{count}} messages returned here.',
                    { count: items.length },
                  )}
                </p>
              ) : null}
            </div>
            <div className="modal-footer">
              <button type="button" className="btn btn-outline-secondary" onClick={onClose}>
                {tr('Close')}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="modal-backdrop fade show" />
    </>
  );
}
