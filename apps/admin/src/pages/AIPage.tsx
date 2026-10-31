import { useEffect, useState, type FormEvent } from 'react';

import { api, ApiError } from '../api/client';
import type {
  AIFeature,
  AIIntegration,
  AIIntegrationInput,
  AIProviderConfig,
  AIProviderKind,
  ModelRuntime,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { useApi } from '../lib/hooks';

type Message = { kind: 'success' | 'danger'; text: string } | null;

type OptionValue = string | number;

interface FormState {
  name: string;
  label: string;
  kind: AIProviderKind;
  base_url: string;
  api_key_env: string;
  default_model: string;
  enabled: boolean;
  gateway: boolean;
  options: Record<string, unknown>;
}

interface Editor {
  mode: 'create' | 'edit';
  id: string;
  draft: FormState;
}

interface BindingState {
  integration_id: string;
  model: string;
}

const VOLCENGINE_DEFAULTS: Record<string, OptionValue> = {
  resource_id: 'volc.seedasr.auc',
  stream_endpoint: 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel',
  stream_resource_id: 'volc.seedasr.sauc.duration',
  model_name: 'bigmodel',
  max_inline_mb: 100,
  poll_interval_seconds: 2,
  poll_timeout_seconds: 300,
};

const BLANK_FORM: FormState = {
  name: '',
  label: '',
  kind: 'openai',
  base_url: '',
  api_key_env: '',
  default_model: '',
  enabled: true,
  gateway: true,
  options: {},
};

function describe(error: unknown): string {
  if (error instanceof ApiError) {
    return error.detail;
  }
  return error instanceof Error ? error.message : String(error);
}

function formFromIntegration(integration: AIIntegration): FormState {
  return {
    name: integration.name,
    label: integration.label,
    kind: integration.kind,
    base_url: integration.base_url,
    api_key_env: integration.api_key_env,
    default_model: integration.default_model,
    enabled: integration.enabled,
    gateway: integration.gateway,
    options: { ...integration.options },
  };
}

function formForKind(kind: AIProviderKind, current: FormState): FormState {
  if (kind === 'volcengine_asr') {
    return {
      ...current,
      kind,
      base_url: current.base_url || 'https://openspeech.bytedance.com',
      api_key_env: current.api_key_env || 'VOLCENGINE_ASR_API_KEY',
      default_model: current.default_model || 'bigmodel',
      gateway: false,
      options: { ...VOLCENGINE_DEFAULTS, ...current.options },
    };
  }
  if (kind === 'fake') {
    return {
      ...current,
      kind,
      base_url: '',
      api_key_env: '',
      default_model: '',
      gateway: false,
      options: {},
    };
  }
  return {
    ...current,
    kind,
    gateway:
      current.kind === 'openai' || current.kind === 'anthropic'
        ? current.gateway
        : true,
    options: {},
  };
}

function toInput(form: FormState): AIIntegrationInput {
  const options = form.kind === 'volcengine_asr' ? { ...form.options } : {};
  // Volcengine ASR keeps the vendor's model name in options; mirror it into
  // default_model so the provider table and the seed show the same value.
  const defaultModel =
    form.kind === 'volcengine_asr'
      ? String(options.model_name || form.default_model).trim()
      : form.default_model.trim();
  return {
    name: form.name.trim(),
    label: form.label.trim(),
    kind: form.kind,
    base_url: form.base_url.trim(),
    api_key_env: form.api_key_env.trim(),
    default_model: defaultModel,
    enabled: form.enabled,
    gateway: form.gateway,
    options,
  };
}

/** A conventional key variable for a vendor name; empty for an unknown one. */
function suggestedKeyEnv(name: string): string {
  const value = name.toLowerCase();
  if (value.includes('deepseek')) {
    return 'DEEPSEEK_API_KEY';
  }
  if (value.includes('qwen') || value.includes('dashscope')) {
    return 'DASHSCOPE_API_KEY';
  }
  if (value.includes('openai')) {
    return 'OPENAI_API_KEY';
  }
  if (value.includes('anthropic')) {
    return 'ANTHROPIC_API_KEY';
  }
  if (value.includes('volcengine') || value.includes('doubao')) {
    return 'VOLCENGINE_ASR_API_KEY';
  }
  return '';
}

function FeatureLabel({
  feature,
  features,
}: {
  feature: string;
  features: AIFeature[];
}) {
  const match = features.find((item) => item.key === feature);
  return <>{match?.label ?? feature}</>;
}

export function AIPage() {
  const result = useApi(() => api.aiConfig(), []);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AIIntegration | null>(null);
  const [bindings, setBindings] = useState<Record<string, BindingState>>({});
  const [runtimeDraft, setRuntimeDraft] = useState<ModelRuntime | null>(null);

  const data: AIProviderConfig | null =
    result.status === 'success' ? result.data : null;

  useEffect(() => {
    if (result.status !== 'success') {
      return;
    }
    const next: Record<string, BindingState> = {};
    for (const feature of result.data.features) {
      next[feature.key] = {
        integration_id: feature.integration_id ?? '',
        model: feature.model,
      };
    }
    setBindings(next);
    setRuntimeDraft(result.data.runtime);
  }, [result.status, result.data]);

  const openCreate = () => {
    setMessage(null);
    setEditor({ mode: 'create', id: '', draft: { ...BLANK_FORM } });
  };

  const openEdit = (integration: AIIntegration) => {
    setMessage(null);
    setEditor({
      mode: 'edit',
      id: integration.id,
      draft: formFromIntegration(integration),
    });
  };

  const updateDraft = (patch: Partial<FormState>) => {
    setEditor((current) => {
      if (!current) {
        return current;
      }
      const draft = { ...current.draft, ...patch };
      // Suggest a conventional variable from the vendor name, but never
      // overwrite a value the user already entered.
      if (patch.name !== undefined && !draft.api_key_env.trim()) {
        draft.api_key_env = suggestedKeyEnv(patch.name);
      }
      return { ...current, draft };
    });
  };

  const updateOption = (key: string, value: string) => {
    setEditor((current) =>
      current
        ? {
            ...current,
            draft: {
              ...current.draft,
              options: { ...current.draft.options, [key]: value },
            },
          }
        : current,
    );
  };

  const saveIntegration = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editor) {
      return;
    }
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(editor.draft.name.trim())) {
      setMessage({
        kind: 'danger',
        text: 'Name must be lowercase letters, digits and dashes, up to 63 characters.',
      });
      return;
    }
    setBusy('save');
    setMessage(null);
    try {
      if (editor.mode === 'create') {
        await api.createAIIntegration(toInput(editor.draft));
        setMessage({
          kind: 'success',
          text: `Created ${editor.draft.name}. It is available to the bound features immediately.`,
        });
      } else {
        await api.updateAIIntegration(editor.id, toInput(editor.draft));
        setMessage({
          kind: 'success',
          text: `Updated ${editor.draft.name}.`,
        });
      }
      setEditor(null);
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const removeIntegration = async () => {
    if (!deleteTarget) {
      return;
    }
    setBusy('delete');
    setMessage(null);
    try {
      const response = await api.deleteAIIntegration(deleteTarget.id);
      const cleared = response.cleared_features.length
        ? ` These features now have no provider: ${response.cleared_features.join(', ')}.`
        : '';
      setMessage({
        kind: 'success',
        text: `Deleted ${response.name}.${cleared}`,
      });
      setDeleteTarget(null);
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const changeBinding = (feature: AIFeature, integrationId: string) => {
    const integration = data?.integrations.find((item) => item.id === integrationId);
    setBindings((current) => ({
      ...current,
      [feature.key]: {
        integration_id: integrationId,
        model:
          integrationId && integration
            ? integration.default_model
            : feature.model,
      },
    }));
  };

  const saveBinding = async (feature: AIFeature) => {
    const binding = bindings[feature.key] ?? { integration_id: '', model: '' };
    setBusy(`feature:${feature.key}`);
    setMessage(null);
    try {
      await api.setAIFeature(feature.key, {
        integration_id: binding.integration_id || null,
        model: binding.model.trim(),
      });
      setMessage({
        kind: 'success',
        text: `${feature.label} now uses ${
          binding.integration_id
            ? data?.integrations.find(
                (item) => item.id === binding.integration_id,
              )?.name ?? 'the selected provider'
            : 'no provider (choose one before using it)'
        }.`,
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const saveRuntime = async () => {
    if (!runtimeDraft) {
      return;
    }
    setBusy('runtime');
    setMessage(null);
    try {
      const response = await api.setAIRuntime(runtimeDraft);
      setRuntimeDraft(response.runtime);
      setMessage({
        kind: 'success',
        text: 'Model runtime saved. The next model call uses the new limits.',
      });
      result.reload();
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
    } finally {
      setBusy(null);
    }
  };

  const integrations: AIIntegration[] = (data?.integrations ?? []).map(
    (integration) => ({
      ...integration,
      gateway: integration.gateway !== false,
    }),
  );
  const features = data?.features ?? [];
  const configured = features.filter((feature) => feature.source === 'admin').length;
  const gatewayRoutes = integrations.filter(
    (integration) => integration.gateway_paths.length > 0,
  ).length;
  const keyed = integrations.filter((integration) => integration.api_key_env);
  const keysPresent = keyed.filter((integration) => integration.api_key_present);
  const disabled = busy !== null;

  return (
    <>
      <PageHeader title="AI providers" />
      <div className="app-content">
        <div className="container-fluid">
          {message ? (
            <div className={`alert alert-${message.kind}`} role="alert">
              {message.text}
            </div>
          ) : null}

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading AI providers…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                The AI provider registry is available when <code>ai.enabled</code> is
                true in <code>apps/api/config.yaml</code>. With it disabled, model
                features use the deterministic fake provider and ASR keeps its YAML
                default; enable it to choose a real model here.
              </div>
            </>
          ) : data ? (
            <>
              <div className="alert alert-info" role="note">
                <i className="bi bi-cpu-fill me-2" aria-hidden="true" />
                Providers, feature bindings and model runtime are stored in{' '}
                <code>{data.path}</code>. Changes take effect on the next model call
                without restarting the API. This registry is the only model choice:{' '}
                <code>config.yaml</code> has no <code>llm</code> block. API keys are{' '}
                <strong>not</strong> stored here: each provider names an environment
                variable that the API process reads.
              </div>

              <div className="row g-3 mb-3">
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{integrations.length}</h3>
                      <p>Providers</p>
                    </div>
                    <i className="small-box-icon bi bi-hdd-network" aria-hidden="true" />
                    <span className="small-box-footer">
                      {integrations.filter((item) => item.enabled).length} enabled
                    </span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-success">
                    <div className="inner">
                      <h3>{gatewayRoutes}</h3>
                      <p>Gateway routes</p>
                    </div>
                    <i className="small-box-icon bi bi-signpost-split" aria-hidden="true" />
                    <span className="small-box-footer">
                      OpenAI and Anthropic providers
                    </span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-info">
                    <div className="inner">
                      <h3>
                        {configured}/{features.length}
                      </h3>
                      <p>Bound features</p>
                    </div>
                    <i className="small-box-icon bi bi-diagram-3" aria-hidden="true" />
                    <span className="small-box-footer">
                      {features.length === 0
                        ? 'No AI features are registered'
                        : configured === features.length
                          ? 'Every feature has an explicit provider'
                          : `${features.length - configured} need a provider`}
                    </span>
                  </div>
                </div>
                <div className="col-6 col-lg-3">
                  <div
                    className={`small-box ${
                      keysPresent.length < keyed.length
                        ? 'text-bg-warning'
                        : 'text-bg-secondary'
                    }`}
                  >
                    <div className="inner">
                      <h3>
                        {keyed.length === 0
                          ? '0'
                          : `${keysPresent.length}/${keyed.length}`}
                      </h3>
                      <p>Keys in environment</p>
                    </div>
                    <i className="small-box-icon bi bi-key" aria-hidden="true" />
                    <span className="small-box-footer">
                      {keyed.length === 0
                        ? 'No provider reads a server-side key'
                        : `${keysPresent.length} of ${keyed.length} named environment variables are set`}
                    </span>
                  </div>
                </div>
              </div>

              <div className="card mb-3">
                <div className="card-header d-flex flex-wrap align-items-center gap-2">
                  <h3 className="card-title mb-0">Providers</h3>
                  <div className="ms-auto d-flex flex-wrap gap-2">
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary"
                      onClick={result.reload}
                      disabled={disabled}
                    >
                      <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                      Refresh
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      onClick={openCreate}
                      disabled={disabled}
                    >
                      <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                      Add provider
                    </button>
                  </div>
                </div>
                <div className="card-body p-0">
                  {integrations.length === 0 ? (
                    <div className="p-3">
                      <EmptyState message="No providers yet. Add one, then bind it to a feature below." />
                    </div>
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Provider</th>
                            <th scope="col">Endpoint</th>
                            <th scope="col">API key</th>
                            <th scope="col">Used by</th>
                            <th scope="col">Gateway</th>
                            <th scope="col">State</th>
                            <th scope="col" className="text-end">
                              Actions
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {integrations.map((integration) => (
                            <tr key={integration.id}>
                              <td style={{ minWidth: '12rem' }}>
                                <div className="fw-semibold">
                                  {integration.label || integration.name}
                                </div>
                                <div className="small text-secondary">
                                  <code>{integration.name}</code>
                                </div>
                                <span className="badge text-bg-light border mt-1">
                                  {integration.kind_label}
                                </span>
                              </td>
                              <td style={{ minWidth: '14rem' }}>
                                {integration.base_url ? (
                                  <div className="text-break">
                                    <code>{integration.base_url}</code>
                                  </div>
                                ) : (
                                  <span className="text-secondary">—</span>
                                )}
                                <div className="small text-secondary">
                                  Model:{' '}
                                  {integration.kind === 'volcengine_asr'
                                    ? String(
                                        integration.options.model_name ||
                                          integration.default_model ||
                                          '—',
                                      )
                                    : integration.default_model || '—'}
                                </div>
                                {integration.kind === 'volcengine_asr' ? (
                                  <div className="small text-secondary">
                                    Resource:{' '}
                                    <code>
                                      {String(
                                        integration.options.resource_id ?? '—',
                                      )}
                                    </code>
                                  </div>
                                ) : null}
                              </td>
                              <td style={{ minWidth: '10rem' }}>
                                {integration.api_key_env ? (
                                  <>
                                    <code>{integration.api_key_env}</code>
                                    <div className="mt-1">
                                      {integration.api_key_present ? (
                                        <span className="badge text-bg-success">
                                          set in environment
                                        </span>
                                      ) : (
                                        <span className="badge text-bg-warning">
                                          missing from environment
                                        </span>
                                      )}
                                    </div>
                                  </>
                                ) : integration.kind === 'volcengine_asr' ? (
                                  <span className="text-secondary">
                                    uses VOLCENGINE_ASR_API_KEY
                                  </span>
                                ) : integration.kind === 'fake' ? (
                                  <span className="text-secondary">not needed</span>
                                ) : integration.gateway === false ? (
                                  <span className="badge text-bg-warning">
                                    no key configured
                                  </span>
                                ) : (
                                  <span className="text-secondary">
                                    not needed for a gateway route
                                  </span>
                                )}
                              </td>
                              <td style={{ minWidth: '10rem' }}>
                                {integration.used_by.length === 0 ? (
                                  <span className="text-secondary">—</span>
                                ) : (
                                  integration.used_by.map((feature) => (
                                    <span
                                      className="badge text-bg-info me-1 mb-1"
                                      key={feature}
                                    >
                                      <FeatureLabel
                                        feature={feature}
                                        features={features}
                                      />
                                    </span>
                                  ))
                                )}
                              </td>
                              <td style={{ minWidth: '12rem' }}>
                                {integration.gateway_paths.length > 0 ? (
                                  integration.gateway_paths.map((path) => (
                                    <code className="d-block small" key={path}>
                                      {path}
                                    </code>
                                  ))
                                ) : integration.gateway === false &&
                                  (integration.kind === 'openai' ||
                                    integration.kind === 'anthropic') ? (
                                  <span className="badge text-bg-secondary">
                                    internal only
                                  </span>
                                ) : (
                                  <span className="text-secondary">
                                    not a gateway route
                                  </span>
                                )}
                              </td>
                              <td>
                                {integration.enabled ? (
                                  <span className="badge text-bg-success">
                                    Enabled
                                  </span>
                                ) : (
                                  <span className="badge text-bg-secondary">
                                    Disabled
                                  </span>
                                )}
                              </td>
                              <td className="text-end text-nowrap">
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-secondary me-1"
                                  onClick={() => openEdit(integration)}
                                  disabled={disabled}
                                >
                                  Edit
                                </button>
                                <button
                                  type="button"
                                  className="btn btn-sm btn-outline-danger"
                                  onClick={() => {
                                    setMessage(null);
                                    setDeleteTarget(integration);
                                  }}
                                  disabled={disabled}
                                >
                                  Delete
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
                <div className="card-footer small text-secondary">
                  Providers with <strong>Expose as gateway route</strong> enabled
                  become routes at <code>/{'{name}'}/v1/…</code>. Winnie forwards
                  the client's own vendor key on those paths; the environment
                  variable here is only for internal jobs and the assistant. An
                  internal-only provider has no public proxy path.
                </div>
              </div>

              <div className="card">
                <div className="card-header">
                  <h3 className="card-title mb-0">Feature bindings</h3>
                </div>
                <div className="card-body p-0">
                  <div className="table-responsive">
                    <table className="table align-middle mb-0">
                      <thead>
                        <tr>
                          <th scope="col">Feature</th>
                          <th scope="col">Provider</th>
                          <th scope="col">Model</th>
                          <th scope="col">Source</th>
                          <th scope="col" className="text-end">
                            Action
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {features.map((feature) => {
                          const binding =
                            bindings[feature.key] ?? {
                              integration_id: '',
                              model: '',
                            };
                          const selected = integrations.find(
                            (item) => item.id === binding.integration_id,
                          );
                          const compatible = integrations.filter(
                            (item) =>
                              feature.kinds.includes(item.kind) &&
                              (item.kind !== 'openai' || item.api_key_env),
                          );
                          const dirty =
                            binding.integration_id !==
                              (feature.integration_id ?? '') ||
                            binding.model !== feature.model;
                          const rowBusy = busy === `feature:${feature.key}`;
                          return (
                            <tr key={feature.key}>
                              <td style={{ minWidth: '16rem' }}>
                                <div className="fw-semibold">{feature.label}</div>
                                <div className="small text-secondary">
                                  {feature.description}
                                </div>
                                <code className="small text-secondary">
                                  {feature.key}
                                </code>
                              </td>
                              <td style={{ minWidth: '16rem' }}>
                                <label
                                  className="visually-hidden"
                                  htmlFor={`binding-${feature.key}`}
                                >
                                  Provider for {feature.label}
                                </label>
                                <select
                                  id={`binding-${feature.key}`}
                                  className="form-select form-select-sm"
                                  value={binding.integration_id}
                                  onChange={(event) =>
                                    changeBinding(feature, event.target.value)
                                  }
                                  disabled={disabled || rowBusy}
                                >
                                  <option value="">Not configured</option>
                                  {compatible.map((integration) => (
                                    <option
                                      key={integration.id}
                                      value={integration.id}
                                      disabled={!integration.enabled}
                                    >
                                      {integration.label || integration.name}
                                      {integration.enabled ? '' : ' (disabled)'}
                                    </option>
                                  ))}
                                </select>
                                {feature.source === 'none' ? (
                                  <div className="form-text text-warning">
                                    Choose a provider; this feature has no fallback.
                                  </div>
                                ) : null}
                              </td>
                              <td style={{ minWidth: '11rem' }}>
                                <label
                                  className="visually-hidden"
                                  htmlFor={`model-${feature.key}`}
                                >
                                  Model for {feature.label}
                                </label>
                                <input
                                  id={`model-${feature.key}`}
                                  className="form-control form-control-sm"
                                  value={binding.model}
                                  placeholder={
                                    selected?.kind === 'fake'
                                      ? 'Not used by the fake provider'
                                      : selected?.default_model ||
                                        (feature.requires_model
                                          ? 'Required'
                                          : 'Not used')
                                  }
                                  onChange={(event) =>
                                    setBindings((current) => ({
                                      ...current,
                                      [feature.key]: {
                                        ...current[feature.key],
                                        integration_id:
                                          current[feature.key]?.integration_id ??
                                          '',
                                        model: event.target.value,
                                      },
                                    }))
                                  }
                                  disabled={
                                    disabled ||
                                    rowBusy ||
                                    !feature.requires_model ||
                                    selected?.kind === 'fake'
                                  }
                                />
                                <div className="form-text">
                                  {selected?.kind === 'fake'
                                    ? 'The fake provider ignores the model.'
                                    : feature.requires_model
                                      ? 'Empty uses the provider default.'
                                      : 'This feature does not use a model.'}
                                </div>
                              </td>
                              <td>
                                {feature.source === 'admin' ? (
                                  <span className="badge text-bg-success">
                                    Admin
                                  </span>
                                ) : (
                                  <span className="badge text-bg-warning">
                                    Not configured
                                  </span>
                                )}
                              </td>
                              <td className="text-end">
                                <button
                                  type="button"
                                  className="btn btn-sm btn-primary"
                                  onClick={() => saveBinding(feature)}
                                  disabled={disabled || rowBusy || !dirty}
                                >
                                  {rowBusy ? (
                                    <>
                                      <span
                                        className="spinner-border spinner-border-sm me-1"
                                        aria-hidden="true"
                                      />
                                      Saving…
                                    </>
                                  ) : (
                                    'Save'
                                  )}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div className="card-footer small text-secondary">
                  A provider can serve only the features listed for its kind. The
                  assistant, translation and minutes use OpenAI-compatible
                  providers; meeting transcription uses Volcengine ASR;{' '}
                  <code>fake</code> keeps an offline demo working. These bindings
                  are the only model choice; <code>config.yaml</code> no longer
                  selects one.
                </div>
              </div>

              {runtimeDraft ? (
                <RuntimeCard
                  runtime={runtimeDraft}
                  busy={busy === 'runtime'}
                  disabled={disabled}
                  onChange={(patch) =>
                    setRuntimeDraft((current) =>
                      current ? { ...current, ...patch } : current,
                    )
                  }
                  onSave={saveRuntime}
                />
              ) : null}
            </>
          ) : null}
        </div>
      </div>

      {editor ? (
        <IntegrationEditor
          editor={editor}
          busy={busy === 'save'}
          onChange={updateDraft}
          onOption={updateOption}
          onKind={(kind) =>
            setEditor((current) =>
              current
                ? { ...current, draft: formForKind(kind, current.draft) }
                : current,
            )
          }
          onCancel={() => setEditor(null)}
          onSubmit={saveIntegration}
        />
      ) : null}

      {deleteTarget ? (
        <DeleteDialog
          integration={deleteTarget}
          busy={busy === 'delete'}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={removeIntegration}
        />
      ) : null}
    </>
  );
}

function RuntimeCard({
  runtime,
  busy,
  disabled,
  onChange,
  onSave,
}: {
  runtime: ModelRuntime;
  busy: boolean;
  disabled: boolean;
  onChange: (patch: Partial<ModelRuntime>) => void;
  onSave: () => void;
}) {
  const number = (key: keyof ModelRuntime, step = '1', min = '0') => (
    <input
      id={`runtime-${key}`}
      className="form-control"
      type="number"
      step={step}
      min={min}
      value={runtime[key]}
      onChange={(event) => onChange({ [key]: Number(event.target.value) })}
      disabled={disabled}
    />
  );

  return (
    <div className="card mt-3">
      <div className="card-header">
        <h3 className="card-title mb-0">Model runtime</h3>
      </div>
      <div className="card-body">
        <p className="small text-secondary">
          Shared limits for every OpenAI-compatible call. They apply to the
          assistant, translation and minutes; the model and endpoint come from
          the provider binding above.
        </p>
        <div className="row g-3">
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-timeout_seconds">
              Timeout (seconds)
            </label>
            {number('timeout_seconds')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-max_output_tokens">
              Max output tokens
            </label>
            {number('max_output_tokens')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-temperature">
              Temperature
            </label>
            {number('temperature', '0.1')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-max_retries">
              Max retries
            </label>
            {number('max_retries')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-retry_backoff_seconds">
              Retry backoff (seconds)
            </label>
            {number('retry_backoff_seconds', '0.1')}
          </div>
          <div className="col-md-4">
            <label
              className="form-label"
              htmlFor="runtime-translation_batch_chars"
            >
              Translation batch (characters)
            </label>
            {number('translation_batch_chars', '100', '500')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-summary_chunk_chars">
              Minutes chunk (characters)
            </label>
            {number('summary_chunk_chars', '1000', '2000')}
          </div>
        </div>
      </div>
      <div className="card-footer d-flex justify-content-end">
        <button
          type="button"
          className="btn btn-primary"
          onClick={onSave}
          disabled={disabled}
        >
          {busy ? (
            <>
              <span
                className="spinner-border spinner-border-sm me-1"
                aria-hidden="true"
              />
              Saving…
            </>
          ) : (
            'Save runtime'
          )}
        </button>
      </div>
    </div>
  );
}


interface EditorProps {
  editor: Editor;
  busy: boolean;
  onChange: (patch: Partial<FormState>) => void;
  onOption: (key: string, value: string) => void;
  onKind: (kind: AIProviderKind) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

function IntegrationEditor({
  editor,
  busy,
  onChange,
  onOption,
  onKind,
  onCancel,
  onSubmit,
}: EditorProps) {
  const draft = editor.draft;
  const isFake = draft.kind === 'fake';
  const isVolcengine = draft.kind === 'volcengine_asr';
  const isGatewayKind = draft.kind === 'openai' || draft.kind === 'anthropic';

  return (
    <>
      <div
        className="modal fade show d-block"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-editor-title"
      >
        <div className="modal-dialog modal-lg modal-dialog-scrollable">
          <form className="modal-content" onSubmit={onSubmit}>
            <div className="modal-header">
              <h5 className="modal-title" id="ai-editor-title">
                {editor.mode === 'create' ? 'Add provider' : `Edit ${draft.name}`}
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label="Close"
                onClick={onCancel}
                disabled={busy}
              />
            </div>
            <div className="modal-body">
              <div className="row g-3">
                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-name">
                    Name
                  </label>
                  <input
                    id="ai-name"
                    className="form-control"
                    value={draft.name}
                    onChange={(event) => onChange({ name: event.target.value })}
                    pattern="[a-z0-9][a-z0-9-]{0,62}"
                    required
                    disabled={busy || editor.mode === 'edit'}
                  />
                  <div className="form-text">
                    Lowercase slug. It becomes the gateway path prefix when the
                    provider is exposed as a route; it cannot change after
                    creation.
                  </div>
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-label">
                    Label
                  </label>
                  <input
                    id="ai-label"
                    className="form-control"
                    value={draft.label}
                    onChange={(event) => onChange({ label: event.target.value })}
                    placeholder="Shown in the console"
                    disabled={busy}
                  />
                </div>

                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-kind">
                    Kind
                  </label>
                  <select
                    id="ai-kind"
                    className="form-select"
                    value={draft.kind}
                    onChange={(event) =>
                      onKind(event.target.value as AIProviderKind)
                    }
                    disabled={busy}
                  >
                    <option value="openai">OpenAI-compatible</option>
                    <option value="anthropic">Anthropic</option>
                    <option value="volcengine_asr">Volcengine Doubao ASR</option>
                    <option value="fake">Deterministic fake</option>
                  </select>
                </div>
                <div className="col-md-6 d-flex align-items-end gap-4">
                  <div className="form-check form-switch mb-2">
                    <input
                      id="ai-enabled"
                      className="form-check-input"
                      type="checkbox"
                      checked={draft.enabled}
                      onChange={(event) =>
                        onChange({ enabled: event.target.checked })
                      }
                      disabled={busy}
                    />
                    <label className="form-check-label" htmlFor="ai-enabled">
                      Enabled
                    </label>
                  </div>
                  {isGatewayKind ? (
                    <div className="form-check form-switch mb-2">
                      <input
                        id="ai-gateway"
                        className="form-check-input"
                        type="checkbox"
                        checked={draft.gateway}
                        onChange={(event) =>
                          onChange({ gateway: event.target.checked })
                        }
                        disabled={busy}
                      />
                      <label className="form-check-label" htmlFor="ai-gateway">
                        Gateway route
                      </label>
                    </div>
                  ) : null}
                </div>

                {!isFake ? (
                  <>
                    <div className="col-12">
                      <label className="form-label" htmlFor="ai-base-url">
                        Base URL
                      </label>
                      <input
                        id="ai-base-url"
                        className="form-control font-monospace"
                        value={draft.base_url}
                        onChange={(event) =>
                          onChange({ base_url: event.target.value })
                        }
                        placeholder="https://api.example.com"
                        required
                        disabled={busy}
                      />
                      <div className="form-text">
                        {isVolcengine
                          ? 'Volcengine speech endpoint root; no /v1 suffix is added.'
                          : draft.kind === 'anthropic'
                            ? 'Anthropic API root; /v1 is added when missing.'
                            : 'OpenAI-compatible: the part before /v1; it is added automatically when missing.'}
                      </div>
                    </div>
                    <div className="col-md-6">
                      <label className="form-label" htmlFor="ai-key-env">
                        API key environment variable
                      </label>
                      <input
                        id="ai-key-env"
                        className="form-control font-monospace"
                        value={draft.api_key_env}
                        onChange={(event) =>
                          onChange({ api_key_env: event.target.value })
                        }
                        placeholder="DEEPSEEK_API_KEY"
                        disabled={busy}
                      />
                      <div className="form-text">
                        {isVolcengine
                          ? 'The API process reads this variable for recording-file and streaming ASR.'
                          : 'The name, not the key. Required for internal features; a gateway-only provider can leave it empty.'}
                      </div>
                    </div>
                    {!isVolcengine ? (
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="ai-default-model">
                          Default model
                        </label>
                        <input
                          id="ai-default-model"
                          className="form-control font-monospace"
                          value={draft.default_model}
                          onChange={(event) =>
                            onChange({ default_model: event.target.value })
                          }
                          placeholder="deepseek-chat"
                          disabled={busy}
                        />
                        <div className="form-text">
                          Used when a feature binding does not override it.
                        </div>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="col-12">
                    <div className="alert alert-secondary mb-0" role="note">
                      The fake provider answers deterministically and never calls a
                      vendor. It needs no URL, key or model.
                    </div>
                  </div>
                )}

                {isVolcengine ? (
                  <div className="col-12">
                    <hr />
                    <h6>Volcengine Doubao options</h6>
                    <div className="row g-3">
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="ai-resource-id">
                          Resource ID
                        </label>
                        <input
                          id="ai-resource-id"
                          className="form-control font-monospace"
                          value={String(draft.options.resource_id ?? '')}
                          onChange={(event) =>
                            onOption('resource_id', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="ai-model-name">
                          Model name
                        </label>
                        <input
                          id="ai-model-name"
                          className="form-control font-monospace"
                          value={String(draft.options.model_name ?? '')}
                          onChange={(event) =>
                            onOption('model_name', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-12">
                        <label
                          className="form-label"
                          htmlFor="ai-stream-endpoint"
                        >
                          Streaming endpoint
                        </label>
                        <input
                          id="ai-stream-endpoint"
                          className="form-control font-monospace"
                          value={String(draft.options.stream_endpoint ?? '')}
                          onChange={(event) =>
                            onOption('stream_endpoint', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-md-6">
                        <label
                          className="form-label"
                          htmlFor="ai-stream-resource-id"
                        >
                          Streaming resource ID
                        </label>
                        <input
                          id="ai-stream-resource-id"
                          className="form-control font-monospace"
                          value={String(
                            draft.options.stream_resource_id ?? '',
                          )}
                          onChange={(event) =>
                            onOption('stream_resource_id', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-md-6">
                        <label
                          className="form-label"
                          htmlFor="ai-max-inline"
                        >
                          Max inline upload (MB)
                        </label>
                        <input
                          id="ai-max-inline"
                          className="form-control"
                          type="number"
                          min={1}
                          max={500}
                          value={String(draft.options.max_inline_mb ?? '')}
                          onChange={(event) =>
                            onOption('max_inline_mb', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-md-6">
                        <label
                          className="form-label"
                          htmlFor="ai-poll-interval"
                        >
                          Poll interval (seconds)
                        </label>
                        <input
                          id="ai-poll-interval"
                          className="form-control"
                          type="number"
                          min={0}
                          step="0.5"
                          value={String(
                            draft.options.poll_interval_seconds ?? '',
                          )}
                          onChange={(event) =>
                            onOption(
                              'poll_interval_seconds',
                              event.target.value,
                            )
                          }
                          disabled={busy}
                        />
                      </div>
                      <div className="col-md-6">
                        <label
                          className="form-label"
                          htmlFor="ai-poll-timeout"
                        >
                          Poll timeout (seconds)
                        </label>
                        <input
                          id="ai-poll-timeout"
                          className="form-control"
                          type="number"
                          min={1}
                          value={String(
                            draft.options.poll_timeout_seconds ?? '',
                          )}
                          onChange={(event) =>
                            onOption('poll_timeout_seconds', event.target.value)
                          }
                          disabled={busy}
                        />
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={onCancel}
                disabled={busy}
              >
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm me-1"
                      aria-hidden="true"
                    />
                    Saving…
                  </>
                ) : editor.mode === 'create' ? (
                  'Add provider'
                ) : (
                  'Save changes'
                )}
              </button>
            </div>
          </form>
        </div>
      </div>
      <div className="modal-backdrop fade show" />
    </>
  );
}

function DeleteDialog({
  integration,
  busy,
  onCancel,
  onConfirm,
}: {
  integration: AIIntegration;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <>
      <div
        className="modal fade show d-block"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-delete-title"
      >
        <div className="modal-dialog">
          <div className="modal-content">
            <div className="modal-header">
              <h5 className="modal-title" id="ai-delete-title">
                Delete {integration.label || integration.name}?
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label="Close"
                onClick={onCancel}
                disabled={busy}
              />
            </div>
            <div className="modal-body">
              <p className="mb-2">
                The provider is removed from the registry and its gateway route
                disappears immediately.
              </p>
              {integration.used_by.length > 0 ? (
                <div className="alert alert-warning mb-0" role="alert">
                  These features will fall back to{' '}
                  <code>apps/api/config.yaml</code>:{' '}
                  {integration.used_by.map((feature) => (
                    <code className="me-1" key={feature}>
                      {feature}
                    </code>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={onCancel}
                disabled={busy}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={onConfirm}
                disabled={busy}
              >
                {busy ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm me-1"
                      aria-hidden="true"
                    />
                    Deleting…
                  </>
                ) : (
                  'Delete provider'
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="modal-backdrop fade show" />
    </>
  );
}
