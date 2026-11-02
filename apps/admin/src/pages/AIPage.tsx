import { useEffect, useState, type FormEvent } from 'react';
import { useSearchParams } from 'react-router-dom';

import { api, ApiError } from '../api/client';
import type {
  AIFeature,
  AIIntegration,
  AIIntegrationInput,
  AIModel,
  AIProviderConfig,
  AIProviderKind,
  AIVendor,
  AIVendorCheck,
  AIVendorInput,
  ModelRuntime,
} from '../api/types';
import { EmptyState, ErrorBlock, LoadingBlock } from '../components/AsyncContent';
import { PageHeader } from '../components/PageHeader';
import { tr } from '../i18n';
import { VendorIcon } from '../lib/vendorIcons';
import { useApi } from '../lib/hooks';
import { UsagePanel } from './UsagePanel';

type Message = { kind: 'success' | 'danger'; text: string } | null;

// Supplying models and paying for them are one concern, so the usage report
// lives here instead of on a page of its own. The tab is part of the URL so
// the old /usage bookmark (and any link to it) can land on the right section.
const TABS = [
  { key: 'suppliers', label: 'Suppliers', icon: 'bi-hdd-network' },
  // Model catalogues are owned by a supplier but read across all of them: the
  // same model id can be offered by several suppliers, and that is the question
  // a reader has when picking one. The tab answers it without leaving the page.
  { key: 'models', label: 'Models', icon: 'bi-collection' },
  { key: 'usage', label: 'LLM usage', icon: 'bi-graph-up-arrow' },
] as const;

type TabKey = (typeof TABS)[number]['key'];

type OptionValue = string | number;

interface FormState {
  name: string;
  label: string;
  vendor: string;
  new_vendor: string;
  new_vendor_label: string;
  kind: AIProviderKind;
  base_url: string;
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
    default_model: defaultModel,
    enabled: form.enabled,
    gateway: form.gateway,
    options,
  };
}

