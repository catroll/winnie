import { useEffect, useState, type FormEvent } from 'react';

import { api, ApiError } from '../api/client';
import type {
  AIFeature,
  AIIntegration,
  AIIntegrationInput,
  AIModel,
  AIProviderConfig,
  AIProviderKind,
  AIVendor,
  AIVendorInput,
  ModelRuntime,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { VendorIcon } from '../lib/vendorIcons';
import { useApi } from '../lib/hooks';

type Message = { kind: 'success' | 'danger'; text: string } | null;

type OptionValue = string | number;

interface FormState {
  name: string;
  label: string;
  vendor: string;
  new_vendor: string;
  new_vendor_label: string;
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
  vendor: string;
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
  vendor: '',
  new_vendor: '',
  new_vendor_label: '',
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
    vendor: integration.vendor,
    new_vendor: '',
    new_vendor_label: '',
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

function toInput(form: FormState, vendor: string): AIIntegrationInput {
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
    vendor,
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

export function AIPage() {
  const result = useApi(() => api.aiConfig(), []);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [modelsEditor, setModelsEditor] = useState<AIVendor | null>(null);
  const [modelsError, setModelsError] = useState('');
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
        vendor: feature.vendor ?? '',
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
      let vendor = editor.draft.vendor;
      if (editor.mode === 'create' && vendor === '__new__') {
        const id = editor.draft.new_vendor.trim();
        if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(id)) {
          throw new Error(
            'Supplier id must be lowercase letters, digits and dashes, up to 63 characters.',
          );
        }
        await api.createAIVendor({
          id,
          label: editor.draft.new_vendor_label.trim() || id,
          models: [],
        });
        vendor = id;
      }
      if (!vendor) {
        throw new Error('Choose a supplier, or create a new one.');
      }
      if (editor.mode === 'create') {
        await api.createAIIntegration(toInput(editor.draft, vendor));
        setMessage({
          kind: 'success',
          text: `Created ${editor.draft.name}. It is available to the bound features immediately.`,
        });
      } else {
        await api.updateAIIntegration(
          editor.id,
          toInput(editor.draft, vendor),
        );
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

  const saveBinding = async (feature: AIFeature) => {
    const binding = bindings[feature.key] ?? { vendor: '', model: '' };
    setBusy(`feature:${feature.key}`);
    setMessage(null);
    try {
      await api.setAIFeature(feature.key, {
        vendor: binding.vendor,
        model: binding.model.trim(),
      });
      const selectedVendor = data?.vendors.find(
        (item) => item.id === binding.vendor,
      );
      setMessage({
        kind: 'success',
        text: `${feature.label} now uses ${
          selectedVendor
            ? `${selectedVendor.label} / ${binding.model || 'no model'}`
            : 'no model (choose one before using it)'
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

  const saveVendor = async (vendor: AIVendor, input: AIVendorInput) => {
    setBusy(`vendor:${vendor.id}`);
    setMessage(null);
    try {
      const response = await api.updateAIVendor(vendor.id, input);
      setMessage({
        kind: 'success',
        text: `Saved ${response.vendor.label} models.`,
      });
      setModelsError('');
      setModelsEditor(null);
      result.reload();
    } catch (error) {
      setModelsError(describe(error));
    } finally {
      setBusy(null);
    }
  };

  const syncVendor = async (vendor: AIVendor) => {
    setBusy(`sync:${vendor.id}`);
    setMessage(null);
    try {
      const response = await api.syncAIVendor(vendor.id);
      setMessage({
        kind: 'success',
        text: `${response.vendor.label}: ${response.vendor.models.length} models synced from the supplier.`,
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
  const vendors = data?.vendors ?? [];
  const vendorGroups = vendors.map((vendor) => ({
    vendor,
    gatewayEndpoints: integrations.filter(
      (item) => item.vendor === vendor.id && item.gateway,
    ),
  }));
  const configured = features.filter((feature) => feature.source === 'admin').length;
  const gatewayRoutes = integrations.filter(
    (integration) => integration.gateway_paths.length > 0,
  ).length;
  const keyed = integrations.filter((integration) => integration.api_key_env);
  const keysPresent = keyed.filter((integration) => integration.api_key_present);
  const disabled = busy !== null;

  return (
    <>
      <PageHeader title="AI suppliers" />
      <div className="app-content">
        <div className="container-fluid">
          {message ? (
            <div className={`alert alert-${message.kind}`} role="alert">
              {message.text}
            </div>
          ) : null}

          {result.status === 'loading' ? (
            <LoadingBlock label="Loading AI suppliers…" />
          ) : result.status === 'error' ? (
            <>
              <ErrorBlock error={result.error} onRetry={result.reload} />
              <div className="alert alert-secondary" role="note">
                The AI provider registry is available when <code>ai.enabled</code> is
                true in <code>apps/api/config.yaml</code>. With it disabled, model
                features have no model and ASR keeps its YAML default; enable it to
                choose a model here.
              </div>
            </>
          ) : data ? (
            <>
              <div className="alert alert-info" role="note">
                <i className="bi bi-cpu-fill me-2" aria-hidden="true" />
                Suppliers group endpoints and models; feature bindings and model
                runtime are stored in <code>{data.path}</code>. Changes take effect
                on the next model call without restarting the API. This registry is
                the only model choice: <code>config.yaml</code> has no{' '}
                <code>llm</code> block. API keys are <strong>not</strong> stored
                here: each endpoint names an environment variable that the API
                process reads.
              </div>

              <div className="row g-3 mb-3">
                <div className="col-6 col-lg-3">
                  <div className="small-box text-bg-primary">
                    <div className="inner">
                      <h3>{vendorGroups.length}</h3>
                      <p>Suppliers</p>
                    </div>
                    <i className="small-box-icon bi bi-hdd-network" aria-hidden="true" />
                    <span className="small-box-footer">
                      {integrations.length} endpoints ·{' '}
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
                  <h3 className="card-title mb-0">Suppliers</h3>
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
                      Add route/API
                    </button>
                  </div>
                </div>
                <div className="card-body p-0">
                  {integrations.length === 0 ? (
                    <div className="p-3">
                      <EmptyState message="No suppliers yet. Add one, its endpoints and models, then bind a feature below." />
                    </div>
                  ) : (
                    <div className="table-responsive">
                      <table className="table table-striped align-middle mb-0">
                        <thead>
                          <tr>
                            <th scope="col">Gateway route</th>
                            <th scope="col">Base URL</th>
                            <th scope="col">API key</th>
                            <th scope="col">Used by</th>
                            <th scope="col">Gateway</th>
                            <th scope="col">State</th>
                            <th scope="col" className="text-end">
                              Actions
                            </th>
                          </tr>
                        </thead>
                        {vendorGroups.map(({ vendor, gatewayEndpoints }) => (
                          <tbody key={vendor.id}>
                            <tr className="table-light">
                              <td colSpan={7} className="py-3">
                                <div className="d-flex flex-wrap align-items-center gap-2">
                                  <VendorIcon id={vendor.id} label={vendor.label} />
                                  <strong>{vendor.label}</strong>
                                  <code className="small text-secondary">
                                    {vendor.id}
                                  </code>
                                  <span className="badge text-bg-secondary">
                                    {vendor.gateway_count}{' '}
                                    {vendor.gateway_count === 1
                                      ? 'gateway route'
                                      : 'gateway routes'}
                                  </span>
                                  {vendor.base_url ? (
                                    <code className="small text-secondary">
                                      {vendor.base_url}
                                    </code>
                                  ) : (
                                    <span className="small text-warning">
                                      no API endpoint
                                    </span>
                                  )}
                                  {vendor.api_key_env ? (
                                    <span
                                      className={`badge ${
                                        vendor.api_key_present
                                          ? 'text-bg-success'
                                          : 'text-bg-warning'
                                      }`}
                                    >
                                      {vendor.api_key_env}
                                      {vendor.api_key_present ? '' : ' missing'}
                                    </span>
                                  ) : null}
                                </div>
                                <div className="d-flex flex-wrap align-items-center gap-2 mt-2">
                                  {vendor.models.length === 0 ? (
                                    <span className="small text-secondary">
                                      No models yet; sync them from the supplier.
                                    </span>
                                  ) : (
                                    vendor.models.slice(0, 5).map((model) => (
                                      <span
                                        className="badge text-bg-light border"
                                        key={model.id}
                                      >
                                        {model.id}
                                      </span>
                                    ))
                                  )}
                                  {vendor.models.length > 5 ? (
                                    <span className="small text-secondary">
                                      +{vendor.models.length - 5}
                                    </span>
                                  ) : null}
                                  <div className="ms-auto d-flex flex-wrap gap-2">
                                    {vendor.internal_integration_id ? (
                                      <>
                                        <button
                                          type="button"
                                          className="btn btn-sm btn-outline-secondary"
                                          onClick={() => {
                                            const internal = integrations.find(
                                              (item) =>
                                                item.id ===
                                                vendor.internal_integration_id,
                                            );
                                            if (internal) {
                                              openEdit(internal);
                                            }
                                          }}
                                          disabled={disabled}
                                          title="Edit the supplier API endpoint"
                                        >
                                          <i
                                            className="bi bi-plug me-1"
                                            aria-hidden="true"
                                          />
                                          API
                                        </button>
                                        <button
                                          type="button"
                                          className="btn btn-sm btn-outline-danger"
                                          onClick={() => {
                                            const internal = integrations.find(
                                              (item) =>
                                                item.id ===
                                                vendor.internal_integration_id,
                                            );
                                            if (internal) {
                                              setMessage(null);
                                              setDeleteTarget(internal);
                                            }
                                          }}
                                          disabled={disabled}
                                          title="Remove the supplier API endpoint"
                                        >
                                          <i
                                            className="bi bi-trash me-1"
                                            aria-hidden="true"
                                          />
                                          Remove API
                                        </button>
                                        {vendor.kind === 'openai' ? (
                                          <button
                                            type="button"
                                            className="btn btn-sm btn-outline-secondary"
                                            onClick={() => syncVendor(vendor)}
                                            disabled={disabled}
                                            title="Fetch the model list from the supplier /models API"
                                          >
                                            {busy === `sync:${vendor.id}` ? (
                                              <>
                                                <span
                                                  className="spinner-border spinner-border-sm me-1"
                                                  aria-hidden="true"
                                                />
                                                Syncing…
                                              </>
                                            ) : (
                                              <>
                                                <i
                                                  className="bi bi-arrow-repeat me-1"
                                                  aria-hidden="true"
                                                />
                                                Sync models
                                              </>
                                            )}
                                          </button>
                                        ) : null}
                                      </>
                                    ) : (
                                      <span className="small text-secondary">
                                        No supplier API; add one with Add route/API.
                                      </span>
                                    )}
                                    <button
                                      type="button"
                                      className="btn btn-sm btn-outline-secondary"
                                      onClick={() => {
                                        setMessage(null);
                                        setModelsError('');
                                        setModelsEditor(vendor);
                                      }}
                                      disabled={disabled}
                                    >
                                      <i
                                        className="bi bi-sliders me-1"
                                        aria-hidden="true"
                                      />
                                      Models
                                    </button>
                                  </div>
                                </div>
                              </td>
                            </tr>
                            {gatewayEndpoints.map((integration) => (
                              <tr key={integration.id}>
                                <td style={{ minWidth: '12rem' }}>
                                  <div className="fw-semibold">
                                    Gateway route · {integration.kind_label}
                                  </div>
                                  <div className="small text-secondary">
                                    <code>{integration.name}</code>
                                  </div>
                                  <span className="badge text-bg-light border mt-1">
                                    {integration.kind_label}
                                  </span>
                                </td>
                                <td style={{ minWidth: '11rem' }}>
                                  <div className="text-break">
                                    <code>{integration.base_url}</code>
                                  </div>
                                </td>
                                <td style={{ minWidth: '8rem' }}>
                                  <span className="text-secondary">
                                    client supplies the key
                                  </span>
                                </td>
                                <td style={{ minWidth: '8rem' }}>
                                  <span className="text-secondary">—</span>
                                </td>
                                <td style={{ minWidth: '9rem' }}>
                                  {integration.gateway_paths.map((path) => (
                                    <code className="d-block small" key={path}>
                                      {path}
                                    </code>
                                  ))}
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
                        ))}
                      </table>
                    </div>
                  )}
                </div>
                <div className="card-footer small text-secondary">
                  A supplier owns its API endpoint, the models it offers and any
                  public gateway routes. Feature bindings select a model, not an
                  endpoint. The API button edits the supplier's server-side
                  endpoint; <strong>Sync models</strong> reads its{' '}
                  <code>/models</code> API. Gateway routes forward the client's own
                  vendor key at <code>/{'{name}'}/v1/…</code>; the environment
                  variable is only for internal calls.
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
                              vendor: '',
                              model: '',
                            };
                          const modelGroups = vendors
                            .map((vendor) => {
                              const internal = integrations.find(
                                (item) =>
                                  item.vendor === vendor.id &&
                                  !item.gateway &&
                                  feature.kinds.includes(item.kind) &&
                                  (item.kind !== 'openai' || item.api_key_env),
                              );
                              if (!internal) {
                                return null;
                              }
                              return { vendor, models: vendor.models };
                            })
                            .filter(
                              (group): group is NonNullable<typeof group> =>
                                group !== null,
                            );
                          const value =
                            binding.vendor && binding.model
                              ? `${binding.vendor}|${binding.model}`
                              : '';
                          const known = modelGroups.some(
                            (group) =>
                              group.vendor.id === binding.vendor &&
                              group.models.some(
                                (model) => model.id === binding.model,
                              ),
                          );
                          const dirty =
                            binding.vendor !== (feature.vendor ?? '') ||
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
                              <td style={{ minWidth: '18rem' }}>
                                <label
                                  className="visually-hidden"
                                  htmlFor={`model-${feature.key}`}
                                >
                                  Model for {feature.label}
                                </label>
                                <select
                                  id={`model-${feature.key}`}
                                  className="form-select form-select-sm"
                                  value={value}
                                  onChange={(event) => {
                                    const raw = event.target.value;
                                    const index = raw.indexOf('|');
                                    const vendor =
                                      index === -1 ? '' : raw.slice(0, index);
                                    const model =
                                      index === -1 ? '' : raw.slice(index + 1);
                                    setBindings((current) => ({
                                      ...current,
                                      [feature.key]: { vendor, model },
                                    }));
                                  }}
                                  disabled={disabled || rowBusy}
                                >
                                  <option value="">Not configured</option>
                                  {modelGroups.map((group) => (
                                    <optgroup
                                      label={group.vendor.label}
                                      key={group.vendor.id}
                                    >
                                      {group.models.map((model) => (
                                        <option
                                          value={`${group.vendor.id}|${model.id}`}
                                          key={model.id}
                                        >
                                          {model.label || model.id}
                                          {model.context_window
                                            ? ` · ${model.context_window.toLocaleString()} ctx`
                                            : ''}
                                        </option>
                                      ))}
                                    </optgroup>
                                  ))}
                                  {binding.vendor && binding.model && !known ? (
                                    <option value={value}>
                                      {binding.model} (not in the catalog)
                                    </option>
                                  ) : null}
                                </select>
                                <div className="form-text">
                                  {feature.source === 'none'
                                    ? 'Choose the model this feature calls.'
                                    : !known
                                      ? 'This model is not in a supplier catalog; sync it first.'
                                      : 'Every feature calls the model selected here.'}
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
                  Every feature calls the model selected here. The supplier is
                  resolved from that model; there is no endpoint/provider choice in
                  a feature binding. Models come from the supplier catalog; sync
                  them with <strong>Sync models</strong> or add them under{' '}
                  <strong>Models</strong>. <code>config.yaml</code> no longer
                  selects a model.
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
          vendors={vendors}
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

      {modelsEditor ? (
        <ModelsEditor
          vendor={modelsEditor}
          error={modelsError}
          busy={busy === `vendor:${modelsEditor.id}`}
          onSave={saveVendor}
          onCancel={() => {
            setModelsError('');
            setModelsEditor(null);
          }}
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


function ModelsEditor({
  vendor,
  error,
  busy,
  onSave,
  onCancel,
}: {
  vendor: AIVendor;
  error: string;
  busy: boolean;
  onSave: (vendor: AIVendor, input: AIVendorInput) => void;
  onCancel: () => void;
}) {
  const [label, setLabel] = useState(vendor.label);
  const [models, setModels] = useState<AIModel[]>(
    vendor.models.map((model) => ({ ...model })),
  );

  const update = (index: number, patch: Partial<AIModel>) => {
    setModels((current) =>
      current.map((model, position) =>
        position === index ? { ...model, ...patch } : model,
      ),
    );
  };

  const add = () => {
    setModels((current) => [
      ...current,
      {
        id: '',
        label: '',
        owned_by: '',
        created: null,
        context_window: null,
        max_output_tokens: null,
        notes: '',
      },
    ]);
  };

  const remove = (index: number) => {
    setModels((current) => current.filter((_, position) => position !== index));
  };

  const number = (value: string): number | null => {
    const trimmed = value.trim();
    return trimmed ? Number(trimmed) : null;
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSave(vendor, {
      id: vendor.id,
      label: label.trim() || vendor.id,
      models: models.map((model) => ({
        ...model,
        id: model.id.trim(),
        label: model.label.trim(),
        notes: model.notes.trim(),
      })),
    });
  };

  return (
    <>
      <div
        className="modal fade show d-block"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-models-title"
      >
        <div className="modal-dialog modal-xl modal-dialog-scrollable">
          <form className="modal-content" onSubmit={submit}>
            <div className="modal-header">
              <h5 className="modal-title" id="ai-models-title">
                Models for {vendor.label}
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
              {error ? (
                <div className="alert alert-danger" role="alert">
                  {error}
                </div>
              ) : null}
              <div className="row g-3 mb-3">
                <div className="col-md-6">
                  <label className="form-label" htmlFor="vendor-label">
                    Supplier label
                  </label>
                  <input
                    id="vendor-label"
                    className="form-control"
                    value={label}
                    onChange={(event) => setLabel(event.target.value)}
                    disabled={busy}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">Supplier id</label>
                  <input
                    className="form-control font-monospace"
                    value={vendor.id}
                    disabled
                  />
                </div>
              </div>
              <p className="small text-secondary">
                Model ids are sent to the endpoint. A feature binding may use the
                endpoint default or one of these entries; removing a model that is
                still used is rejected.
              </p>
              <div className="table-responsive">
                <table className="table align-middle">
                  <thead>
                    <tr>
                      <th scope="col">Model id</th>
                      <th scope="col">Label</th>
                      <th scope="col">Context</th>
                      <th scope="col">Max output</th>
                      <th scope="col">Notes</th>
                      <th scope="col" className="text-end">
                        Action
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((model, index) => (
                      <tr key={index}>
                        <td style={{ minWidth: '12rem' }}>
                          <input
                            className="form-control form-control-sm font-monospace"
                            value={model.id}
                            onChange={(event) =>
                              update(index, { id: event.target.value })
                            }
                            placeholder="deepseek-chat"
                            required
                            disabled={busy}
                          />
                        </td>
                        <td style={{ minWidth: '10rem' }}>
                          <input
                            className="form-control form-control-sm"
                            value={model.label}
                            onChange={(event) =>
                              update(index, { label: event.target.value })
                            }
                            placeholder={model.id || 'DeepSeek Chat'}
                            disabled={busy}
                          />
                        </td>
                        <td style={{ minWidth: '7rem' }}>
                          <input
                            className="form-control form-control-sm"
                            type="number"
                            min={1}
                            value={model.context_window ?? ''}
                            onChange={(event) =>
                              update(index, {
                                context_window: number(event.target.value),
                              })
                            }
                            disabled={busy}
                          />
                        </td>
                        <td style={{ minWidth: '7rem' }}>
                          <input
                            className="form-control form-control-sm"
                            type="number"
                            min={1}
                            value={model.max_output_tokens ?? ''}
                            onChange={(event) =>
                              update(index, {
                                max_output_tokens: number(event.target.value),
                              })
                            }
                            disabled={busy}
                          />
                        </td>
                        <td style={{ minWidth: '12rem' }}>
                          <input
                            className="form-control form-control-sm"
                            value={model.notes}
                            onChange={(event) =>
                              update(index, { notes: event.target.value })
                            }
                            placeholder="Optional"
                            disabled={busy}
                          />
                        </td>
                        <td className="text-end">
                          <button
                            type="button"
                            className="btn btn-sm btn-outline-danger"
                            onClick={() => remove(index)}
                            disabled={busy}
                          >
                            Remove
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {models.length === 0 ? (
                <EmptyState message="No models for this supplier yet." />
              ) : null}
              <button
                type="button"
                className="btn btn-sm btn-outline-primary"
                onClick={add}
                disabled={busy}
              >
                <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                Add model
              </button>
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
                ) : (
                  'Save models'
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


interface EditorProps {
  editor: Editor;
  vendors: AIVendor[];
  busy: boolean;
  onChange: (patch: Partial<FormState>) => void;
  onOption: (key: string, value: string) => void;
  onKind: (kind: AIProviderKind) => void;
  onCancel: () => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

function IntegrationEditor({
  editor,
  vendors,
  busy,
  onChange,
  onOption,
  onKind,
  onCancel,
  onSubmit,
}: EditorProps) {
  const draft = editor.draft;
  const isVolcengine = draft.kind === 'volcengine_asr';
  const isGatewayKind = draft.kind === 'openai' || draft.kind === 'anthropic';
  const selectedVendor = vendors.find((vendor) => vendor.id === draft.vendor);

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
                {editor.mode === 'create' ? 'Add endpoint' : `Edit ${draft.name}`}
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
                  <label className="form-label" htmlFor="ai-vendor">
                    Supplier
                  </label>
                  {editor.mode === 'create' ? (
                    <select
                      id="ai-vendor"
                      className="form-select"
                      value={draft.vendor}
                      onChange={(event) =>
                        onChange({ vendor: event.target.value })
                      }
                      disabled={busy}
                      required
                    >
                      <option value="">Choose a supplier</option>
                      {vendors.map((vendor) => (
                        <option value={vendor.id} key={vendor.id}>
                          {vendor.label}
                        </option>
                      ))}
                      <option value="__new__">New supplier…</option>
                    </select>
                  ) : (
                    <input
                      id="ai-vendor"
                      className="form-control"
                      value={selectedVendor?.label ?? draft.vendor}
                      disabled
                    />
                  )}
                  <div className="form-text">
                    {editor.mode === 'create'
                      ? 'A supplier can own several protocol/role endpoints and a model catalog.'
                      : 'The supplier is fixed for an existing endpoint.'}
                  </div>
                </div>
                {editor.mode === 'create' && draft.vendor === '__new__' ? (
                  <>
                    <div className="col-md-3">
                      <label className="form-label" htmlFor="ai-new-vendor">
                        Supplier id
                      </label>
                      <input
                        id="ai-new-vendor"
                        className="form-control font-monospace"
                        value={draft.new_vendor}
                        onChange={(event) =>
                          onChange({ new_vendor: event.target.value })
                        }
                        pattern="[a-z0-9][a-z0-9-]{0,62}"
                        placeholder="deepseek"
                        required
                        disabled={busy}
                      />
                    </div>
                    <div className="col-md-3">
                      <label
                        className="form-label"
                        htmlFor="ai-new-vendor-label"
                      >
                        Supplier label
                      </label>
                      <input
                        id="ai-new-vendor-label"
                        className="form-control"
                        value={draft.new_vendor_label}
                        onChange={(event) =>
                          onChange({ new_vendor_label: event.target.value })
                        }
                        placeholder="DeepSeek"
                        disabled={busy}
                      />
                    </div>
                  </>
                ) : null}
                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-name">
                    Endpoint name
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
                  'Add endpoint'
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
