import { useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { AssistantSkill } from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { useApi } from '../lib/hooks';

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function Badge({ label, className }: { label: string; className: string }) {
  return <span className={`badge ${className}`}>{label}</span>;
}

export function SkillsPage() {
  const result = useApi(() => api.assistantSkills(), []);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // A reload replaces the server view; drop local toggles so the page follows
  // the persisted state again.
  useEffect(() => {
    if (result.status === 'success') {
      setOverrides({});
    }
  }, [result.status, result.data]);

  const loaded = result.status === 'success' ? result.data : null;
  const skills: AssistantSkill[] =
    loaded?.skills.map((skill) =>
      skill.name in overrides
        ? { ...skill, enabled: overrides[skill.name] }
        : skill,
    ) ?? [];

  const available = skills.filter((skill) => skill.available);
  const enabled = available.filter((skill) => skill.enabled);
  const ownerDisabled = available.filter((skill) => !skill.enabled);
  const unavailable = skills.filter((skill) => !skill.available);

  const toggle = async (skill: AssistantSkill, next: boolean) => {
    setBusy(skill.name);
    setError(null);
    setOverrides((current) => ({ ...current, [skill.name]: next }));
    try {
      const updated = await api.setAssistantSkill(skill.name, next);
      setOverrides((current) => ({ ...current, [skill.name]: updated.enabled }));
    } catch (caught) {
      setOverrides((current) => ({ ...current, [skill.name]: !next }));
      setError(describe(caught));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader title="Skills" />
      <div className="app-content">
        <div className="container-fluid">
          <div className="alert alert-info" role="note">
            <i className="bi bi-info-circle-fill me-2" aria-hidden="true" />
            {tr(
              'A skill bundles tools with the prompt instructions that teach their use. Disabling one removes both from the next chat turn. Availability shows whether the service behind it is configured; a disabled skill stays listed so you can see what is off.',
            )}
          </div>

          {error ? (
            <div className="alert alert-danger" role="alert">
              <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
              {error}
            </div>
          ) : null}

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading skills…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                {tr(
                  'Skills are served by the assistant capability. They are available when',
                )}{' '}
                <code>assistant.enabled</code> {tr('is true in')}{' '}
                <code> apps/api/config.yaml</code>.
              </div>
            </>
          ) : skills.length === 0 ? (
            <EmptyState message="No chat skills are registered." />
          ) : (
            <>
              <div className="row g-3 mb-3">
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{skills.length}</h3>
                      <p>{tr('Skills')}</p>
                    </div>
                    <i className="small-box-icon bi bi-puzzle" aria-hidden="true" />
                    <span className="small-box-footer">{tr('Registered bundles')}</span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-success">
                    <div className="inner">
                      <h3>{enabled.length}</h3>
                      <p>{tr('Enabled')}</p>
                    </div>
                    <i className="small-box-icon bi bi-toggle-on" aria-hidden="true" />
                    <span className="small-box-footer">{tr('In the next chat turn')}</span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-warning">
                    <div className="inner">
                      <h3>{ownerDisabled.length}</h3>
                      <p>{tr('Disabled by owner')}</p>
                    </div>
                    <i className="small-box-icon bi bi-toggle-off" aria-hidden="true" />
                    <span className="small-box-footer">{tr('Tools and instructions off')}</span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-secondary">
                    <div className="inner">
                      <h3>{unavailable.length}</h3>
                      <p>{tr('Unavailable')}</p>
                    </div>
                    <i className="small-box-icon bi bi-plug" aria-hidden="true" />
                    <span className="small-box-footer">{tr('Service not configured')}</span>
                  </div>
                </div>
              </div>

              <div className="d-flex flex-wrap align-items-center gap-2 mb-3">
                <h2 className="h5 mb-0">{tr('Registered skills')}</h2>
                <button
                  type="button"
                  className="btn btn-sm btn-outline-secondary ms-auto"
                  onClick={result.reload}
                  disabled={busy !== null}
                >
                  <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                  {tr('Refresh')}
                </button>
              </div>

              <div className="row g-3">
                {skills.map((skill) => {
                  const isBusy = busy === skill.name;
                  const switchDisabled =
                    !skill.toggleable || !skill.available || isBusy;
                  return (
                    <div className="col-12 col-xl-6" key={skill.name}>
                      <div className="card h-100">
                        <div className="card-header d-flex flex-wrap align-items-center gap-2">
                          <h3 className="card-title mb-0">{skill.title}</h3>
                          <code className="small text-secondary">{skill.name}</code>
                          {!skill.toggleable ? (
                            <Badge label={tr('Always on')} className="text-bg-dark" />
                          ) : null}
                          {!skill.scheduled_safe ? (
                            <Badge
                              label={tr('Chat only')}
                              className="text-bg-warning"
                            />
                          ) : null}
                          {!skill.available ? (
                            <Badge label={tr('Unavailable')} className="text-bg-secondary" />
                          ) : null}
                          <div className="ms-auto">
                            <div className="form-check form-switch mb-0">
                              <input
                                className="form-check-input"
                                type="checkbox"
                                role="switch"
                                id={`skill-${skill.name}`}
                                checked={skill.enabled && skill.available}
                                disabled={switchDisabled}
                                aria-label={tr('Enable {{name}}', { name: skill.title })}
                                onChange={(event) =>
                                  void toggle(skill, event.target.checked)
                                }
                              />
                              <label
                                className="form-check-label small"
                                htmlFor={`skill-${skill.name}`}
                              >
                                {isBusy ? tr('Saving…') : tr('Enable')}
                              </label>
                            </div>
                          </div>
                        </div>
                        <div className="card-body">
                          <p className="mb-2">{skill.description}</p>

                          {!skill.available ? (
                            <div className="alert alert-secondary py-2 mb-2" role="note">
                              <i
                                className="bi bi-slash-circle me-1"
                                aria-hidden="true"
                              />
                              {tr('Not available: {{reason}}.', {
                                reason:
                                  skill.unavailable_reason ||
                                  tr('disabled in configuration'),
                              })}
                            </div>
                          ) : null}

                          <div className="d-flex flex-wrap gap-1 mb-2">
                            {skill.tools.length === 0 ? (
                              <span className="text-secondary small">
                                {tr('No tools; prompt instructions only.')}
                              </span>
                            ) : (
                              skill.tools.map((tool) => (
                                <code className="badge text-bg-light border" key={tool}>
                                  {tool}
                                </code>
                              ))
                            )}
                          </div>

                          {skill.permissions.length > 0 ? (
                            <div className="small text-secondary mb-2">
                              {tr('Permissions')}{' '}
                              {skill.permissions.map((permission) => (
                                <code className="me-2" key={permission}>
                                  {permission}
                                </code>
                              ))}
                            </div>
                          ) : null}

                          <div className="small text-secondary">
                            {skill.scheduled_safe
                              ? tr('Available in chat and scheduled task runs.')
                              : tr('Available in chat, not in scheduled task runs.')}
                          </div>

                          {skill.instructions ? (
                            <details className="mt-2">
                              <summary className="small text-secondary">
                                {tr('Prompt instructions')}
                              </summary>
                              <pre className="small mt-2 mb-0 text-wrap">
                                {skill.instructions}
                              </pre>
                            </details>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </>
  );
}