export function AIPage() {
  const result = useApi(() => api.aiConfig(), []);
  const [searchParams, setSearchParams] = useSearchParams();
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [modelsEditor, setModelsEditor] = useState<AIVendor | null>(null);
  const [modelsError, setModelsError] = useState('');
  const [keyEditor, setKeyEditor] = useState<AIVendor | null>(null);
  const [keyError, setKeyError] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<AIIntegration | null>(null);
  const [bindings, setBindings] = useState<Record<string, BindingState>>({});
  const [runtimeDraft, setRuntimeDraft] = useState<ModelRuntime | null>(null);
  // The last probe per supplier. It is kept per vendor rather than in the
  // page message so two suppliers can be compared and a later unrelated edit
  // does not erase the evidence that a key worked.
  const [checks, setChecks] = useState<Record<string, AIVendorCheck>>({});

  // Any tab not named in the URL is the first one, so an unknown or stale
  // ``?tab=`` value still lands on a real section.
  const requestedTab = searchParams.get('tab');
  const tab: TabKey = TABS.some((item) => item.key === requestedTab)
    ? (requestedTab as TabKey)
    : 'suppliers';
  const selectTab = (next: TabKey) => {
    const params = new URLSearchParams(searchParams);
    if (next === 'suppliers') {
      params.delete('tab');
    } else {
      params.set('tab', next);
    }
    // A tab is view state, not a page: replacing the history entry keeps Back
    // pointing at whatever page the user came from.
    setSearchParams(params, { replace: true });
  };

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
      return { ...current, draft: { ...current.draft, ...patch } };
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
        text: tr(
          'Name must be lowercase letters, digits and dashes, up to 63 characters.',
        ),
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
            tr(
              'Supplier id must be lowercase letters, digits and dashes, up to 63 characters.',
            ),
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
        throw new Error(tr('Choose a supplier, or create a new one.'));
      }
      if (editor.mode === 'create') {
        await api.createAIIntegration(toInput(editor.draft, vendor));
        setMessage({
          kind: 'success',
          text: tr(
            'Created {{name}}. It is available to the bound features immediately.',
            { name: editor.draft.name },
          ),
        });
      } else {
        await api.updateAIIntegration(
          editor.id,
          toInput(editor.draft, vendor),
        );
        setMessage({
          kind: 'success',
          text: tr('Updated {{name}}.', { name: editor.draft.name }),
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
      setMessage({
        kind: 'success',
        text: response.cleared_features.length
          ? tr(
              'Deleted {{name}}. These features now have no provider: {{features}}.',
              {
                name: response.name,
                features: response.cleared_features.join(', '),
              },
            )
          : tr('Deleted {{name}}.', { name: response.name }),
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
        text: tr('{{feature}} now uses {{target}}.', {
          feature: feature.label,
          target: selectedVendor
            ? `${selectedVendor.label} / ${binding.model || tr('no model')}`
            : tr('no model (choose one before using it)'),
        }),
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
        text: tr('Model runtime saved. The next model call uses the new limits.'),
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
        text: tr('Saved {{label}} models.', {
          label: response.vendor.label,
        }),
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

  const saveVendorKey = async (vendor: AIVendor, apiKey: string) => {
    setBusy(`key:${vendor.id}`);
    setKeyError('');
    try {
      const response = await api.setAIVendorKey(vendor.id, apiKey);
      setMessage({
        kind: 'success',
        text: response.vendor.api_key_present
          ? tr('Saved the {{label}} key. Every call uses it immediately.', {
              label: response.vendor.label,
            })
          : tr('Cleared the {{label}} key.', { label: response.vendor.label }),
      });
      setKeyEditor(null);
      // A probe describes the key that was stored when it ran; a new key
      // invalidates it, so the old verdict must not linger next to the badge.
      setChecks((current) => {
        const next = { ...current };
        delete next[vendor.id];
        return next;
      });
      result.reload();
    } catch (error) {
      setKeyError(describe(error));
    } finally {
      setBusy(null);
    }
  };

  /**
   * Ask the supplier itself whether the stored key works.
   *
   * The probe spends no tokens: it is the supplier's own model list, which is
   * the cheapest endpoint that requires the same credential an internal call
   * uses. It answers two questions the console could otherwise only guess at —
   * whether the key is accepted, and whether the model a feature is bound to
   * still exists. It does not prove that a completion succeeds, and the note
   * beside the result says so.
   */
  const checkVendor = async (vendor: AIVendor) => {
    setBusy(`check:${vendor.id}`);
    setMessage(null);
    try {
      const response = await api.checkAIVendor(vendor.id);
      setChecks((current) => ({ ...current, [vendor.id]: response.check }));
    } catch (error) {
      setMessage({ kind: 'danger', text: describe(error) });
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
        text: tr('{{label}}: {{count}} models synced from the supplier.', {
          label: response.vendor.label,
          count: response.vendor.models.length,
        }),
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
  // Both an internal endpoint and a gateway route send the supplier key the
  // registry stores (the gateway swaps the caller's Winnie key for it), so a
  // key saved on any supplier is in use. This counter stays about the internal
  // endpoints because those are the ones a feature call resolves.
  const keyed = integrations.filter((integration) => !integration.gateway);
  const keysPresent = keyed.filter((integration) => integration.api_key_present);
  // A probe needs an HTTP endpoint the supplier answers on its own behalf. It
  // mirrors the server, which prefers the internal endpoint and falls back to a
  // gateway route, and has nothing to ask a speech-only supplier.
  const canProbe = (vendor: AIVendor) =>
    integrations.some(
      (item) =>
        item.vendor === vendor.id &&
        item.enabled &&
        Boolean(item.base_url) &&
        (item.kind === 'openai' || item.kind === 'anthropic'),
    );
  const disabled = busy !== null;

  return (
    <>
      <PageHeader title="AI providers" />
      <div className="app-content">
        <div className="container-fluid">
          <ul
            className="nav nav-tabs mb-3"
            role="tablist"
            aria-label={tr('AI provider sections')}
          >
            {TABS.map((item) => (
              <li className="nav-item" role="presentation" key={item.key}>
                <button
                  type="button"
                  role="tab"
                  id={`ai-tab-${item.key}`}
                  aria-controls={`ai-panel-${item.key}`}
                  aria-selected={tab === item.key}
                  className={`nav-link${tab === item.key ? ' active' : ''}`}
                  onClick={() => selectTab(item.key)}
                >
                  <i className={`bi ${item.icon} me-1`} aria-hidden="true" />
                  {tr(item.label)}
                </button>
              </li>
            ))}
          </ul>

          {tab === 'usage' ? (
            <div
              role="tabpanel"
              id="ai-panel-usage"
              aria-labelledby="ai-tab-usage"
            >
              <UsagePanel />
            </div>
          ) : tab === 'models' ? (
            <div
              role="tabpanel"
              id="ai-panel-models"
              aria-labelledby="ai-tab-models"
            >
              {result.status === 'loading' ? (
                <LoadingBlock label="Loading AI suppliers…" />
              ) : result.status === 'error' ? (
                <ErrorBlock error={result.error} onRetry={result.reload} />
              ) : (
                <ModelsPanel vendors={vendors} features={features} />
              )}
            </div>
          ) : (
            <div
              role="tabpanel"
              id="ai-panel-suppliers"
              aria-labelledby="ai-tab-suppliers"
            >
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
                    {tr('The AI provider registry is available when')}{' '}
                    <code>ai.enabled</code> {tr('is true in')}{' '}
                    <code>apps/api/config.yaml</code>
                    {tr(
                      '. With it disabled, model features have no model and ASR keeps its YAML default; enable it to choose a model here.',
                    )}
                  </div>
                </>
              ) : data ? (
                <>
                  <div className="alert alert-info" role="note">
                    <i className="bi bi-cpu-fill me-2" aria-hidden="true" />
                    {tr(
                      'Suppliers group endpoints and models; feature bindings and model runtime are stored in',
                    )}{' '}
                    <code>{data.path}</code>
                    {tr(
                      '. Changes take effect on the next model call without restarting the API. This registry is the only model choice:',
                    )}{' '}
                    <code>config.yaml</code> {tr('has no')} <code>llm</code>{' '}
                    {tr('block. Each supplier key is')}{' '}
                    <strong>{tr('stored here with its models')}</strong>
                    {tr(
                      ' and also mirrored into the backup file',
                    )}{' '}
                    <code>{data.env_path}</code>
                    {tr(
                      '. The registry wins; editing only the backup does not change the key.',
                    )}
                  </div>

                  <div className="row g-3 mb-3">
                    <div className="col-6 col-lg-3">
                      <div className="small-box text-bg-primary">
                        <div className="inner">
                          <h3>{vendorGroups.length}</h3>
                          <p>{tr('Suppliers')}</p>
                        </div>
                        <i className="small-box-icon bi bi-hdd-network" aria-hidden="true" />
                        <span className="small-box-footer">
                          {tr('{{endpoints}} endpoints · {{enabled}} enabled', {
                            endpoints: integrations.length,
                            enabled: integrations.filter((item) => item.enabled)
                              .length,
                          })}
                        </span>
                      </div>
                    </div>
                    <div className="col-6 col-lg-3">
                      <div className="small-box text-bg-success">
                        <div className="inner">
                          <h3>{gatewayRoutes}</h3>
                          <p>{tr('Gateway routes')}</p>
                        </div>
                        <i className="small-box-icon bi bi-signpost-split" aria-hidden="true" />
                        <span className="small-box-footer">
                          {tr('OpenAI and Anthropic providers')}
                        </span>
                      </div>
                    </div>
                    <div className="col-6 col-lg-3">
                      <div className="small-box text-bg-info">
                        <div className="inner">
                          <h3>
                            {configured}/{features.length}
                          </h3>
                          <p>{tr('Bound features')}</p>
                        </div>
                        <i className="small-box-icon bi bi-diagram-3" aria-hidden="true" />
                        <span className="small-box-footer">
                          {features.length === 0
                            ? tr('No AI features are registered')
                            : configured === features.length
                              ? tr('Every feature has an explicit provider')
                              : tr('{{count}} need a provider', {
                                  count: features.length - configured,
                                })}
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
                          <p>{tr('Keys configured')}</p>
                        </div>
                        <i className="small-box-icon bi bi-key" aria-hidden="true" />
                        <span className="small-box-footer">
                          {keyed.length === 0
                            ? tr('No provider reads a server-side key')
                            : tr(
                                '{{present}} of {{total}} internal endpoints have a supplier key',
                                {
                                  present: keysPresent.length,
                                  total: keyed.length,
                                },
                              )}
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="card mb-3">
                    <div className="card-header d-flex flex-wrap align-items-center gap-2">
                      <h3 className="card-title mb-0">{tr('Suppliers')}</h3>
                      <div className="ms-auto d-flex flex-wrap gap-2">
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-secondary"
                          onClick={result.reload}
                          disabled={disabled}
                        >
                          <i className="bi bi-arrow-clockwise me-1" aria-hidden="true" />
                          {tr('Refresh')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-primary"
                          onClick={openCreate}
                          disabled={disabled}
                        >
                          <i className="bi bi-plus-lg me-1" aria-hidden="true" />
                          {tr('Add route/API')}
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
                                <th scope="col">{tr('Gateway route')}</th>
                                <th scope="col">{tr('Base URL')}</th>
                                <th scope="col">{tr('API key')}</th>
                                <th scope="col">{tr('Used by')}</th>
                                <th scope="col">{tr('Gateway')}</th>
                                <th scope="col">{tr('State')}</th>
                                <th scope="col" className="text-end">
                                  {tr('Actions')}
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
                                        {vendor.gateway_count === 1
                                          ? tr('{{count}} gateway route', {
                                              count: vendor.gateway_count,
                                            })
                                          : tr('{{count}} gateway routes', {
                                              count: vendor.gateway_count,
                                            })}
                                      </span>
                                      {vendor.base_url ? (
                                        <code className="small text-secondary">
                                          {vendor.base_url}
                                        </code>
                                      ) : (
                                        <span className="small text-warning">
                                          {tr('no API endpoint')}
                                        </span>
                                      )}
                                      <span
                                        className={`badge ${
                                          vendor.api_key_present
                                            ? 'text-bg-success'
                                            : 'text-bg-warning'
                                        }`}
                                        title={tr(
                                          'The key is stored with this supplier and mirrored into {{env}}',
                                          { env: vendor.api_key_env },
                                        )}
                                      >
                                        <i
                                          className="bi bi-key me-1"
                                          aria-hidden="true"
                                        />
                                        {vendor.api_key_present
                                          ? tr('key {{hint}}', {
                                              hint: vendor.api_key_hint,
                                            })
                                          : tr('no key')}
                                      </span>
                                    </div>
                                    <div className="d-flex flex-wrap align-items-center gap-2 mt-2">
                                      {vendor.models.length === 0 ? (
                                        <span className="small text-secondary">
                                          {tr('No models yet; sync them from the supplier.')}
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
                                        <button
                                          type="button"
                                          className="btn btn-sm btn-outline-secondary"
                                          onClick={() => {
                                            setMessage(null);
                                            setKeyError('');
                                            setKeyEditor(vendor);
                                          }}
                                          disabled={disabled}
                                          title={tr(
                                            'Store or replace the supplier API key',
                                          )}
                                        >
                                          <i
                                            className="bi bi-key me-1"
                                            aria-hidden="true"
                                          />
                                          {tr('API key')}
                                        </button>
                                        {canProbe(vendor) ? (
                                          <button
                                            type="button"
                                            className="btn btn-sm btn-outline-secondary"
                                            onClick={() => checkVendor(vendor)}
                                            disabled={disabled || !vendor.api_key_present}
                                            title={tr(
                                              'Ask the supplier for its model list with the stored key',
                                            )}
                                          >
                                            {busy === `check:${vendor.id}` ? (
                                              <>
                                                <span
                                                  className="spinner-border spinner-border-sm me-1"
                                                  aria-hidden="true"
                                                />
                                                {tr('Checking…')}
                                              </>
                                            ) : (
                                              <>
                                                <i
                                                  className="bi bi-shield-check me-1"
                                                  aria-hidden="true"
                                                />
                                                {tr('Check')}
                                              </>
                                            )}
                                          </button>
                                        ) : null}
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
                                              title={tr('Edit the supplier API endpoint')}
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
                                              title={tr('Remove the supplier API endpoint')}
                                            >
                                              <i
                                                className="bi bi-trash me-1"
                                                aria-hidden="true"
                                              />
                                              {tr('Remove API')}
                                            </button>
                                            {vendor.kind === 'openai' ? (
                                              <button
                                                type="button"
                                                className="btn btn-sm btn-outline-secondary"
                                                onClick={() => syncVendor(vendor)}
                                                disabled={disabled}
                                                title={tr(
                                                  'Fetch the model list from the supplier /models API',
                                                )}
                                              >
                                                {busy === `sync:${vendor.id}` ? (
                                                  <>
                                                    <span
                                                      className="spinner-border spinner-border-sm me-1"
                                                      aria-hidden="true"
                                                    />
                                                    {tr('Syncing…')}
                                                  </>
                                                ) : (
                                                  <>
                                                    <i
                                                      className="bi bi-arrow-repeat me-1"
                                                      aria-hidden="true"
                                                    />
                                                    {tr('Sync models')}
                                                  </>
                                                )}
                                              </button>
                                            ) : null}
                                          </>
                                        ) : (
                                          <span className="small text-secondary">
                                            {tr(
                                              'No supplier API. Add it with Add route/API and switch Gateway route off; model sync needs the supplier endpoint.',
                                            )}
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
                                          {tr('Models')}
                                        </button>
                                      </div>
                                      {checks[vendor.id] ? (
                                        <div className="mt-2">
                                          <CheckResult check={checks[vendor.id]} />
                                        </div>
                                      ) : null}
                                    </div>
                                  </td>
                                </tr>
                                {gatewayEndpoints.map((integration) => (
                                  <tr key={integration.id}>
                                    <td style={{ minWidth: '12rem' }}>
                                      <div className="fw-semibold">
                                        {tr('Gateway route · {{kind}}', {
                                          kind: integration.kind_label,
                                        })}
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
                                        {tr('the gateway injects the supplier key')}
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
                                          {tr('Enabled')}
                                        </span>
                                      ) : (
                                        <span className="badge text-bg-secondary">
                                          {tr('Disabled')}
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
                                        {tr('Edit')}
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
                                        {tr('Delete')}
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
                      {tr(
                        "A supplier owns its API endpoint, the models it offers and any public gateway routes. Feature bindings select a model, not an endpoint. The API button edits the supplier's server-side endpoint;",
                      )}{' '}
                      <strong>{tr('Sync models')}</strong> {tr('reads its')}{' '}
                      <code>/models</code>{' '}
                      {tr("API. A gateway route is called with a Winnie key at")}{' '}
                      <code>/{'{name}'}/v1/…</code>
                      {tr(
                        '; the gateway injects the supplier key downstream, and the environment variable is only for internal calls.',
                      )}
                    </div>
                  </div>

                  <div className="card">
                    <div className="card-header">
                      <h3 className="card-title mb-0">{tr('Feature bindings')}</h3>
                    </div>
                    <div className="card-body p-0">
                      <div className="table-responsive">
                        <table className="table align-middle mb-0">
                          <thead>
                            <tr>
                              <th scope="col">{tr('Feature')}</th>
                              <th scope="col">{tr('Model')}</th>
                              <th scope="col">{tr('Source')}</th>
                              <th scope="col" className="text-end">
                                {tr('Action')}
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
                                      feature.kinds.includes(item.kind),
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
                                      {tr('Model for {{feature}}', {
                                        feature: feature.label,
                                      })}
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
                                      <option value="">{tr('Not configured')}</option>
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
                                              {model.context_window ? (
                                                <>
                                                  {' · '}
                                                  {tr('{{tokens}} ctx', {
                                                    tokens:
                                                      model.context_window.toLocaleString(),
                                                  })}
                                                </>
                                              ) : null}
                                            </option>
                                          ))}
                                        </optgroup>
                                      ))}
                                      {binding.vendor && binding.model && !known ? (
                                        <option value={value}>
                                          {binding.model} {tr('(not in the catalog)')}
                                        </option>
                                      ) : null}
                                    </select>
                                    <div className="form-text">
                                      {feature.source === 'none'
                                        ? tr('Choose the model this feature calls.')
                                        : !known
                                          ? tr(
                                              'This model is not in a supplier catalog; sync it first.',
                                            )
                                          : tr(
                                              'Every feature calls the model selected here.',
                                            )}
                                    </div>
                                  </td>
                                  <td>
                                    {feature.source === 'admin' ? (
                                      <span className="badge text-bg-success">
                                        {tr('Admin')}
                                      </span>
                                    ) : (
                                      <span className="badge text-bg-warning">
                                        {tr('Not configured')}
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
                                          {tr('Saving…')}
                                        </>
                                      ) : (
                                        tr('Save')
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
                      {tr(
                        'Every feature calls the model selected here. The supplier is resolved from that model; there is no endpoint/provider choice in a feature binding. Models come from the supplier catalog; sync them with',
                      )}{' '}
                      <strong>{tr('Sync models')}</strong> {tr('or add them under')}{' '}
                      <strong>{tr('Models')}</strong>. <code>config.yaml</code>{' '}
                      {tr('no longer selects a model.')}
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
          )}
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

      {keyEditor ? (
        <KeyEditor
          vendor={keyEditor}
          error={keyError}
          busy={busy === `key:${keyEditor.id}`}
          onSave={saveVendorKey}
          onCancel={() => {
            setKeyError('');
            setKeyEditor(null);
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

interface CatalogRow {
  /** The model id as the supplier spells it. */
  id: string;
  /** Suppliers whose synced catalogue contains this id. */
  suppliers: AIVendor[];
  /** Features bound to this model id. */
  features: AIFeature[];
}

// Invert the per-supplier catalogues into one row per model id.
//
// A supplier owns its models, so the same id legitimately appears under more
// than one supplier (an OpenAI-compatible endpoint and its vendor, say). The
// count that matters is distinct suppliers, not endpoints.
function catalogRows(vendors: AIVendor[], features: AIFeature[]): CatalogRow[] {
  const byId = new Map<string, CatalogRow>();
  for (const vendor of vendors) {
    for (const model of vendor.models) {
      const id = model.id.trim();
      if (!id) {
        continue;
      }
      const row = byId.get(id) ?? { id, suppliers: [], features: [] };
      // A catalogue that repeats an id must not count its supplier twice.
      if (!row.suppliers.some((item) => item.id === vendor.id)) {
        row.suppliers.push(vendor);
      }
      byId.set(id, row);
    }
  }
  for (const feature of features) {
    const model = feature.model.trim();
    if (model) {
      byId.get(model)?.features.push(feature);
    }
  }
  // Models offered by more than one supplier are the ones worth knowing about:
  // they keep working when one supplier is disabled or out of quota.
  return [...byId.values()].sort(
    (a, b) =>
      b.suppliers.length - a.suppliers.length || a.id.localeCompare(b.id),
  );
}

function ModelsPanel({
  vendors,
  features,
}: {
  vendors: AIVendor[];
  features: AIFeature[];
}) {
  const [query, setQuery] = useState('');

  const rows = catalogRows(vendors, features);
  const needle = query.trim().toLowerCase();
  const visible = needle
    ? rows.filter(
        (row) =>
          row.id.toLowerCase().includes(needle) ||
          row.suppliers.some(
            (vendor) =>
              vendor.id.toLowerCase().includes(needle) ||
              vendor.label.toLowerCase().includes(needle),
          ),
      )
    : rows;
  const shared = rows.filter((row) => row.suppliers.length > 1).length;

  return (
    <div className="card mb-3">
      <div className="card-header d-flex flex-wrap align-items-center gap-2">
        <h3 className="card-title mb-0">{tr('Models')}</h3>
        {rows.length > 0 ? (
          <span className="badge text-bg-secondary">
            {tr('{{count}} models', { count: rows.length })}
          </span>
        ) : null}
        {shared > 0 ? (
          <span className="badge text-bg-primary">
            {tr('{{count}} multi-supplier', { count: shared })}
          </span>
        ) : null}
      </div>
      <div className="card-body">
        <p className="small text-secondary">
          {tr(
            'Every model in a connected supplier catalogue, one row per model id. The count is suppliers, not endpoints: a model offered by more than one supplier keeps working when one of them is disabled.',
          )}
        </p>
        {rows.length === 0 ? (
          <EmptyState message="No models yet. Sync a supplier on the Suppliers tab to list its models." />
        ) : (
          <>
            <div className="row g-2 align-items-end mb-3">
              <div className="col-sm-6 col-lg-4">
                <label className="form-label small" htmlFor="ai-model-search">
                  {tr('Filter models')}
                </label>
                <input
                  id="ai-model-search"
                  className="form-control form-control-sm"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder={tr('Model id or supplier')}
                />
              </div>
              <div className="col-sm-6 col-lg-8">
                <p className="small text-secondary mb-0">
                  {tr('{{shown}} of {{total}} models shown', {
                    shown: visible.length,
                    total: rows.length,
                  })}
                </p>
              </div>
            </div>

            {visible.length === 0 ? (
              <EmptyState message="No model matches the filter." />
            ) : (
              <div className="table-responsive">
                <table className="table table-striped align-middle mb-0">
                  <thead>
                    <tr>
                      <th scope="col">{tr('Model')}</th>
                      <th scope="col">{tr('Suppliers')}</th>
                      <th scope="col">{tr('Offered by')}</th>
                      <th scope="col">{tr('Used by')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <code>{row.id}</code>
                        </td>
                        <td>
                          <span
                            className={`badge ${
                              row.suppliers.length > 1
                                ? 'text-bg-primary'
                                : 'text-bg-secondary'
                            }`}
                          >
                            {row.suppliers.length === 1
                              ? tr('{{count}} supplier', { count: 1 })
                              : tr('{{count}} suppliers', {
                                  count: row.suppliers.length,
                                })}
                          </span>
                        </td>
                        <td>
                          <div className="d-flex flex-wrap align-items-center gap-2">
                            {row.suppliers.map((vendor) => (
                              <span
                                className="d-inline-flex align-items-center gap-1"
                                key={vendor.id}
                                title={vendor.id}
                              >
                                <VendorIcon id={vendor.id} label={vendor.label} />
                                <span className="small">{vendor.label}</span>
                              </span>
                            ))}
                          </div>
                        </td>
                        <td>
                          {row.features.length === 0 ? (
                            <span className="small text-secondary">—</span>
                          ) : (
                            <div className="d-flex flex-wrap gap-1">
                              {row.features.map((feature) => (
                                <span
                                  className="badge text-bg-light border"
                                  key={feature.key}
                                >
                                  {feature.label}
                                </span>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
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
        <h3 className="card-title mb-0">{tr('Model runtime')}</h3>
      </div>
      <div className="card-body">
        <p className="small text-secondary">
          {tr(
            'Shared limits for every OpenAI-compatible call. They apply to the assistant, translation and minutes; the model and endpoint come from the provider binding above.',
          )}
        </p>
        <div className="row g-3">
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-timeout_seconds">
              {tr('Timeout (seconds)')}
            </label>
            {number('timeout_seconds')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-max_output_tokens">
              {tr('Max output tokens')}
            </label>
            {number('max_output_tokens')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-temperature">
              {tr('Temperature')}
            </label>
            {number('temperature', '0.1')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-max_retries">
              {tr('Max retries')}
            </label>
            {number('max_retries')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-retry_backoff_seconds">
              {tr('Retry backoff (seconds)')}
            </label>
            {number('retry_backoff_seconds', '0.1')}
          </div>
          <div className="col-md-4">
            <label
              className="form-label"
              htmlFor="runtime-translation_batch_chars"
            >
              {tr('Translation batch (characters)')}
            </label>
            {number('translation_batch_chars', '100', '500')}
          </div>
          <div className="col-md-4">
            <label className="form-label" htmlFor="runtime-summary_chunk_chars">
              {tr('Minutes chunk (characters)')}
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
              {tr('Saving…')}
            </>
          ) : (
            tr('Save runtime')
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
                {tr('Models for {{vendor}}', { vendor: vendor.label })}
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Close')}
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
                    {tr('Supplier label')}
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
                  <label className="form-label">{tr('Supplier id')}</label>
                  <input
                    className="form-control font-monospace"
                    value={vendor.id}
                    disabled
                  />
                </div>
              </div>
              <p className="small text-secondary">
                {tr(
                  'Model ids are sent to the endpoint. A feature binding may use the endpoint default or one of these entries; removing a model that is still used is rejected.',
                )}
              </p>
              <div className="table-responsive">
                <table className="table align-middle">
                  <thead>
                    <tr>
                      <th scope="col">{tr('Model id')}</th>
                      <th scope="col">{tr('Label')}</th>
                      <th scope="col">{tr('Context')}</th>
                      <th scope="col">{tr('Max output')}</th>
                      <th scope="col">{tr('Notes')}</th>
                      <th scope="col" className="text-end">
                        {tr('Action')}
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
                            placeholder={tr('Optional')}
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
                            {tr('Remove')}
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
                {tr('Add model')}
              </button>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={onCancel}
                disabled={busy}
              >
                {tr('Cancel')}
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm me-1"
                      aria-hidden="true"
                    />
                    {tr('Saving…')}
                  </>
                ) : (
                  tr('Save models')
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
                {editor.mode === 'create'
                  ? tr('Add endpoint')
                  : tr('Edit {{name}}', { name: draft.name })}
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Close')}
                onClick={onCancel}
                disabled={busy}
              />
            </div>
            <div className="modal-body">
              <div className="row g-3">
                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-vendor">
                    {tr('Supplier')}
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
                      <option value="">{tr('Choose a supplier')}</option>
                      {vendors.map((vendor) => (
                        <option value={vendor.id} key={vendor.id}>
                          {vendor.label}
                        </option>
                      ))}
                      <option value="__new__">{tr('New supplier…')}</option>
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
                      ? tr(
                          'A supplier can own several protocol/role endpoints and a model catalog.',
                        )
                      : tr('The supplier is fixed for an existing endpoint.')}
                  </div>
                </div>
                {editor.mode === 'create' && draft.vendor === '__new__' ? (
                  <>
                    <div className="col-md-3">
                      <label className="form-label" htmlFor="ai-new-vendor">
                        {tr('Supplier id')}
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
                        {tr('Supplier label')}
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
                    {tr('Endpoint name')}
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
                    {tr(
                      'Lowercase slug. It becomes the gateway path prefix when the provider is exposed as a route; it cannot change after creation.',
                    )}
                  </div>
                </div>
                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-label">
                    {tr('Label')}
                  </label>
                  <input
                    id="ai-label"
                    className="form-control"
                    value={draft.label}
                    onChange={(event) => onChange({ label: event.target.value })}
                    placeholder={tr('Shown in the console')}
                    disabled={busy}
                  />
                </div>

                <div className="col-md-6">
                  <label className="form-label" htmlFor="ai-kind">
                    {tr('Kind')}
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
                    <option value="openai">{tr('OpenAI-compatible')}</option>
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
                      {tr('Enabled')}
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
                        {tr('Gateway route')}
                      </label>
                    </div>
                  ) : null}
                </div>

                <>
                    <div className="col-12">
                      <label className="form-label" htmlFor="ai-base-url">
                        {tr('Base URL')}
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
                          ? tr(
                              'Volcengine speech endpoint root; no /v1 suffix is added.',
                            )
                          : draft.kind === 'anthropic'
                            ? tr('Anthropic API root; /v1 is added when missing.')
                            : tr(
                                'OpenAI-compatible: the part before /v1; it is added automatically when missing.',
                              )}
                      </div>
                    </div>
                    {!isVolcengine ? (
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="ai-default-model">
                          {tr('Default model')}
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
                          {tr('Used when a feature binding does not override it.')}
                        </div>
                      </div>
                    ) : null}
                </>

                {isVolcengine ? (
                  <div className="col-12">
                    <hr />
                    <h6>{tr('Volcengine Doubao options')}</h6>
                    <div className="row g-3">
                      <div className="col-md-6">
                        <label className="form-label" htmlFor="ai-resource-id">
                          {tr('Resource ID')}
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
                          {tr('Model name')}
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
                          {tr('Streaming endpoint')}
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
                          {tr('Streaming resource ID')}
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
                          {tr('Max inline upload (MB)')}
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
                          {tr('Poll interval (seconds)')}
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
                          {tr('Poll timeout (seconds)')}
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
                {tr('Cancel')}
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm me-1"
                      aria-hidden="true"
                    />
                    {tr('Saving…')}
                  </>
                ) : editor.mode === 'create' ? (
                  tr('Add endpoint')
                ) : (
                  tr('Save changes')
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

function KeyEditor({
  vendor,
  error,
  busy,
  onSave,
  onCancel,
}: {
  vendor: AIVendor;
  error: string;
  busy: boolean;
  onSave: (vendor: AIVendor, apiKey: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState('');
  const [revealed, setRevealed] = useState(false);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    onSave(vendor, value.trim());
  };

  return (
    <>
      <div
        className="modal fade show d-block"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ai-key-title"
      >
        <div className="modal-dialog modal-dialog-centered">
          <form className="modal-content" onSubmit={submit}>
            <div className="modal-header">
              <h5 className="modal-title" id="ai-key-title">
                {tr('API key for {{vendor}}', { vendor: vendor.label })}
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Close')}
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
              <p className="small text-secondary">
                {tr(
                  'The key is stored on this supplier, next to its models, and every call uses it. A copy is also written to',
                )}{' '}
                <code>{vendor.api_key_backup_path}</code>
                {tr(
                  ' as a backup; the registry wins if the two differ. Leave the field empty to clear the key.',
                )}
              </p>
              <label className="form-label" htmlFor="ai-key-value">
                {tr('Key')}
                {vendor.api_key_present ? (
                  <span className="text-secondary">
                    {' '}
                    {tr('(currently {{hint}})', { hint: vendor.api_key_hint })}
                  </span>
                ) : null}
              </label>
              <div className="input-group">
                <input
                  id="ai-key-value"
                  className="form-control font-monospace"
                  type={revealed ? 'text' : 'password'}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  placeholder={tr('Paste the supplier API key')}
                  autoComplete="off"
                  spellCheck={false}
                  disabled={busy}
                />
                <button
                  type="button"
                  className="btn btn-outline-secondary"
                  onClick={() => setRevealed((current) => !current)}
                  disabled={busy}
                  aria-label={
                    revealed ? tr('Hide the key') : tr('Show the key')
                  }
                >
                  <i
                    className={`bi ${revealed ? 'bi-eye-slash' : 'bi-eye'}`}
                    aria-hidden="true"
                  />
                </button>
              </div>
              <div className="form-text">
                {tr(
                  'The console never reads the stored value back; it shows only the last four characters.',
                )}
              </div>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={onCancel}
                disabled={busy}
              >
                {tr('Cancel')}
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm me-1"
                      aria-hidden="true"
                    />
                    {tr('Saving…')}
                  </>
                ) : (
                  tr('Save key')
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
                {tr('Delete {{name}}?', {
                  name: integration.label || integration.name,
                })}
              </h5>
              <button
                type="button"
                className="btn-close"
                aria-label={tr('Close')}
                onClick={onCancel}
                disabled={busy}
              />
            </div>
            <div className="modal-body">
              <p className="mb-2">
                {tr(
                  'The provider is removed from the registry and its gateway route disappears immediately.',
                )}
              </p>
              {integration.used_by.length > 0 ? (
                <div className="alert alert-warning mb-0" role="alert">
                  {tr('These features will fall back to')}{' '}
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
                {tr('Cancel')}
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
                    {tr('Deleting…')}
                  </>
                ) : (
                  tr('Delete provider')
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

/**
 * One probe result, in one line.
 *
 * The failures are different facts and must read differently: a rejected key
 * (the owner pasted the wrong value), a supplier that answered with an error
 * (not the key's fault), and a supplier with nothing to probe. A success still
 * says what it did *not* test — "the key works" and "a completion works" are
 * different claims, and the console should not blur them.
 */
function CheckResult({ check }: { check: AIVendorCheck }) {
  const view = (() => {
    switch (check.state) {
      case 'ok':
        return {
          tone: 'text-success-emphasis',
          icon: 'bi-check-circle-fill',
          text: tr('The supplier accepted the key · {{count}} models', {
            count: check.models ?? 0,
          }),
        };
      case 'unauthorized':
        return {
          tone: 'text-danger-emphasis',
          icon: 'bi-x-octagon-fill',
          text: tr('The supplier rejected the key (HTTP {{status}}). {{detail}}', {
            status: check.status ?? 0,
            detail: check.detail,
          }),
        };
      case 'unsupported':
        return {
          tone: 'text-secondary',
          icon: 'bi-info-circle',
          text: tr('This supplier has no model list to check.'),
        };
      default:
        return {
          tone: 'text-danger-emphasis',
          icon: 'bi-exclamation-triangle-fill',
          text: check.status
            ? tr('The check failed (HTTP {{status}}). {{detail}}', {
                status: check.status,
                detail: check.detail,
              })
            : tr('The check could not reach the supplier. {{detail}}', {
                detail: check.detail,
              }),
        };
    }
  })();
  return (
    <div className={`small ${view.tone}`} role="status">
      <i className={`bi ${view.icon} me-1`} aria-hidden="true" />
      {view.text}
      {check.state === 'ok' ? (
        <div className="text-secondary">
          {tr(
            'A check proves the key and the endpoint; it does not prove that a completion succeeds.',
          )}
        </div>
      ) : null}
      {check.missing_models.length > 0 ? (
        <div className="text-warning-emphasis">
          {tr('Missing from the supplier catalog: {{models}}', {
            models: check.missing_models.join(', '),
          })}
        </div>
      ) : null}
    </div>
  );
}
