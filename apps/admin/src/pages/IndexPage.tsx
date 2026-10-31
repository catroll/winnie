import { useEffect, useState } from 'react';

import { api, ApiError } from '../api/client';
import type {
  IndexBuildResult,
  IndexEditableConfig,
  IndexStatus,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { formatDateTime } from '../lib/format';
import { useApi } from '../lib/hooks';

type Message = { kind: 'success' | 'danger'; text: string } | null;

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function IndexPage() {
  const result = useApi(
    () =>
      Promise.all([api.indexStatus(), api.indexConfig()]).then(([status, config]) => ({
        status,
        config,
      })),
    [],
  );

  const [draft, setDraft] = useState<IndexEditableConfig | null>(null);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastBuild, setLastBuild] = useState<IndexBuildResult | null>(null);

  const loaded = result.status === 'success' ? result.data : null;

  useEffect(() => {
    if (result.status === 'success') {
      setDraft(structuredClone(result.data.config.config));
    }
  }, [result.status, result.data]);

  const status: IndexStatus | null = loaded?.status ?? null;
  const configPath = loaded?.config.path ?? status?.config_path ?? '';

  const updateRoot = (index: number, patch: Partial<IndexEditableConfig['roots'][number]>) => {
    setDraft((current) =>
      current
        ? {
            ...current,
            roots: current.roots.map((root, position) =>
              position === index ? { ...root, ...patch } : root,
            ),
          }
        : current,
    );
  };

  const removeRoot = (index: number) => {
    setDraft((current) =>
      current
        ? { ...current, roots: current.roots.filter((_, position) => position !== index) }
        : current,
    );
  };

  const addRoot = () => {
    setDraft((current) =>
      current
        ? {
            ...current,
            roots: [
              ...current.roots,
              { name: '', path: '', include: [], exclude: [], watch: false, tier: 'lexical' },
            ],
          }
        : current,
    );
  };

  const updateSection = <K extends 'scan' | 'index' | 'retrieval'>(
    section: K,
    patch: Partial<IndexEditableConfig[K]>,
  ) => {
    setDraft((current) =>
      current ? { ...current, [section]: { ...current[section], ...patch } } : current,
    );
  };

  const save = async () => {
    if (!draft) {
      return;
    }
    const invalid = draft.roots.find((root) => !root.name.trim() || !root.path.trim());
    if (invalid) {
      setMessage({ kind: 'danger', text: 'Every root needs a name and an absolute path.' });
      return;
    }
    setBusy('save');
    setMessage(null);
    try {
      const saved = await api.saveIndexConfig(draft);
      setDraft(structuredClone(saved.config));
      setMessage({
        kind: 'success',
        text: 'Saved. The daemon reloaded the configuration; run a build to apply new roots.',
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const runBuild = async (dryRun: boolean) => {
    setBusy(dryRun ? 'dry' : 'build');
    setMessage(null);
    try {
      const { build } = await api.buildIndex(dryRun);
      setLastBuild(build);
      setMessage({
        kind: 'success',
        text: dryRun
          ? `Dry run: ${build.added} new, ${build.sealed} sealed, nothing written.`
          : `Build: ${build.added} added, ${build.changed} changed, ${build.deleted} removed, ${build.chunks} chunks.`,
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const installSkill = async () => {
    setBusy('skill');
    setMessage(null);
    try {
      const response = await api.indexSkill('install');
      setMessage({ kind: 'success', text: `Skill written to ${response.path}` });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const disabled = busy !== null;

  return (
    <>
      <PageHeader title="Document index" />
      <div className="app-content">
        <div className="container-fluid">
          {message ? (
            <div className={`alert alert-${message.kind}`} role="alert">
              {message.text}
            </div>
          ) : null}

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading index status…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                The console reaches the index through the Winnie API. If the daemon is not
                running, start it with <code>systemctl --user start winnie-index</code> or
                <code> make index-serve</code>.
              </div>
            </>
          ) : !status || !draft ? (
            <EmptyState message="The index returned no status." />
          ) : (
            <>
              <div className="row g-3 mb-3">
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{status.documents.indexed ?? 0}</h3>
                      <p>Indexed documents</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-text" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-success">
                    <div className="inner">
                      <h3>{status.chunks}</h3>
                      <p>Chunks</p>
                    </div>
                    <i className="small-box-icon bi bi-collection" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-warning">
                    <div className="inner">
                      <h3>{status.documents.sealed ?? 0}</h3>
                      <p>Sealed files</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-x" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-info">
                    <div className="inner">
                      <h3>{formatBytes(status.db_bytes)}</h3>
                      <p>Catalog size</p>
                    </div>
                    <i className="small-box-icon bi bi-hdd" aria-hidden="true" />
                  </div>
                </div>
              </div>

              <div className="row g-3">
                <div className="col-lg-8">
                  <div className="card">
                    <div className="card-header d-flex flex-wrap align-items-center gap-2">
                      <h3 className="card-title mb-0">Roots</h3>
                      <div className="ms-auto d-flex gap-2">
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-primary"
                          onClick={addRoot}
                          disabled={disabled}
                        >
                          <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                          Add root
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={() => void save()}
                          disabled={disabled}
                        >
                          {busy === 'save' ? 'Saving…' : 'Save configuration'}
                        </button>
                      </div>
                    </div>
                    <div className="card-body p-0">
                      {draft.roots.length === 0 ? (
                        <div className="p-3">
                          <EmptyState message="No roots yet. Add one to start indexing." />
                        </div>
                      ) : (
                        <div className="table-responsive">
                          <table className="table align-middle mb-0">
                            <thead>
                              <tr>
                                <th scope="col">Name</th>
                                <th scope="col">Path</th>
                                <th scope="col">Include</th>
                                <th scope="col">Exclude</th>
                                <th scope="col">Watch</th>
                                <th scope="col" className="text-end">
                                  Actions
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {draft.roots.map((root, index) => (
                                <tr key={`${root.name}-${index}`}>
                                  <td>
                                    <input
                                      className="form-control form-control-sm"
                                      value={root.name}
                                      aria-label={`Root ${index + 1} name`}
                                      onChange={(event) =>
                                        updateRoot(index, { name: event.target.value })
                                      }
                                    />
                                  </td>
                                  <td>
                                    <input
                                      className="form-control form-control-sm"
                                      style={{ minWidth: '220px' }}
                                      value={root.path}
                                      aria-label={`Root ${index + 1} path`}
                                      placeholder="/home/you/notes"
                                      onChange={(event) =>
                                        updateRoot(index, { path: event.target.value })
                                      }
                                    />
                                  </td>
                                  <td>
                                    <input
                                      className="form-control form-control-sm"
                                      style={{ minWidth: '150px' }}
                                      value={(root.include ?? []).join(', ')}
                                      aria-label={`Root ${index + 1} include globs`}
                                      placeholder="optional, comma separated"
                                      onChange={(event) =>
                                        updateRoot(index, { include: splitList(event.target.value) })
                                      }
                                    />
                                  </td>
                                  <td>
                                    <input
                                      className="form-control form-control-sm"
                                      style={{ minWidth: '150px' }}
                                      value={(root.exclude ?? []).join(', ')}
                                      aria-label={`Root ${index + 1} exclude globs`}
                                      placeholder="optional, comma separated"
                                      onChange={(event) =>
                                        updateRoot(index, { exclude: splitList(event.target.value) })
                                      }
                                    />
                                  </td>
                                  <td>
                                    <div className="form-check form-switch">
                                      <input
                                        className="form-check-input"
                                        type="checkbox"
                                        checked={Boolean(root.watch)}
                                        aria-label={`Root ${index + 1} watch`}
                                        onChange={(event) =>
                                          updateRoot(index, { watch: event.target.checked })
                                        }
                                      />
                                    </div>
                                  </td>
                                  <td className="text-end">
                                    <button
                                      type="button"
                                      className="btn btn-sm btn-outline-danger"
                                      onClick={() => removeRoot(index)}
                                      disabled={disabled}
                                      aria-label={`Remove root ${root.name || index + 1}`}
                                    >
                                      <i className="bi bi-trash" aria-hidden="true" />
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                    <div className="card-footer text-secondary small">
                      Saving rewrites the YAML file and reloads the daemon. Removing a root also
                      removes its documents from the index; the files on disk are untouched.
                      Format support in this version is <code>txt</code> and <code>md</code>.
                    </div>
                  </div>
                </div>

                <div className="col-lg-4">
                  <div className="card mb-3">
                    <div className="card-header">
                      <h3 className="card-title">Sweep and limits</h3>
                    </div>
                    <div className="card-body">
                      <div className="mb-3">
                        <label className="form-label" htmlFor="scan-interval">
                          Sweep interval (seconds)
                        </label>
                        <input
                          id="scan-interval"
                          type="number"
                          min={5}
                          max={86400}
                          className="form-control"
                          value={draft.scan.interval_seconds}
                          onChange={(event) =>
                            updateSection('scan', {
                              interval_seconds: Number(event.target.value) || 5,
                            })
                          }
                        />
                      </div>
                      <div className="mb-3">
                        <label className="form-label" htmlFor="max-file">
                          Max file size (MB)
                        </label>
                        <input
                          id="max-file"
                          type="number"
                          min={1}
                          max={512}
                          className="form-control"
                          value={draft.index.max_file_mb}
                          onChange={(event) =>
                            updateSection('index', {
                              max_file_mb: Number(event.target.value) || 1,
                            })
                          }
                        />
                      </div>
                      <div className="row">
                        <div className="col-6 mb-3">
                          <label className="form-label" htmlFor="result-limit">
                            Results
                          </label>
                          <input
                            id="result-limit"
                            type="number"
                            min={1}
                            max={50}
                            className="form-control"
                            value={draft.retrieval.limit}
                            onChange={(event) =>
                              updateSection('retrieval', {
                                limit: Number(event.target.value) || 1,
                              })
                            }
                          />
                        </div>
                        <div className="col-6 mb-3">
                          <label className="form-label" htmlFor="per-doc">
                            Chunks per document
                          </label>
                          <input
                            id="per-doc"
                            type="number"
                            min={1}
                            max={10}
                            className="form-control"
                            value={draft.retrieval.max_per_doc}
                            onChange={(event) =>
                              updateSection('retrieval', {
                                max_per_doc: Number(event.target.value) || 1,
                              })
                            }
                          />
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="card mb-3">
                    <div className="card-header">
                      <h3 className="card-title">Maintenance</h3>
                    </div>
                    <div className="card-body d-grid gap-2">
                      <button
                        type="button"
                        className="btn btn-outline-secondary"
                        onClick={() => void runBuild(true)}
                        disabled={disabled}
                      >
                        {busy === 'dry' ? 'Checking…' : 'Dry run'}
                      </button>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={() => void runBuild(false)}
                        disabled={disabled}
                      >
                        {busy === 'build' ? 'Building…' : 'Build now'}
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-primary"
                        onClick={() => void installSkill()}
                        disabled={disabled}
                      >
                        {busy === 'skill' ? 'Writing…' : 'Install Agent Skill'}
                      </button>
                    </div>
                    <div className="card-footer small text-secondary">
                      Last sweep: {formatDateTime(status.last_sweep_at)}
                      {status.skill.installed ? (
                        <span className="badge text-bg-success ms-2">skill installed</span>
                      ) : (
                        <span className="badge text-bg-warning ms-2">skill missing</span>
                      )}
                    </div>
                  </div>

                  <div className="card">
                    <div className="card-header">
                      <h3 className="card-title">Endpoints</h3>
                    </div>
                    <div className="card-body small">
                      <div className="mb-2">
                        <span className="text-secondary d-block">MCP</span>
                        <code className="text-break">{status.endpoints.mcp}</code>
                      </div>
                      <div className="mb-2">
                        <span className="text-secondary d-block">HTTP</span>
                        <code className="text-break">{status.endpoints.http}</code>
                      </div>
                      <div className="mb-2">
                        <span className="text-secondary d-block">Catalog</span>
                        <code className="text-break">{status.db_path}</code>
                      </div>
                      <div>
                        <span className="text-secondary d-block">Config</span>
                        <code className="text-break">{configPath}</code>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {status.roots.length > 0 ? (
                <div className="card mt-3">
                  <div className="card-header">
                    <h3 className="card-title">Indexed roots</h3>
                  </div>
                  <div className="card-body p-0">
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Root</th>
                            <th scope="col">Path</th>
                            <th scope="col" className="text-end">
                              Documents
                            </th>
                            <th scope="col" className="text-end">
                              Chunks
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {status.roots.map((root) => (
                            <tr key={root.name}>
                              <td className="fw-semibold">{root.name}</td>
                              <td className="text-break">{root.path}</td>
                              <td className="text-end">{root.documents}</td>
                              <td className="text-end">{root.chunks}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              ) : null}

              {lastBuild || status.last_build ? (
                <div className="card mt-3">
                  <div className="card-header">
                    <h3 className="card-title">Last build</h3>
                  </div>
                  <div className="card-body">
                    {(() => {
                      const build = lastBuild ?? status.last_build;
                      if (!build) {
                        return null;
                      }
                      return (
                        <>
                          <p className="mb-2">
                            {build.dry_run ? 'Dry run' : 'Build'} — {build.added} added,{' '}
                            {build.changed} changed, {build.unchanged} unchanged,{' '}
                            {build.deleted} removed, {build.failed} failed, {build.sealed} sealed,{' '}
                            {build.chunks} chunks.
                          </p>
                          {build.errors && build.errors.length > 0 ? (
                            <ul className="mb-0 small text-danger">
                              {build.errors.map((error) => (
                                <li key={error}>{error}</li>
                              ))}
                            </ul>
                          ) : null}
                        </>
                      );
                    })()}
                  </div>
                </div>
              ) : null}

              {status.failures.length > 0 ? (
                <div className="card mt-3">
                  <div className="card-header">
                    <h3 className="card-title">Failures</h3>
                  </div>
                  <div className="card-body p-0">
                    <div className="table-responsive">
                      <table className="table align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Path</th>
                            <th scope="col">Error</th>
                          </tr>
                        </thead>
                        <tbody>
                          {status.failures.map((failure) => (
                            <tr key={failure.path}>
                              <td className="text-break">{failure.path}</td>
                              <td className="text-danger text-break">{failure.error}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>
    </>
  );
}
