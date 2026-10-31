import { useState, type FormEvent, type ReactNode } from 'react';

import { ApiError, api } from '../api/client';
import type {
  AdminUser,
  AdminUserCreate,
  AdminUserList,
  TemporaryPassword,
  UserRole,
} from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

const PAGE_SIZE = 25;

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

export function UsersPage() {
  const { user: currentUser } = useAuth();
  const [queryDraft, setQueryDraft] = useState('');
  const [query, setQuery] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<AdminUserCreate>({
    username: '',
    display_name: '',
    role: 'user',
  });
  const [temporary, setTemporary] = useState<TemporaryPassword | null>(null);

  const result = useApi<AdminUserList>(
    () =>
      api.adminUsers({
        q: query || undefined,
        role: role || undefined,
        isActive: status === '' ? undefined : status === 'active',
        limit: PAGE_SIZE,
        offset,
      }),
    [query, role, status, offset],
  );

  const applySearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setOffset(0);
    setQuery(queryDraft.trim());
  };

  const changeFilter = (setter: (value: string) => void, value: string) => {
    setter(value);
    setOffset(0);
  };

  const createUser = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setActionError(null);
    setBusy('create');
    try {
      const created = await api.createAdminUser({
        username: createForm.username.trim(),
        display_name: createForm.display_name?.trim() || null,
        role: createForm.role,
      });
      setTemporary(created);
      setCreateOpen(false);
      setCreateForm({ username: '', display_name: '', role: 'user' });
      result.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  const resetPassword = async (target: AdminUser) => {
    if (
      !window.confirm(
        tr('Generate a temporary password for {{username}}? Every session will be signed out.', {
          username: target.username,
        }),
      )
    ) {
      return;
    }
    setActionError(null);
    setBusy(`reset-${target.id}`);
    try {
      setTemporary(await api.resetAdminUserPassword(target.id));
      result.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  const changeRole = async (target: AdminUser, nextRole: UserRole) => {
    setActionError(null);
    setBusy(`role-${target.id}`);
    try {
      await api.updateAdminUser(target.id, { role: nextRole });
      result.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  const toggleActive = async (target: AdminUser) => {
    const nextActive = !target.is_active;
    if (
      !nextActive &&
      !window.confirm(
        tr('Deactivate {{username}}? Every session will be signed out.', {
          username: target.username,
        }),
      )
    ) {
      return;
    }
    setActionError(null);
    setBusy(`active-${target.id}`);
    try {
      await api.updateAdminUser(target.id, { is_active: nextActive });
      result.reload();
    } catch (error) {
      setActionError(messageOf(error));
    } finally {
      setBusy(null);
    }
  };

  const data = result.data;

  return (
    <>
      <PageHeader title="Users" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="card mb-4">
            <div className="card-header">
              <h3 className="card-title mb-0">{tr('User management')}</h3>
              <div className="card-tools">
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    setActionError(null);
                    setCreateOpen(true);
                  }}
                >
                  <i className="bi bi-person-plus me-1" aria-hidden="true" />
                  {tr('Create user')}
                </button>
              </div>
            </div>
            <div className="card-body">
              <form className="row g-2 align-items-end mb-3" onSubmit={applySearch}>
                <div className="col-sm-5 col-lg-4">
                  <label className="form-label small" htmlFor="user-search">
                    {tr('Search users')}
                  </label>
                  <input
                    id="user-search"
                    className="form-control form-control-sm"
                    value={queryDraft}
                    onChange={(event) => setQueryDraft(event.target.value)}
                    placeholder={tr('Username or display name')}
                  />
                </div>
                <div className="col-sm-3 col-lg-2">
                  <label className="form-label small" htmlFor="user-role-filter">
                    {tr('Role')}
                  </label>
                  <select
                    id="user-role-filter"
                    className="form-select form-select-sm"
                    value={role}
                    onChange={(event) => changeFilter(setRole, event.target.value)}
                  >
                    <option value="">{tr('All roles')}</option>
                    <option value="admin">{tr('Administrator')}</option>
                    <option value="user">{tr('User')}</option>
                  </select>
                </div>
                <div className="col-sm-3 col-lg-2">
                  <label className="form-label small" htmlFor="user-status-filter">
                    {tr('Status')}
                  </label>
                  <select
                    id="user-status-filter"
                    className="form-select form-select-sm"
                    value={status}
                    onChange={(event) => changeFilter(setStatus, event.target.value)}
                  >
                    <option value="">{tr('All statuses')}</option>
                    <option value="active">{tr('Active')}</option>
                    <option value="inactive">{tr('Inactive')}</option>
                  </select>
                </div>
                <div className="col-sm-2 col-lg-2 d-flex gap-2">
                  <button type="submit" className="btn btn-outline-secondary btn-sm">
                    <i className="bi bi-search me-1" aria-hidden="true" />
                    {tr('Search')}
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline-secondary btn-sm"
                    onClick={() => {
                      setQueryDraft('');
                      setQuery('');
                      setRole('');
                      setStatus('');
                      setOffset(0);
                    }}
                  >
                    {tr('Reset')}
                  </button>
                </div>
              </form>

              {actionError ? (
                <div className="alert alert-danger py-2" role="alert">
                  {actionError}
                </div>
              ) : null}

              {result.status === 'loading' ? (
                <LoadingBlock label={tr('Loading users…')} />
              ) : result.status === 'error' ? (
                <ErrorBlock error={result.error} onRetry={result.reload} />
              ) : data && data.items.length === 0 ? (
                <EmptyState message={tr('No users match these filters.')} />
              ) : (
                <>
                  <div className="table-responsive">
                    <table className="table table-striped table-hover align-middle mb-0">
                      <thead>
                        <tr>
                          <th scope="col">{tr('Username')}</th>
                          <th scope="col">{tr('Display name')}</th>
                          <th scope="col">{tr('Role')}</th>
                          <th scope="col">{tr('Status')}</th>
                          <th scope="col">{tr('Created')}</th>
                          <th scope="col">{tr('Last session')}</th>
                          <th scope="col" className="text-end">
                            {tr('Actions')}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {data?.items.map((item) => {
                          const isSelf = item.id === currentUser?.id;
                          const rowBusy =
                            busy?.endsWith(item.id) === true || busy === 'create';
                          return (
                            <tr key={item.id}>
                              <td>
                                <div className="fw-semibold font-monospace">
                                  {item.username}
                                </div>
                                {isSelf ? (
                                  <span className="badge text-bg-light text-dark">
                                    {tr('You')}
                                  </span>
                                ) : null}
                              </td>
                              <td>{item.display_name || '—'}</td>
                              <td>
                                <select
                                  className="form-select form-select-sm"
                                  value={item.role}
                                  disabled={rowBusy || isSelf}
                                  aria-label={tr('Role for {{username}}', {
                                    username: item.username,
                                  })}
                                  onChange={(event) =>
                                    void changeRole(item, event.target.value as UserRole)
                                  }
                                >
                                  <option value="admin">{tr('Administrator')}</option>
                                  <option value="user">{tr('User')}</option>
                                </select>
                              </td>
                              <td>
                                <span
                                  className={`badge ${
                                    item.is_active ? 'text-bg-success' : 'text-bg-secondary'
                                  }`}
                                >
                                  {item.is_active ? tr('Active') : tr('Inactive')}
                                </span>
                              </td>
                              <td className="text-nowrap text-secondary">
                                {formatDateTime(item.created_at)}
                              </td>
                              <td className="text-nowrap text-secondary">
                                {formatDateTime(item.last_session_at)}
                                <span className="d-block small">
                                  {tr('{{count}} active', {
                                    count: item.active_sessions,
                                  })}
                                </span>
                              </td>
                              <td className="text-end text-nowrap">
                                <button
                                  type="button"
                                  className="btn btn-outline-secondary btn-sm me-1"
                                  disabled={rowBusy}
                                  onClick={() => void resetPassword(item)}
                                >
                                  {tr('Reset password')}
                                </button>
                                <button
                                  type="button"
                                  className={`btn btn-sm ${
                                    item.is_active
                                      ? 'btn-outline-danger'
                                      : 'btn-outline-success'
                                  }`}
                                  disabled={rowBusy || isSelf}
                                  onClick={() => void toggleActive(item)}
                                >
                                  {item.is_active ? tr('Deactivate') : tr('Activate')}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3">
                    <span className="small text-secondary">
                      {tr('Showing {{from}}–{{to}} of {{total}}', {
                        from: data && data.total > 0 ? offset + 1 : 0,
                        to: data ? Math.min(offset + PAGE_SIZE, data.total) : 0,
                        total: data?.total ?? 0,
                      })}
                    </span>
                    <div className="btn-group">
                      <button
                        type="button"
                        className="btn btn-outline-secondary btn-sm"
                        disabled={offset === 0}
                        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                      >
                        {tr('Previous')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-secondary btn-sm"
                        disabled={!data || offset + PAGE_SIZE >= data.total}
                        onClick={() => setOffset(offset + PAGE_SIZE)}
                      >
                        {tr('Next')}
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {createOpen ? (
        <Modal title={tr('Create user')} onClose={() => setCreateOpen(false)}>
          <form onSubmit={createUser}>
            <div className="modal-body">
              <div className="mb-3">
                <label className="form-label" htmlFor="create-username">
                  {tr('Username')}
                </label>
                <input
                  id="create-username"
                  className="form-control"
                  value={createForm.username}
                  onChange={(event) =>
                    setCreateForm({ ...createForm, username: event.target.value })
                  }
                  pattern="[A-Za-z0-9_.-]+"
                  minLength={3}
                  maxLength={64}
                  required
                />
                <div className="form-text">
                  {tr('3–64 characters: letters, digits, dot, underscore or hyphen.')}
                </div>
              </div>
              <div className="mb-3">
                <label className="form-label" htmlFor="create-display-name">
                  {tr('Display name')}
                </label>
                <input
                  id="create-display-name"
                  className="form-control"
                  value={createForm.display_name ?? ''}
                  onChange={(event) =>
                    setCreateForm({ ...createForm, display_name: event.target.value })
                  }
                  maxLength={128}
                />
              </div>
              <div className="mb-0">
                <label className="form-label" htmlFor="create-role">
                  {tr('Role')}
                </label>
                <select
                  id="create-role"
                  className="form-select"
                  value={createForm.role}
                  onChange={(event) =>
                    setCreateForm({
                      ...createForm,
                      role: event.target.value as UserRole,
                    })
                  }
                >
                  <option value="user">{tr('User')}</option>
                  <option value="admin">{tr('Administrator')}</option>
                </select>
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={() => setCreateOpen(false)}
                disabled={busy === 'create'}
              >
                {tr('Cancel')}
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy === 'create'}>
                {busy === 'create' ? tr('Creating…') : tr('Create user')}
              </button>
            </div>
          </form>
        </Modal>
      ) : null}

      {temporary ? (
        <Modal
          title={tr('Temporary password')}
          onClose={() => setTemporary(null)}
          closeLabel={tr('I saved the password')}
        >
          <div className="modal-body">
            <p>
              {tr(
                'Give this password to {{username}} through a secure channel. It is shown once and cannot be recovered.',
                { username: temporary.user.username },
              )}
            </p>
            <div className="input-group">
              <input
                className="form-control font-monospace"
                readOnly
                value={temporary.temporary_password}
                aria-label={tr('Temporary password')}
              />
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={() => {
                  void navigator.clipboard?.writeText(temporary.temporary_password);
                }}
              >
                {tr('Copy')}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}

function Modal({
  title,
  onClose,
  children,
  closeLabel,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  closeLabel?: string;
}) {
  return (
    <>
      <div className="modal fade show d-block" tabIndex={-1} role="dialog" aria-modal="true">
        <div className="modal-dialog modal-dialog-centered" role="document">
          <div className="modal-content">
            <div className="modal-header">
              <h5 className="modal-title">{title}</h5>
              <button
                type="button"
                className="btn-close"
                aria-label={closeLabel ?? tr('Close')}
                onClick={onClose}
              />
            </div>
            {children}
            {closeLabel ? (
              <div className="modal-footer">
                <button type="button" className="btn btn-primary" onClick={onClose}>
                  {closeLabel}
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </div>
      <div className="modal-backdrop fade show" />
    </>
  );
}
