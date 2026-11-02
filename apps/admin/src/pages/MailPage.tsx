import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type { MailCachedMessage, MailSettings } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

/**
 * The mailbox as the cache sees it.
 *
 * The sync is explicit: it is the only action that touches the server, and it
 * walks every folder. Reading a message costs one download the first time and
 * is free afterwards, so the reading pane can be reopened without a connection.
 */
export function MailPage() {
  const [folder, setFolder] = useState('');
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selected, setSelected] = useState<MailCachedMessage | null>(null);
  const [opening, setOpening] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState('');
  const [actionError, setActionError] = useState('');

  const settings = useApi<MailSettings>(() => api.mailSettings(), []);
  const folders = useApi(() => api.mailFolders(), []);
  const messages = useApi(
    () =>
      api.mailMessages({
        folder,
        q: query,
        unread: unreadOnly || undefined,
        limit: 100,
      }),
    [folder, query, unreadOnly],
  );

  // A search should not fire on every keystroke; 300 ms is long enough to type
  // a word and short enough that the list feels live.
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  const configured =
    settings.status === 'success' && settings.data.enabled && settings.data.can_receive;

  const sync = async () => {
    setSyncing(true);
    setActionError('');
    setNotice('');
    try {
      const result = await api.syncMail();
      const failed = result.errors.length
        ? ' ' + tr('{{failed}} folders failed.', { failed: String(result.errors.length) })
        : '';
      setNotice(
        tr('Synced {{folders}} folders, {{fetched}} new messages.', {
          folders: String(result.folders),
          fetched: String(result.fetched),
        }) + failed,
      );
      folders.reload();
      messages.reload();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setSyncing(false);
    }
  };

  const open = async (message: MailCachedMessage) => {
    setActionError('');
    if (message.has_body && message.body) {
      setSelected(message);
      return;
    }
    setOpening(message.id);
    try {
      setSelected(await api.mailMessage(message.id));
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setOpening('');
    }
  };

  const stats = folders.status === 'success' ? folders.data.stats : null;
  const lastSynced =
    folders.status === 'success'
      ? folders.data.folders
          .map((row) => row.last_synced_at)
          .filter(Boolean)
          .sort()
          .at(-1) ?? ''
      : '';

  return (
    <>
      <PageHeader title="Mail" crumbs={[{ label: 'Settings', to: '/settings' }]} />
      <div className="app-content">
        <div className="container-fluid">
          <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
            <button
              type="button"
              className="btn btn-primary"
              onClick={sync}
              disabled={syncing || !configured}
            >
              {syncing ? (
                <>
                  <span className="spinner-border spinner-border-sm me-2" aria-hidden="true" />
                  {tr('Syncing…')}
                </>
              ) : (
                <>
                  <i className="bi bi-arrow-repeat me-1" aria-hidden="true" />
                  {tr('Sync mailbox')}
                </>
              )}
            </button>
            <span className="small text-secondary">
              {lastSynced
                ? tr('Last synced {{when}}', { when: formatDateTime(lastSynced) })
                : tr('Never synced')}
            </span>
            {stats ? (
              <span className="badge text-bg-light ms-auto">
                {tr('{{messages}} messages · {{unread}} unread', {
                  messages: String(stats.messages),
                  unread: String(stats.unread),
                })}
              </span>
            ) : null}
          </div>

          {notice ? (
            <div className="alert alert-success py-2" role="status">
              {notice}
            </div>
          ) : null}
          {actionError ? (
            <div className="alert alert-danger py-2" role="alert">
              {actionError}
            </div>
          ) : null}

          {settings.status === 'success' && !configured ? (
            <div className="alert alert-info" role="status">
              {tr('No mailbox is configured yet.')}{' '}
              <Link to="/settings">{tr('Open Settings to add one.')}</Link>
            </div>
          ) : null}

          <div className="row g-3">
            <div className="col-lg-3">
              <div className="card">
                <div className="card-header">
                  <h3 className="card-title mb-0">{tr('Folders')}</h3>
                </div>
                {folders.status === 'loading' ? (
                  <div className="card-body py-4 text-center">
                    <div className="spinner-border spinner-border-sm text-primary" aria-hidden="true" />
                  </div>
                ) : folders.status === 'error' ? (
                  <div className="card-body">
                    <ErrorBlock error={folders.error} onRetry={folders.reload} />
                  </div>
                ) : folders.data.folders.length === 0 ? (
                  <div className="card-body small text-secondary">
                    {tr('Sync the mailbox to list its folders.')}
                  </div>
                ) : (
                  <div className="list-group list-group-flush">
                    <button
                      type="button"
                      className={`list-group-item list-group-item-action d-flex justify-content-between align-items-center${
                        folder === '' ? ' active' : ''
                      }`}
                      onClick={() => {
                        setFolder('');
                        setSelected(null);
                      }}
                    >
                      <span>{tr('All mail')}</span>
                    </button>
                    {folders.data.folders.map((row) => (
                      <button
                        key={row.name}
                        type="button"
                        className={`list-group-item list-group-item-action d-flex justify-content-between align-items-center${
                          folder === row.name ? ' active' : ''
                        }`}
                        onClick={() => {
                          setFolder(row.name);
                          setSelected(null);
                        }}
                      >
                        <span className="text-truncate">
                          {row.display_name || row.name}
                          {row.selectable ? null : (
                            <span className="small text-secondary ms-1">
                              ({tr('not selectable')})
                            </span>
                          )}
                        </span>
                        <span className="badge rounded-pill text-bg-secondary">
                          {row.unread > 0 ? row.unread : row.total}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
              {folders.status === 'success'
                ? folders.data.folders
                    .filter((row) => row.last_error)
                    .map((row) => (
                      <div key={row.name} className="alert alert-warning py-2 mt-2 small">
                        <strong>{row.display_name || row.name}</strong>: {row.last_error}
                      </div>
                    ))
                : null}
            </div>

            <div className="col-lg-9">
              {selected ? (
                <div className="card">
                  <div className="card-header d-flex flex-wrap align-items-center gap-2">
                    <h3 className="card-title mb-0 text-truncate">{selected.subject || tr('(no subject)')}</h3>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary ms-auto"
                      onClick={() => setSelected(null)}
                    >
                      <i className="bi bi-arrow-left me-1" aria-hidden="true" />
                      {tr('Back to messages')}
                    </button>
                  </div>
                  <div className="card-body">
                    <dl className="row mb-3 small">
                      <dt className="col-sm-2">{tr('From')}</dt>
                      <dd className="col-sm-10">{selected.sender || '—'}</dd>
                      <dt className="col-sm-2">{tr('To')}</dt>
                      <dd className="col-sm-10">{selected.recipients || '—'}</dd>
                      <dt className="col-sm-2">{tr('Received')}</dt>
                      <dd className="col-sm-10">
                        {selected.date ? formatDateTime(selected.date) : '—'}
                      </dd>
                      <dt className="col-sm-2">{tr('Folder')}</dt>
                      <dd className="col-sm-10">
                        {selected.folder_display || selected.folder} · #{selected.uid}
                      </dd>
                    </dl>
                    <pre className="mb-0 text-wrap font-monospace small">
                      {selected.body || tr('The body is not cached for this message.')}
                    </pre>
                  </div>
                </div>
              ) : (
                <div className="card">
                  <div className="card-header">
                    <div className="d-flex flex-wrap align-items-center gap-2">
                      <h3 className="card-title mb-0">
                        {folder || tr('All mail')}
                      </h3>
                      <div className="ms-auto d-flex flex-wrap align-items-center gap-2">
                        <input
                          className="form-control form-control-sm"
                          type="search"
                          value={search}
                          placeholder={tr('Search subject, sender and body')}
                          onChange={(event) => setSearch(event.target.value)}
                          style={{ width: '18rem' }}
                        />
                        <div className="form-check mb-0">
                          <input
                            className="form-check-input"
                            type="checkbox"
                            id="mail-unread-only"
                            checked={unreadOnly}
                            onChange={(event) => setUnreadOnly(event.target.checked)}
                          />
                          <label className="form-check-label small" htmlFor="mail-unread-only">
                            {tr('Unread only')}
                          </label>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="card-body p-0">
                    {messages.status === 'loading' ? (
                      <LoadingBlock />
                    ) : messages.status === 'error' ? (
                      <div className="p-3">
                        <ErrorBlock error={messages.error} onRetry={messages.reload} />
                      </div>
                    ) : messages.data.length === 0 ? (
                      <EmptyState message={tr('No cached messages match.')} />
                    ) : (
                      <div className="table-responsive">
                        <table className="table table-sm table-hover mb-0 align-middle">
                          <thead>
                            <tr>
                              <th scope="col">{tr('From')}</th>
                              <th scope="col">{tr('Subject')}</th>
                              <th scope="col" className="d-none d-md-table-cell">
                                {tr('Folder')}
                              </th>
                              <th scope="col">{tr('Received')}</th>
                            </tr>
                          </thead>
                          <tbody>
                            {messages.data.map((message) => (
                              <tr
                                key={message.id}
                                role="button"
                                onClick={() => open(message)}
                                style={{ cursor: 'pointer' }}
                              >
                                <td className="text-nowrap small">{message.sender || '—'}</td>
                                <td>
                                  {message.unread ? (
                                    <span className="badge text-bg-primary me-2">
                                      {tr('New')}
                                    </span>
                                  ) : null}
                                  {opening === message.id ? (
                                    <span className="text-secondary small">{tr('Opening…')}</span>
                                  ) : null}
                                  <div>{message.subject || tr('(no subject)')}</div>
                                  <div
                                    className="small text-secondary text-truncate"
                                    style={{ maxWidth: '42rem' }}
                                  >
                                    {message.snippet}
                                  </div>
                                </td>
                                <td className="d-none d-md-table-cell small text-secondary">
                                  {message.folder_display || message.folder}
                                </td>
                                <td className="text-nowrap small text-secondary">
                                  {message.date ? formatDateTime(message.date) : '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
