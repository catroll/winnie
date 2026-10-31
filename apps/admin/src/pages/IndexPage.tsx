import { Fragment, useEffect, useState } from 'react';

import { api, ApiError } from '../api/client';
import type {
  IndexBuildResult,
  IndexEditableConfig,
  IndexSearchResponse,
  IndexStatus,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
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
  const [query, setQuery] = useState('');
  const [rootFilter, setRootFilter] = useState('');
  const [since, setSince] = useState('');
  const [searchLimit, setSearchLimit] = useState(8);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<IndexSearchResponse | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);

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
      setMessage({
        kind: 'danger',
        text: tr('Every root needs a name and an absolute path.'),
      });
      return;
    }
    setBusy('save');
    setMessage(null);
    try {
      const saved = await api.saveIndexConfig(draft);
      setDraft(structuredClone(saved.config));
      setMessage({
        kind: 'success',
        text: tr(
          'Saved. The daemon reloaded the configuration; run a build to apply new roots.',
        ),
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
          ? tr('Dry run: {{added}} new, {{sealed}} sealed, nothing written.', {
              added: build.added,
              sealed: build.sealed,
            })
          : tr(
              'Build: {{added}} added, {{changed}} changed, {{deleted}} removed, {{chunks}} chunks.',
              {
                added: build.added,
                changed: build.changed,
                deleted: build.deleted,
                chunks: build.chunks,
              },
            ),
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
      setMessage({
        kind: 'success',
        text: tr('Skill written to {{path}}', { path: response.path }),
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const runSearch = async (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      return;
    }
    setSearching(true);
    setSearchError(null);
    try {
      const response = await api.searchIndex({
        query: trimmed,
        roots: rootFilter ? [rootFilter] : undefined,
        since: since.trim() || undefined,
        limit: searchLimit,
      });
      setSearchResult(response);
    } catch (error) {
      setSearchResult(null);
      setSearchError(describe(error));
    } finally {
      setSearching(false);
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
                {tr(
                  'The console reaches the index through the Winnie API. If the daemon is not running, start it with',
                )}{' '}
                <code>systemctl --user start winnie-index</code> {tr('or')}
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
                      <p>{tr('Indexed documents')}</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-text" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-success">
                    <div className="inner">
                      <h3>{status.chunks}</h3>
                      <p>{tr('Chunks')}</p>
                    </div>
                    <i className="small-box-icon bi bi-collection" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-warning">
                    <div className="inner">
                      <h3>{status.documents.sealed ?? 0}</h3>
                      <p>{tr('Sealed files')}</p>
                    </div>
                    <i className="small-box-icon bi bi-file-earmark-x" aria-hidden="true" />
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-info">
                    <div className="inner">
                      <h3>{formatBytes(status.db_bytes)}</h3>
                      <p>{tr('Catalog size')}</p>
                    </div>
                    <i className="small-box-icon bi bi-hdd" aria-hidden="true" />
                  </div>
                </div>
              </div>

              <div className="row g-3 mb-3">
                <div className="col-12">
                  <div className="card">
                    <div className="card-header d-flex flex-wrap align-items-center gap-2">
                      <h3 className="card-title mb-0">{tr('Search test')}</h3>
                      <span className="ms-auto small text-secondary">
                        {tr('Every matching chunk, not the collapsed retrieval view')}
                      </span>
                    </div>
                    <div className="card-body">
                      <form className="row g-2 align-items-end" onSubmit={(event) => void runSearch(event)}>
                        <div className="col-12 col-lg-5">
                          <label className="form-label" htmlFor="search-query">
                            {tr('Query')}
                          </label>
                          <input
                            id="search-query"
                            className="form-control"
                            value={query}
                            placeholder="例如 部署，或 MCP server"
                            onChange={(event) => setQuery(event.target.value)}
                          />
                        </div>
                        <div className="col-6 col-lg-2">
                          <label className="form-label" htmlFor="search-root">
                            {tr('Root')}
                          </label>
                          <select
                            id="search-root"
                            className="form-select"
                            value={rootFilter}
                            onChange={(event) => setRootFilter(event.target.value)}
                          >
                            <option value="">{tr('All roots')}</option>
                            {draft.roots.map((root) => (
                              <option key={root.name} value={root.name}>
                                {root.name}
                              </option>
                            ))}
                          </select>
                        </div>
                        <div className="col-6 col-lg-2">
                          <label className="form-label" htmlFor="search-since">
                            {tr('Modified since')}
                          </label>
                          <input
                            id="search-since"
                            className="form-control"
                            value={since}
                            placeholder="7d / 24h"
                            onChange={(event) => setSince(event.target.value)}
                          />
                        </div>
                        <div className="col-6 col-lg-1">
                          <label className="form-label" htmlFor="search-limit">
                            {tr('Limit')}
                          </label>
                          <input
                            id="search-limit"
                            type="number"
                            min={1}
                            max={50}
                            className="form-control"
                            value={searchLimit}
                            onChange={(event) =>
                              setSearchLimit(Number(event.target.value) || 8)
                            }
                          />
                        </div>
                        <div className="col-6 col-lg-2">
                          <button type="submit" className="btn btn-primary w-100" disabled={searching}>
                            {searching ? tr('Searching…') : tr('Search')}
                          </button>
                        </div>
                      </form>

                      {searchError ? (
                        <div className="alert alert-danger mt-3 mb-0" role="alert">
                          {searchError}
                        </div>
                      ) : null}

                      {searchResult ? (
                        <div className="mt-3">
                          <div className="d-flex flex-wrap align-items-center gap-2 small">
                            <span className="badge text-bg-primary">
                              {tr('{{chunks}} chunks in {{files}} files', {
                                chunks: searchResult.returned,
                                files: searchResult.files.length,
                              })}
                            </span>
                            <span className="text-secondary">
                              {tr(
                                '{{candidates}} candidates · {{elapsed}} ms · {{dropped}} dropped',
                                {
                                  candidates: searchResult.candidates,
                                  elapsed: searchResult.elapsed_ms,
                                  dropped: searchResult.dropped,
                                },
                              )}
                            </span>
                            <span className="text-secondary">{tr('expression')}</span>
                            <code>{searchResult.expression ?? '—'}</code>
                            {searchResult.terms.map((term) => (
                              <span className="badge text-bg-light border" key={term}>
                                {term}
                              </span>
                            ))}
                          </div>

                          {searchResult.hint ? (
                            <div className="alert alert-warning mt-2 mb-0" role="note">
                              {searchResult.hint}
                            </div>
                          ) : null}

                          {searchResult.hits.length === 0 ? (
                            <p className="text-secondary mt-2 mb-0">{tr('No chunk matched.')}</p>
                          ) : (
                            <div className="mt-2">
                              {searchResult.files.map((file) => {
                                const hits = searchResult.hits.filter(
                                  (hit) => hit.root === file.root && hit.path === file.path,
                                );
                                return (
                                  <div
                                    className="border rounded mb-2"
                                    key={`${file.root}:${file.path}`}
                                  >
                                    <div className="p-2 d-flex flex-wrap align-items-center gap-2">
                                      <span className="badge text-bg-secondary">{file.root}</span>
                                      <code className="text-break">{file.path}</code>
                                      <span className="ms-auto small text-secondary">
                                        {file.chunks === 1
                                          ? tr(
                                              '{{chunks}} chunk · best {{score}} · lower is better',
                                              {
                                                chunks: file.chunks,
                                                score: file.best_score.toFixed(2),
                                              },
                                            )
                                          : tr(
                                              '{{chunks}} chunks · best {{score}} · lower is better',
                                              {
                                                chunks: file.chunks,
                                                score: file.best_score.toFixed(2),
                                              },
                                            )}
                                      </span>
                                    </div>
                                    <div className="table-responsive">
                                      <table className="table table-sm mb-0">
                                        <thead>
                                          <tr>
                                            <th scope="col">{tr('Lines')}</th>
                                            <th scope="col">{tr('Heading')}</th>
                                            <th scope="col" className="text-end">
                                              {tr('Score')}
                                            </th>
                                            <th scope="col" className="text-end">
                                              {tr('Tokens')}
                                            </th>
                                            <th scope="col">{tr('Chunk')}</th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {hits.map((hit) => (
                                            <tr key={hit.chunk_id}>
                                              <td className="text-nowrap">
                                                {hit.start_line}–{hit.end_line}
                                              </td>
                                              <td>{hit.heading || '—'}</td>
                                              <td className="text-end">{hit.score.toFixed(2)}</td>
                                              <td className="text-end">{hit.tokens}</td>
                                              <td>
                                                <details>
                                                  <summary className="text-truncate" style={{ maxWidth: '520px' }}>
                                                    {hit.text.slice(0, 90).replace(/\n/g, ' ')}
                                                    {hit.text.length > 90 ? '…' : ''}
                                                  </summary>
                                                  <pre className="small mt-2 mb-0 text-wrap">{hit.text}</pre>
                                                </details>
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>

              <div className="row g-3">
                <div className="col-lg-8">
                  <div className="card">
                    <div className="card-header d-flex flex-wrap align-items-center gap-2">
                      <h3 className="card-title mb-0">{tr('Roots')}</h3>
                      <div className="ms-auto d-flex gap-2">
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-primary"
                          onClick={addRoot}
                          disabled={disabled}
                        >
                          <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                          {tr('Add root')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={() => void save()}
                          disabled={disabled}
                        >
                          {busy === 'save' ? tr('Saving…') : tr('Save configuration')}
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
                                <th scope="col" style={{ width: '150px' }}>
                                  {tr('Name')}
                                </th>
                                <th scope="col">{tr('Path')}</th>
                                <th scope="col" className="text-center">
                                  {tr('Watch')}
                                </th>
                                <th scope="col" className="text-end">
                                  {tr('Actions')}
                                </th>
                              </tr>
                            </thead>
                            <tbody>
                              {draft.roots.map((root, index) => (
                                <Fragment key={`${root.name}-${index}`}>
                                  <tr>
                                    <td>
                                      <input
                                        className="form-control form-control-sm"
                                        value={root.name}
                                        aria-label={tr('Root {{number}} name', {
                                          number: index + 1,
                                        })}
                                        onChange={(event) =>
                                          updateRoot(index, { name: event.target.value })
                                        }
                                      />
                                    </td>
                                    <td>
                                      <input
                                        className="form-control form-control-sm"
                                        value={root.path}
                                        aria-label={tr('Root {{number}} path', {
                                          number: index + 1,
                                        })}
                                        placeholder="/home/you/notes"
                                        onChange={(event) =>
                                          updateRoot(index, { path: event.target.value })
                                        }
                                      />
                                    </td>
                                    <td className="text-center">
                                      <div className="form-check form-switch d-inline-block">
                                        <input
                                          className="form-check-input"
                                          type="checkbox"
                                          checked={Boolean(root.watch)}
                                          aria-label={tr('Root {{number}} watch', {
                                            number: index + 1,
                                          })}
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
                                        aria-label={tr('Remove root {{root}}', {
                                          root: root.name || index + 1,
                                        })}
                                      >
                                        <i className="bi bi-trash" aria-hidden="true" />
                                      </button>
                                    </td>
                                  </tr>
                                  <tr className="table-light">
                                    <td colSpan={4} className="py-1">
                                      <details>
                                        <summary className="small text-secondary">
                                          {tr('Include / exclude globs')}
                                        </summary>
                                        <div className="row g-2 mt-1">
                                          <div className="col-md-6">
                                            <input
                                              className="form-control form-control-sm"
                                              value={(root.include ?? []).join(', ')}
                                              aria-label={tr('Root {{number}} include globs', {
                                                number: index + 1,
                                              })}
                                              placeholder={tr('include, comma separated')}
                                              onChange={(event) =>
                                                updateRoot(index, {
                                                  include: splitList(event.target.value),
                                                })
                                              }
                                            />
                                          </div>
                                          <div className="col-md-6">
                                            <input
                                              className="form-control form-control-sm"
                                              value={(root.exclude ?? []).join(', ')}
                                              aria-label={tr('Root {{number}} exclude globs', {
                                                number: index + 1,
                                              })}
                                              placeholder={tr('exclude, comma separated')}
                                              onChange={(event) =>
                                                updateRoot(index, {
                                                  exclude: splitList(event.target.value),
                                                })
                                              }
                                            />
                                          </div>
                                        </div>
                                      </details>
                                    </td>
                                  </tr>
                                </Fragment>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                    <div className="card-footer text-secondary small">
                      {tr(
                        'Saving rewrites the YAML file and reloads the daemon. Removing a root also removes its documents from the index; the files on disk are untouched. Format support in this version is',
                      )}{' '}
                      <code>txt</code> {tr('and')} <code>md</code>.
                    </div>
                  </div>
                </div>

                <div className="col-lg-4">
                  <div className="card mb-3">
                    <div className="card-header">
                      <h3 className="card-title">{tr('Sweep and limits')}</h3>
                    </div>
                    <div className="card-body">
                      <div className="mb-3">
                        <label className="form-label" htmlFor="scan-interval">
                          {tr('Sweep interval (seconds)')}
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
                          {tr('Max file size (MB)')}
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
                            {tr('Results')}
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
                            {tr('Chunks per document')}
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
                      <h3 className="card-title">{tr('Maintenance')}</h3>
                    </div>
                    <div className="card-body d-grid gap-2">
                      <button
                        type="button"
                        className="btn btn-outline-secondary"
                        onClick={() => void runBuild(true)}
                        disabled={disabled}
                      >
                        {busy === 'dry' ? tr('Checking…') : tr('Dry run')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={() => void runBuild(false)}
                        disabled={disabled}
                      >
                        {busy === 'build' ? tr('Building…') : tr('Build now')}
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-primary"
                        onClick={() => void installSkill()}
                        disabled={disabled}
                      >
                        {busy === 'skill' ? tr('Writing…') : tr('Install Agent Skill')}
                      </button>
                    </div>
                    <div className="card-footer small text-secondary">
                      {tr('Last sweep: {{date}}', {
                        date: formatDateTime(status.last_sweep_at),
                      })}
                      {status.skill.installed ? (
                        <span className="badge text-bg-success ms-2">
                          {tr('skill installed')}
                        </span>
                      ) : (
                        <span className="badge text-bg-warning ms-2">
                          {tr('skill missing')}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="card">
                    <div className="card-header">
                      <h3 className="card-title">{tr('Endpoints')}</h3>
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
                        <span className="text-secondary d-block">{tr('Catalog')}</span>
                        <code className="text-break">{status.db_path}</code>
                      </div>
                      <div>
                        <span className="text-secondary d-block">{tr('Config')}</span>
                        <code className="text-break">{configPath}</code>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              {status.roots.length > 0 ? (
                <div className="card mt-3">
                  <div className="card-header">
                    <h3 className="card-title">{tr('Indexed roots')}</h3>
                  </div>
                  <div className="card-body p-0">
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">{tr('Root')}</th>
                            <th scope="col">{tr('Path')}</th>
                            <th scope="col" className="text-end">
                              {tr('Documents')}
                            </th>
                            <th scope="col" className="text-end">
                              {tr('Chunks')}
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
                    <h3 className="card-title">{tr('Last build')}</h3>
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
                            {build.dry_run ? tr('Dry run') : tr('Build')} —{' '}
                            {tr(
                              '{{added}} added, {{changed}} changed, {{unchanged}} unchanged, {{deleted}} removed, {{failed}} failed, {{sealed}} sealed, {{chunks}} chunks.',
                              {
                                added: build.added,
                                changed: build.changed,
                                unchanged: build.unchanged,
                                deleted: build.deleted,
                                failed: build.failed,
                                sealed: build.sealed,
                                chunks: build.chunks,
                              },
                            )}
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
                    <h3 className="card-title">{tr('Failures')}</h3>
                  </div>
                  <div className="card-body p-0">
                    <div className="table-responsive">
                      <table className="table align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">{tr('Path')}</th>
                            <th scope="col">{tr('Error')}</th>
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
