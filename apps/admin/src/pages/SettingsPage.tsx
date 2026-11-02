import { useEffect, useState, type FormEvent } from 'react';

import { Link } from 'react-router-dom';

import { ApiError, api } from '../api/client';
import type {
  MailProtocol,
  MailSecurity,
  MailSettings,
  MailSettingsInput,
  MailTestTarget,
} from '../api/types';
import { ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { formatDateTime } from '../lib/format';
import { useApi, useTitle } from '../lib/hooks';

const SECURITY_MODES: MailSecurity[] = ['ssl', 'starttls', 'none'];

const SEND_PORTS: Record<MailSecurity, number> = { ssl: 465, starttls: 587, none: 25 };
const IMAP_PORTS: Record<MailSecurity, number> = { ssl: 993, starttls: 143, none: 143 };
const POP3_PORTS: Record<MailSecurity, number> = { ssl: 995, starttls: 110, none: 110 };

const SECURITY_LABELS: Record<MailSecurity, string> = {
  ssl: 'SSL/TLS',
  starttls: 'STARTTLS',
  none: 'No encryption',
};

interface FormState {
  enabled: boolean;
  address: string;
  display_name: string;
  smtpHost: string;
  smtpPort: number;
  smtpSecurity: MailSecurity;
  smtpUsername: string;
  smtpFrom: string;
  smtpFromName: string;
  protocol: MailProtocol;
  receiveHost: string;
  receivePort: number;
  receiveSecurity: MailSecurity;
  receiveUsername: string;
  receiveShared: boolean;
  folder: string;
  maxMessages: number;
  timeoutSeconds: number;
  smtpPassword: string;
  receivePassword: string;
}

function emptyForm(): FormState {
  return {
    enabled: false,
    address: '',
    display_name: '',
    smtpHost: '',
    smtpPort: SEND_PORTS.ssl,
    smtpSecurity: 'ssl',
    smtpUsername: '',
    smtpFrom: '',
    smtpFromName: '',
    protocol: 'imap',
    receiveHost: '',
    receivePort: IMAP_PORTS.ssl,
    receiveSecurity: 'ssl',
    receiveUsername: '',
    receiveShared: true,
    folder: 'INBOX',
    maxMessages: 20,
    timeoutSeconds: 20,
    smtpPassword: '',
    receivePassword: '',
  };
}

function formOf(settings: MailSettings): FormState {
  return {
    enabled: settings.enabled,
    address: settings.address,
    display_name: settings.display_name,
    smtpHost: settings.smtp.host,
    smtpPort: settings.smtp.port,
    smtpSecurity: settings.smtp.security,
    smtpUsername: settings.smtp.username,
    smtpFrom: settings.smtp.from_address,
    smtpFromName: settings.smtp.from_name,
    protocol: settings.receive.protocol,
    receiveHost: settings.receive.host,
    receivePort: settings.receive.port,
    receiveSecurity: settings.receive.security,
    receiveUsername: settings.receive.username,
    receiveShared: settings.receive.use_send_credentials,
    folder: settings.receive.folder || 'INBOX',
    maxMessages: settings.max_messages,
    timeoutSeconds: settings.timeout_seconds,
    smtpPassword: '',
    receivePassword: '',
  };
}

function payloadOf(form: FormState): MailSettingsInput {
  const blankToNull = (value: string) => (value === '' ? null : value);
  return {
    enabled: form.enabled,
    address: form.address.trim(),
    display_name: form.display_name.trim(),
    smtp: {
      host: form.smtpHost.trim(),
      port: form.smtpPort,
      security: form.smtpSecurity,
      username: form.smtpUsername.trim(),
      password: blankToNull(form.smtpPassword),
      from_address: form.smtpFrom.trim(),
      from_name: form.smtpFromName.trim(),
    },
    receive: {
      protocol: form.protocol,
      host: form.receiveHost.trim(),
      port: form.receivePort,
      security: form.receiveSecurity,
      username: form.receiveUsername.trim(),
      password: blankToNull(form.receivePassword),
      use_send_credentials: form.receiveShared,
      folder: form.folder.trim() || 'INBOX',
    },
    max_messages: form.maxMessages,
    timeout_seconds: form.timeoutSeconds,
  };
}

function messageOf(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return tr('The request failed.');
}

function receivePortFor(protocol: MailProtocol, security: MailSecurity): number {
  if (protocol === 'pop3') {
    return POP3_PORTS[security];
  }
  return IMAP_PORTS[security];
}

export function SettingsPage() {
  useTitle('Settings');

  const loaded = useApi(() => api.mailSettings(), []);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (loaded.status === 'success') {
      setForm(formOf(loaded.data));
    }
  }, [loaded.status, loaded.status === 'success' ? loaded.data : null]);

  const update = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const changeSendSecurity = (security: MailSecurity) => {
    setForm((current) => ({
      ...current,
      smtpSecurity: security,
      // Follow the conventional port only while it still is the previous one.
      smtpPort: current.smtpPort === SEND_PORTS[current.smtpSecurity] ? SEND_PORTS[security] : current.smtpPort,
    }));
  };

  const changeReceiveSecurity = (security: MailSecurity) => {
    setForm((current) => ({
      ...current,
      receiveSecurity: security,
      receivePort:
        current.receivePort === receivePortFor(current.protocol, current.receiveSecurity)
          ? receivePortFor(current.protocol, security)
          : current.receivePort,
    }));
  };

  const changeProtocol = (protocol: MailProtocol) => {
    setForm((current) => ({
      ...current,
      protocol,
      receivePort:
        current.receivePort === receivePortFor(current.protocol, current.receiveSecurity)
          ? receivePortFor(protocol, current.receiveSecurity)
          : current.receivePort,
    }));
  };

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    setNotice('');
    setBusy('save');
    try {
      await api.saveMailSettings(payloadOf(form));
      setForm((current) => ({ ...current, smtpPassword: '', receivePassword: '' }));
      setNotice(tr('Mailbox settings saved.'));
      loaded.reload();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy('');
    }
  };

  const runTest = async (target: MailTestTarget) => {
    setError('');
    setNotice('');
    setBusy(target);
    try {
      const result = await api.testMail(target);
      setNotice(result.detail);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy('');
    }
  };

  const sendTest = async () => {
    setError('');
    setNotice('');
    setBusy('send');
    try {
      const result = await api.sendMailTest({
        subject: tr('Winnie mailbox test'),
        body: tr('This message was sent from the Settings page to check the mailbox.'),
      });
      setNotice(result.receipt);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy('');
    }
  };

  const syncMailbox = async () => {
    setError('');
    setNotice('');
    setBusy('sync');
    try {
      // Saving happens before a sync, so this walks the folders the form above
      // just stored. Browsing lives on the Mail page; this only refreshes it.
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
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy('');
    }
  };

  if (loaded.status === 'loading') {
    return (
      <>
        <PageHeader title="Settings" />
        <div className="app-content">
          <div className="container-fluid">
            <LoadingBlock />
          </div>
        </div>
      </>
    );
  }
  if (loaded.status === 'error') {
    return (
      <>
        <PageHeader title="Settings" />
        <div className="app-content">
          <div className="container-fluid">
            <ErrorBlock error={loaded.error} onRetry={loaded.reload} />
          </div>
        </div>
      </>
    );
  }

  const status = loaded.data;

  return (
    <>
      <PageHeader title="Settings" />
      <div className="app-content">
        <div className="container-fluid">
          {error ? (
            <div className="alert alert-danger" role="alert">
              {error}
            </div>
          ) : null}
          {notice ? (
            <div className="alert alert-success" role="status">
              {notice}
            </div>
          ) : null}

          <form onSubmit={save}>
            <div className="card mb-4">
              <div className="card-header">
                <h3 className="card-title">{tr('Mailbox')}</h3>
                <p className="card-subtitle text-secondary small mb-0 mt-1">
                  {tr(
                    'One mailbox per account. The password is stored on disk with mode 0600 and is never returned here.',
                  )}
                </p>
              </div>
              <div className="card-body">
                <div className="row g-3 align-items-end">
                  <div className="col-md-4">
                    <label className="form-label" htmlFor="mail-address">
                      {tr('Email address')}
                    </label>
                    <input
                      id="mail-address"
                      className="form-control"
                      type="email"
                      value={form.address}
                      onChange={(event) => update('address', event.target.value)}
                      placeholder="name@example.com"
                    />
                  </div>
                  <div className="col-md-4">
                    <label className="form-label" htmlFor="mail-display-name">
                      {tr('Display name')}
                    </label>
                    <input
                      id="mail-display-name"
                      className="form-control"
                      value={form.display_name}
                      onChange={(event) => update('display_name', event.target.value)}
                      placeholder="Winnie"
                    />
                  </div>
                  <div className="col-md-4">
                    <div className="form-check form-switch">
                      <input
                        id="mail-enabled"
                        className="form-check-input"
                        type="checkbox"
                        role="switch"
                        checked={form.enabled}
                        onChange={(event) => update('enabled', event.target.checked)}
                      />
                      <label className="form-check-label" htmlFor="mail-enabled">
                        {tr('Use this mailbox for sending')}
                      </label>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="row">
              <div className="col-lg-6">
                <div className="card mb-4 h-100">
                  <div className="card-header">
                    <h3 className="card-title">{tr('Sending (SMTP)')}</h3>
                  </div>
                  <div className="card-body">
                    <div className="row g-3">
                      <div className="col-md-8">
                        <label className="form-label" htmlFor="smtp-host">
                          {tr('Server')}
                        </label>
                        <input
                          id="smtp-host"
                          className="form-control"
                          value={form.smtpHost}
                          onChange={(event) => update('smtpHost', event.target.value)}
                          placeholder="smtp.example.com"
                        />
                      </div>
                      <div className="col-md-4">
                        <label className="form-label" htmlFor="smtp-port">
                          {tr('Port')}
                        </label>
                        <input
                          id="smtp-port"
                          className="form-control"
                          type="number"
                          min={1}
                          max={65535}
                          value={form.smtpPort}
                          onChange={(event) => update('smtpPort', Number(event.target.value))}
                        />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="smtp-security">
                          {tr('Encryption')}
                        </label>
                        <select
                          id="smtp-security"
                          className="form-select"
                          value={form.smtpSecurity}
                          onChange={(event) => changeSendSecurity(event.target.value as MailSecurity)}
                        >
                          {SECURITY_MODES.map((mode) => (
                            <option key={mode} value={mode}>
                              {SECURITY_LABELS[mode]}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="smtp-username">
                          {tr('Username')}
                        </label>
                        <input
                          id="smtp-username"
                          className="form-control"
                          autoComplete="off"
                          value={form.smtpUsername}
                          onChange={(event) => update('smtpUsername', event.target.value)}
                        />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="smtp-password">
                          {tr('Password or authorization code')}
                        </label>
                        <input
                          id="smtp-password"
                          className="form-control"
                          type="password"
                          autoComplete="new-password"
                          value={form.smtpPassword}
                          onChange={(event) => update('smtpPassword', event.target.value)}
                          placeholder={
                            status.smtp.password_set
                              ? tr('Saved — leave blank to keep it')
                              : tr('Not set')
                          }
                        />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="smtp-from">
                          {tr('Sender address (optional)')}
                        </label>
                        <input
                          id="smtp-from"
                          className="form-control"
                          value={form.smtpFrom}
                          onChange={(event) => update('smtpFrom', event.target.value)}
                          placeholder={form.address || tr('Same as the email address')}
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="col-lg-6">
                <div className="card mb-4 h-100">
                  <div className="card-header">
                    <h3 className="card-title">{tr('Receiving (IMAP / POP3)')}</h3>
                  </div>
                  <div className="card-body">
                    <div className="row g-3">
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="receive-protocol">
                          {tr('Protocol')}
                        </label>
                        <select
                          id="receive-protocol"
                          className="form-select"
                          value={form.protocol}
                          onChange={(event) => changeProtocol(event.target.value as MailProtocol)}
                        >
                          <option value="imap">IMAP</option>
                          <option value="pop3">POP3</option>
                          <option value="none">{tr('Off')}</option>
                        </select>
                      </div>
                      <div className="col-md-3">
                        <label className="form-label" htmlFor="receive-port">
                          {tr('Port')}
                        </label>
                        <input
                          id="receive-port"
                          className="form-control"
                          type="number"
                          min={1}
                          max={65535}
                          value={form.receivePort}
                          onChange={(event) => update('receivePort', Number(event.target.value))}
                          disabled={form.protocol === 'none'}
                        />
                      </div>
                      <div className="col-md-3">
                        <label className="form-label" htmlFor="receive-security">
                          {tr('Encryption')}
                        </label>
                        <select
                          id="receive-security"
                          className="form-select"
                          value={form.receiveSecurity}
                          onChange={(event) => changeReceiveSecurity(event.target.value as MailSecurity)}
                          disabled={form.protocol === 'none'}
                        >
                          {SECURITY_MODES.map((mode) => (
                            <option key={mode} value={mode}>
                              {SECURITY_LABELS[mode]}
                            </option>
                          ))}
                        </select>
                      </div>
                      <div className="col-md-8">
                        <label className="form-label" htmlFor="receive-host">
                          {tr('Server')}
                        </label>
                        <input
                          id="receive-host"
                          className="form-control"
                          value={form.receiveHost}
                          onChange={(event) => update('receiveHost', event.target.value)}
                          placeholder={form.protocol === 'pop3' ? 'pop.example.com' : 'imap.example.com'}
                          disabled={form.protocol === 'none'}
                        />
                      </div>
                      <div className="col-md-4">
                        <label className="form-label" htmlFor="receive-folder">
                          {tr('Folder')}
                        </label>
                        <input
                          id="receive-folder"
                          className="form-control"
                          value={form.folder}
                          onChange={(event) => update('folder', event.target.value)}
                          disabled={form.protocol !== 'imap'}
                        />
                      </div>
                      <div className="col-12">
                        <div className="form-check">
                          <input
                            id="receive-shared"
                            className="form-check-input"
                            type="checkbox"
                            checked={form.receiveShared}
                            onChange={(event) => update('receiveShared', event.target.checked)}
                            disabled={form.protocol === 'none'}
                          />
                          <label className="form-check-label" htmlFor="receive-shared">
                            {tr('Use the same username and password as sending')}
                          </label>
                        </div>
                      </div>
                      {!form.receiveShared && form.protocol !== 'none' ? (
                        <>
                          <div className="col-md-6">
                            <label className="form-label" htmlFor="receive-username">
                              {tr('Username')}
                            </label>
                            <input
                              id="receive-username"
                              className="form-control"
                              autoComplete="off"
                              value={form.receiveUsername}
                              onChange={(event) => update('receiveUsername', event.target.value)}
                            />
                          </div>
                          <div className="col-md-6">
                            <label className="form-label" htmlFor="receive-password">
                              {tr('Password or authorization code')}
                            </label>
                            <input
                              id="receive-password"
                              className="form-control"
                              type="password"
                              autoComplete="new-password"
                              value={form.receivePassword}
                              onChange={(event) => update('receivePassword', event.target.value)}
                              placeholder={
                                status.receive.password_set
                                  ? tr('Saved — leave blank to keep it')
                                  : tr('Not set')
                              }
                            />
                          </div>
                        </>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="card mb-4">
              <div className="card-footer d-flex flex-wrap gap-2 align-items-center">
                <button className="btn btn-primary" type="submit" disabled={busy !== ''}>
                  {busy === 'save' ? tr('Saving…') : tr('Save')}
                </button>
                <button
                  className="btn btn-outline-secondary"
                  type="button"
                  onClick={() => runTest('smtp')}
                  disabled={busy !== ''}
                >
                  {busy === 'smtp' ? tr('Testing…') : tr('Test sending')}
                </button>
                <button
                  className="btn btn-outline-secondary"
                  type="button"
                  onClick={() => runTest('receive')}
                  disabled={busy !== '' || form.protocol === 'none'}
                >
                  {busy === 'receive' ? tr('Testing…') : tr('Test receiving')}
                </button>
                <button
                  className="btn btn-outline-secondary"
                  type="button"
                  onClick={sendTest}
                  disabled={busy !== ''}
                >
                  {busy === 'send' ? tr('Sending…') : tr('Send a test message')}
                </button>
                <button
                  className="btn btn-outline-secondary"
                  type="button"
                  onClick={syncMailbox}
                  disabled={busy !== '' || form.protocol === 'none'}
                >
                  {busy === 'sync' ? tr('Syncing…') : tr('Sync mailbox')}
                </button>
                <Link className="btn btn-outline-secondary" to="/mail">
                  {tr('Browse mail')}
                </Link>
                <span className="ms-auto small text-secondary">
                  {status.updated_at
                    ? tr('Saved {{when}}', { when: formatDateTime(status.updated_at) })
                    : tr('Not configured yet')}
                </span>
              </div>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
