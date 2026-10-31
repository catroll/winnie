/** Response shapes of the Winnie API used by the console. */

export interface Health {
  status: string;
  version: string;
}

export interface Readiness {
  status: string;
  database: string;
  queue: string;
}

export interface GatewayRoute {
  name: string;
  protocol: 'openai' | 'anthropic';
  base_url: string;
  api_base: string;
  paths: string[];
}

export interface RouteList {
  routes: GatewayRoute[];
}

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
  user: User;
}

export type MeetingStatus =
  | 'created'
  | 'uploaded'
  | 'queued'
  | 'transcribing'
  | 'transcribed'
  | 'translating'
  | 'summarizing'
  | 'ready'
  | 'failed';

export interface Meeting {
  id: string;
  title: string;
  status: MeetingStatus;
  stage: string | null;
  source: 'file' | 'realtime';
  local_language: string;
  audio_duration_ms: number | null;
  audio_deleted_at: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface MeetingList {
  items: Meeting[];
  total: number;
}

export interface Segment {
  seq: number;
  start_ms: number | null;
  end_ms: number | null;
  speaker: string | null;
  text: string;
  detected_language: string | null;
  translated_text: string | null;
  translation_status: string;
}

export interface Transcript {
  meeting_id: string;
  segments: Segment[];
}

export interface Topic {
  title: string;
  points: string[];
}

export interface Decision {
  text: string;
  context: string;
}

export interface ActionItem {
  text: string;
  owner: string | null;
  due_date: string | null;
  status: string;
}

export interface Highlight {
  segment_seq: number;
  text: string;
}

export interface Minutes {
  meeting_id: string;
  summary: string;
  topics: Topic[];
  decisions: Decision[];
  action_items: ActionItem[];
  highlights: Highlight[];
  model: string;
  prompt_version: string;
  created_at: string;
}

/** Local document index, as exposed through the Winnie API proxy. */

export interface IndexRoot {
  name: string;
  path: string;
  include: string[];
  exclude: string[];
  watch?: boolean;
  tier?: string;
}

export interface IndexEditableConfig {
  roots: IndexRoot[];
  formats: { prose: string[] };
  scan: { interval_seconds: number; watch: boolean; debounce_ms: number };
  index: {
    max_file_mb: number;
    max_chunks_per_doc: number;
    chunk_tokens: number;
    overlap_tokens: number;
  };
  retrieval: { limit: number; bm25_k: number; max_per_doc: number };
  skill: { enabled: boolean; target: string };
}

export interface IndexConfigResponse {
  path: string;
  config: IndexEditableConfig;
}

export interface IndexRootStatus {
  name: string;
  path: string;
  documents: number;
  chunks: number;
}

export interface IndexBuildResult {
  roots: number;
  seen: number;
  added: number;
  changed: number;
  unchanged: number;
  renamed: number;
  deleted: number;
  failed: number;
  sealed: number;
  chunks: number;
  bytes_read: number;
  at?: string;
  dry_run?: boolean;
  errors?: string[];
}

export interface IndexStatus {
  documents: Record<string, number>;
  chunks: number;
  roots: IndexRootStatus[];
  last_sweep_at: string | null;
  failures: { path: string; error: string }[];
  db_bytes: number;
  db_path: string;
  config_path: string;
  endpoints: { mcp: string; http: string };
  skill: { target: string; installed: boolean };
  last_build: IndexBuildResult | null;
}

export interface IndexSearchHit {
  chunk_id: number;
  doc_id: number;
  root: string;
  path: string;
  heading: string;
  start_line: number;
  end_line: number;
  score: number;
  tokens: number;
  mtime: string;
  text_hash: string | null;
  text: string;
  truncated: boolean;
}

export interface IndexSearchFile {
  root: string;
  path: string;
  chunks: number;
  best_score: number;
  heading: string;
  start_line: number;
  end_line: number;
  chunk_ids: number[];
}

export interface IndexSearchResponse {
  query: string;
  expression: string | null;
  terms: string[];
  roots: string[];
  elapsed_ms: number;
  candidates: number;
  returned: number;
  dropped: number;
  hint: string;
  hits: IndexSearchHit[];
  files: IndexSearchFile[];
}

/** Assistant scheduled tasks, as defined by the assistant and read by the console. */

export interface AssistantTask {
  id: string;
  title: string;
  schedule: string;
  prompt: string;
  enabled: boolean;
  webhook: string;
  next_run: string | null;
  last_run: string | null;
  last_status: string | null;
  last_error: string | null;
}

export interface AssistantTaskList {
  tasks: AssistantTask[];
  /** Definitions the scheduler ignored, with the reason. */
  errors: string[];
}

/** One recorded execution of an assistant scheduled task. */

export interface AssistantTaskRun {
  task_id: string;
  status: string;
  started_at: string;
  finished_at: string;
  conversation_id: string;
  error: string;
  delivery_error: string;
}

export interface AssistantTaskRunList {
  runs: AssistantTaskRun[];
  total: number;
  offset: number;
  limit: number;
  /** Log lines the API could not parse, for example a partial last line. */
  skipped: number;
}

export interface AssistantStatus {
  model: string;
  search: string;
  timezone: string;
  root: string;
  conversations: number;
  memory: { enabled: boolean; entries: number; files: number };
  index: { enabled: boolean; url: string };
  scheduler: { running: boolean; tasks: number; errors: string[] };
}

/** Assistant conversations, whichever channel they started from. */

export type AssistantMessageRole = 'user' | 'assistant' | 'tool';

export interface AssistantMessage {
  role: AssistantMessageRole;
  name: string;
  text: string;
  at: string;
}

export interface AssistantConversationSummary {
  id: string;
  title: string;
  created_at: string;
  source: string;
  channel: string;
  message_count: number;
  preview: string;
}

export interface AssistantConversationList {
  items: AssistantConversationSummary[];
  total: number;
}

export interface AssistantConversation {
  id: string;
  title: string;
  created_at: string;
  source: string;
  channel: string;
  messages: AssistantMessage[];
}

export interface AssistantChatResponse {
  conversation_id: string;
  reply: string;
  tools_used: string[];
  usage_tokens: number;
}

/** Admin-managed AI providers and the features that use them. */

export type AIProviderKind = 'openai' | 'anthropic' | 'volcengine_asr' | 'fake';

export interface AIIntegration {
  id: string;
  name: string;
  label: string;
  kind: AIProviderKind;
  kind_label: string;
  base_url: string;
  api_key_env: string;
  api_key_present: boolean;
  default_model: string;
  enabled: boolean;
  /** Whether an OpenAI/Anthropic provider is exposed as a gateway route. */
  gateway: boolean;
  options: Record<string, unknown>;
  used_by: string[];
  gateway_paths: string[];
  created_at: string;
  updated_at: string;
}

export interface AIIntegrationInput {
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

export interface AIFeature {
  key: string;
  label: string;
  description: string;
  kinds: AIProviderKind[];
  requires_model: boolean;
  integration_id: string | null;
  model: string;
  source: 'admin' | 'none';
  integration_name: string;
}

export interface ModelRuntime {
  timeout_seconds: number;
  max_output_tokens: number;
  temperature: number;
  max_retries: number;
  retry_backoff_seconds: number;
  translation_batch_chars: number;
  summary_chunk_chars: number;
}

export interface AIKind {
  kind: AIProviderKind;
  label: string;
  description: string;
  gateway: boolean;
  requires_base_url: boolean;
  features: string[];
}

export interface AIProviderConfig {
  path: string;
  runtime: ModelRuntime;
  integrations: AIIntegration[];
  features: AIFeature[];
  kinds: AIKind[];
  updated_at: string;
}

/** Recorded model usage, estimated costs and provider balances. */

export interface UsageTotals {
  requests: number;
  errors: number;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cost_micros: number;
  provider_priced_requests: number;
  estimated_requests: number;
  unpriced_requests: number;
  unreported_requests: number;
}

export interface UsageDayRow extends UsageTotals {
  date: string;
}

export interface UsageCapabilityRow extends UsageTotals {
  capability: string;
}

export interface UsageModelRow extends UsageTotals {
  provider: string;
  model: string;
}

export interface UsagePriceRow {
  match: string;
  input_per_million: number;
  output_per_million: number;
  cached_input_per_million: number | null;
  cache_write_per_million: number | null;
}

export interface UsagePeriod {
  days: number;
  from: string;
  to: string;
}

export interface UsageOverview {
  currency: string;
  period: UsagePeriod;
  generated_at: string;
  totals: UsageTotals;
  by_day: UsageDayRow[];
  by_capability: UsageCapabilityRow[];
  by_model: UsageModelRow[];
  prices: UsagePriceRow[];
}

export interface UsageRecord {
  id: string;
  at: string;
  capability: string;
  source: string;
  provider: string;
  model: string;
  status: string;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  total_tokens: number | null;
  provider_cost_micros: number | null;
  error: string;
  detail: Record<string, unknown>;
  has_usage: boolean;
  cost_micros: number | null;
  cost_source: string;
  cost_reason: string;
  cost_currency: string;
}

export interface UsageRecordList {
  period: UsagePeriod;
  items: UsageRecord[];
  total: number;
  limit: number;
  offset: number;
}

export interface UsageBalanceLine {
  label: string;
  value: string;
}

export interface UsageBalance {
  integration_id: string;
  name: string;
  label: string;
  kind: string;
  base_url: string;
  supported: boolean;
  currency: string;
  total: string;
  lines: UsageBalanceLine[];
  fetched_at: string;
  error: string;
  cached: boolean;
  endpoint: string;
}

export interface UsageBalanceList {
  ttl_seconds: number;
  balances: UsageBalance[];
}


/** Chat skills: a bundle of tools, prompt instructions and permissions. */

export interface AssistantSkill {
  name: string;
  title: string;
  description: string;
  instructions: string;
  tools: string[];
  permissions: string[];
  enabled: boolean;
  available: boolean;
  unavailable_reason: string;
  toggleable: boolean;
  scheduled_safe: boolean;
}

export interface AssistantSkillList {
  skills: AssistantSkill[];
}
