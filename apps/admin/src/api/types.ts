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
  path_prefix: string;
  vendor: string;
  api_key_env: string;
  credential_present: boolean;
}

export interface RouteList {
  routes: GatewayRoute[];
}

export interface GatewayKey {
  id: string;
  label: string;
  created_at: string;
  revoked_at: string | null;
  routes: string[];
  /** Published model name -> "supplier/model", applied to this key's calls. */
  overrides: Record<string, string>;
  /** Account that created the key; empty for a key older than ownership. */
  owner_id: string;
  owner_name: string;
}

/** A model name the platform publishes, with where it is served by default. */
export interface PublishedModel {
  id: string;
  label: string;
  aliases: string[];
  vendor: string;
  model: string;
}

export interface GatewayKeyList {
  keys: GatewayKey[];
  /** Route names a key may be limited to; not secret. */
  routes: string[];
  /** The model names a client may ask for, and where they go by default. */
  models: PublishedModel[];
  /** Suppliers that have a gateway route, for an override to point at. */
  suppliers: string[];
}

export interface GatewayKeyCreated {
  key: GatewayKey;
  /** Shown once; never stored by the browser or the API. */
  secret: string;
}

export interface GatewayKeyDraft {
  label: string;
  routes: string[];
  overrides: Record<string, string>;
}

export type UserRole = 'admin' | 'user';

export interface User {
  id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  created_at: string;
}

export interface AdminUser {
  id: string;
  username: string;
  display_name: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_session_at: string | null;
  active_sessions: number;
}

export interface AdminUserList {
  items: AdminUser[];
  total: number;
  limit: number;
  offset: number;
}

export interface AdminUserCreate {
  username: string;
  display_name?: string | null;
  role: UserRole;
}

export interface AdminUserUpdate {
  display_name?: string | null;
  role?: UserRole;
  is_active?: boolean;
}

export interface TemporaryPassword {
  user: AdminUser;
  temporary_password: string;
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

export type AIProviderKind = 'openai' | 'anthropic' | 'volcengine_asr';

export interface AIModel {
  id: string;
  label: string;
  owned_by: string;
  created: number | null;
  context_window: number | null;
  max_output_tokens: number | null;
  notes: string;
}

export interface AIVendor {
  id: string;
  label: string;
  models: AIModel[];
  integration_count: number;
  gateway_count: number;
  internal_integration_id: string;
  kind: AIProviderKind | '';
  base_url: string;
  /** The env variable the key is mirrored into (a backup, not the source). */
  api_key_env: string;
  api_key_present: boolean;
  /** The last four characters of the stored key, or an empty string. */
  api_key_hint: string;
  /** Where the backup copy lives on this host. */
  api_key_backup_path: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AIVendorInput {
  id: string;
  label: string;
  models: AIModel[];
}

/**
 * What probing a supplier with its stored key found.
 *
 * `state` separates the outcomes the owner acts on differently: `ok` (the
 * supplier listed its models), `unauthorized` (401/403, so the key is wrong or
 * revoked), `error` (any other status, or the endpoint was unreachable), and
 * `unsupported` (a speech-only supplier has no cheap HTTP probe).
 * `missing_models` names the feature/model pairs bound to this supplier that
 * its catalog no longer contains.
 */
export interface AIVendorCheck {
  state: 'ok' | 'unauthorized' | 'error' | 'unsupported';
  status: number | null;
  url: string;
  models: number | null;
  missing_models: string[];
  detail: string;
}

export interface AIIntegration {
  id: string;
  name: string;
  label: string;
  vendor: string;
  vendor_label: string;
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
  vendor: string;
  kind: AIProviderKind;
  base_url: string;
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
  vendor: string;
  vendor_label: string;
  /** The alias the feature is bound to; empty for a direct binding. */
  virtual: string;
  virtual_label: string;
}

/**
 * A stable alias for one supplier model.
 *
 * Features point at the alias instead of the supplier model, so retargeting
 * every feature that shares a role is one edit on this record.
 */
export interface AIVirtualModel {
  id: string;
  label: string;
  /** Other call names this platform model answers to. */
  aliases: string[];
  vendor: string;
  vendor_label: string;
  model: string;
  model_label: string;
  /** Feature keys bound to this alias. */
  used_by: string[];
  created_at: string;
  updated_at: string;
}

export interface AIVirtualModelInput {
  id: string;
  label: string;
  aliases: string[];
  vendor: string;
  model: string;
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
  /** The env backup the console mirrors every supplier key into. */
  env_path: string;
  runtime: ModelRuntime;
  vendors: AIVendor[];
  integrations: AIIntegration[];
  virtual_models: AIVirtualModel[];
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

/** News pipeline: runs, stages, drafts and their reviews. */

export type NewsStageStatus = 'pending' | 'running' | 'ok' | 'failed' | 'skipped';
export type NewsRunStatus = 'running' | 'done' | 'partial' | 'failed';

export interface NewsStage {
  name: string;
  status: NewsStageStatus;
  started_at: string | null;
  finished_at: string | null;
  count: number | null;
  detail: string;
}

export interface NewsRun {
  id: string;
  created_at: string;
  updated_at: string;
  status: NewsRunStatus;
  source_types: string[];
  stages: NewsStage[];
  stats: Record<string, number | string>;
  error: string;
}

export interface NewsStatus {
  enabled: boolean;
  ready: boolean;
  llm: string;
  search: string;
  active_run: string | null;
  events: number;
  runs: number;
  root: string;
}

export interface NewsDraftSummary {
  event_id: string;
  title: string;
  category: string;
  revision: number;
  tags: string[];
  sources: number;
  uncertainties: number;
  generated_at: string;
}

export interface NewsReviewProblem {
  type: string;
  severity: string;
  detail: string;
  origin: string;
}

export interface NewsReview {
  schema: string;
  event_id: string;
  passed: boolean;
  score: number;
  problems: NewsReviewProblem[];
  feedback: string;
  retry: number;
  max_retries: number;
  status: 'passed' | 'retry' | 'needs_human';
  checked_at: string;
}

export interface NewsDraftSource {
  title: string;
  url: string;
  source: string | null;
  published_at: string | null;
}

export interface NewsDraft {
  schema: string;
  event_id: string;
  title: string;
  category: string;
  brief: string;
  content: string;
  tags: string[];
  sources: NewsDraftSource[];
  uncertainties: string[];
  language: string;
  model: string;
  generated_at: string;
  revision: number;
}

export interface NewsDraftDetail {
  draft: NewsDraft;
  review: NewsReview | null;
}

export interface NewsRunDrafts {
  run_id: string;
  drafts: NewsDraftSummary[];
  reviews: NewsReview[];
}

export type NewsDigestFormat = 'standard' | 'brief' | 'table';

export interface NewsDigestItem {
  event_id: string;
  category: string;
  date: string;
  title: string;
  brief: string;
  tags: string[];
  event_type: string;
}

export interface NewsDigestSection {
  category: string;
  items: NewsDigestItem[];
}

export interface NewsDigest {
  schema: string;
  generated_at: string;
  window_days: number;
  start_date: string;
  end_date: string;
  requested: number;
  count: number;
  format: NewsDigestFormat;
  sections: NewsDigestSection[];
  excluded: Record<string, number>;
  notes: string[];
}

export interface NewsDigestResponse {
  digest: NewsDigest;
  format: NewsDigestFormat;
  text: string;
}

export interface NewsEventSummary {
  id: string;
  title: string;
  summary: string;
  event_type: string;
  status: string;
  first_seen: string;
  last_seen: string;
  coverage: {
    articles: number;
    sources: number;
    languages: number;
    first_report: string | null;
    latest_report: string | null;
  };
}

export interface NewsEventList {
  events: NewsEventSummary[];
}


export interface NewsSourceState {
  key: string;
  type: string;
  id: string;
  name: string;
  last_status: 'ok' | 'error' | 'unknown' | string;
  last_success: string | null;
  last_error: string;
  error_at: string | null;
  consecutive_failures: number;
  last_count: number;
  updated_at: string;
}

export interface NewsSourceList {
  updated_at: string;
  sources: NewsSourceState[];
}


/* Phone data: what the Android client uploaded, served by /api/v1/phone. */

export interface PhoneSyncState {
  kind: string;
  cursor: string;
  last_sync_at: string | null;
  full_sync_at: string | null;
  last_error: string;
  accepted_total: number;
}

export interface PhoneDevice {
  id: string;
  device_key: string;
  name: string;
  platform: string;
  manufacturer: string;
  model: string;
  os_version: string;
  app_version: string;
  timezone: string;
  scopes: string[];
  info: Record<string, unknown>;
  revoked: boolean;
  last_seen_at: string | null;
  last_sync_at: string | null;
  created_at: string;
  states: PhoneSyncState[];
}

export interface PhoneKindCount {
  kind: string;
  count: number;
}

export interface PhoneDeviceStatus {
  device: PhoneDevice;
  counts: PhoneKindCount[];
}

export interface PhoneSummary {
  devices: PhoneDeviceStatus[];
  counts: PhoneKindCount[];
  last_sync_at: string | null;
  stale_minutes: number | null;
  pending_commands: number;
}

export interface PhoneRangeQuery {
  deviceId?: string;
  since?: string;
  until?: string;
  limit?: number;
}

export interface PhoneCalendarEvent {
  id: string;
  device_id: string;
  device_name: string;
  calendar_name: string;
  title: string;
  description: string;
  location: string;
  starts_at: string | null;
  ends_at: string | null;
  all_day: boolean;
  timezone: string;
  recurrence: string;
  organizer: string;
  attendees: string[];
  status: string;
}

export interface PhoneCall {
  id: string;
  device_id: string;
  device_name: string;
  number: string;
  cached_name: string;
  direction: string;
  started_at: string | null;
  duration_seconds: number;
  is_new: boolean;
}

export interface PhoneMessage {
  id: string;
  device_id: string;
  device_name: string;
  address: string;
  direction: string;
  body: string;
  subject: string;
  received_at: string | null;
  is_read: boolean;
  kind: string;
}

/** One number in the SMS quick picker, counted per normalised address. */
export interface PhoneMessageSender {
  address: string;
  count: number;
  last_at: string | null;
}

export interface PhoneContactBirthday {
  month: number;
  day: number;
  year: number | null;
  type: string;
}

export interface PhoneContact {
  id: string;
  device_id: string;
  device_name: string;
  display_name: string;
  numbers: string[];
  emails: string[];
  organization: string;
  starred: boolean;
  birthdays: PhoneContactBirthday[];
}

export interface PhoneNotification {
  id: string;
  device_id: string;
  device_name: string;
  package_name: string;
  app_label: string;
  title: string;
  text: string;
  category: string;
  posted_at: string | null;
  is_ongoing: boolean;
}

export interface PhoneLocation {
  id: string;
  device_id: string;
  device_name: string;
  latitude: number;
  longitude: number;
  accuracy_m: number | null;
  provider: string;
  captured_at: string | null;
  /** Resolved server-side; empty when reverse geocoding is not configured. */
  address: string;
  province: string;
  city: string;
  district: string;
  poi: string;
}

export interface PhoneGeocodeResult {
  configured: boolean;
  geocoded: number;
  remaining: number;
}

export interface PhoneApp {
  id: string;
  device_id: string;
  device_name: string;
  package_name: string;
  label: string;
  version_name: string;
  is_system: boolean;
  is_enabled: boolean;
  installed_at: string | null;
  updated_at: string | null;
}

export interface PhoneCommand {
  id: string;
  device_id: string;
  kind: string;
  payload: Record<string, unknown>;
  summary: string;
  status: string;
  result: string;
  error: string;
  expires_at: string | null;
  approved_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface PhoneSearchHit {
  kind: string;
  at: string | null;
  title: string;
  text: string;
  device_id: string;
  device_name: string;
  record_id: string;
}

export interface PhoneSearchResponse {
  query: string;
  hits: PhoneSearchHit[];
  device_names: Record<string, string>;
}

/** The owner's weekly schedule: one Markdown file per week in a WLB tree. */
export interface ScheduleLink {
  text: string;
  target: string;
}

export interface ScheduleEvent {
  id: string;
  scope: 'day' | 'week';
  line: number;
  done: boolean;
  /** `MM/DD`, or null for a week-level item. */
  date: string | null;
  weekday: string | null;
  every: boolean;
  /** `HH:MM`, or null when the event has no time tag. */
  start: string | null;
  end: string | null;
  tags: string[];
  text: string;
  /** The indented block under the event line as Markdown, indent removed. */
  note: string;
  links: ScheduleLink[];
}

export interface ScheduleDay {
  date: string;
  label: string;
  heading: string;
  line: number;
  events: ScheduleEvent[];
}

export interface ScheduleSection {
  title: string;
  line: number;
  events: ScheduleEvent[];
}

export type ScheduleLayout = 'canonical' | 'legacy';

export interface ScheduleWeek {
  file: string;
  stem: string;
  layout: ScheduleLayout;
  editable: boolean;
  title: string;
  week: { monday: string; sunday: string; stem: string; label: string };
  days: ScheduleDay[];
  sections: ScheduleSection[];
  related: Record<string, string>;
  warnings: string[];
}

export interface ScheduleWeekPayload extends ScheduleWeek {
  root: string;
  today: string;
}

export interface ScheduleMutation {
  changed: boolean;
  message: string;
  event: ScheduleEvent | null;
  week: ScheduleWeekPayload;
  removed?: string[];
}

export interface ScheduleEventInput {
  date: string;
  text: string;
  time?: string | null;
  weekday?: boolean;
  every?: boolean;
  tags?: string[];
  done?: boolean;
  week_scope?: boolean;
  after?: string | null;
}

export interface ScheduleEventUpdate {
  week: string;
  id: string;
  text?: string;
  time?: string;
  done?: boolean;
  tags_add?: string[];
  tags_remove?: string[];
  note?: string;
  move_to?: string;
}

export interface ProactiveChannelAttempt {
  name: string;
  at?: string;
  receipt?: string;
  error?: string;
}

export interface ProactiveItem {
  id: string;
  kind: string;
  subject_key: string;
  occurrence_key: string;
  stage: string;
  priority: number;
  title: string;
  body: string;
  actions: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  due_at: string | null;
  planned_at: string;
  status: string;
  channels: ProactiveChannelAttempt[];
  acked_at: string | null;
  acked_via: string;
  feedback: string;
  created_at: string;
}

export interface ProactiveStatus {
  enabled: boolean;
  owner: string;
  channels: string[];
  counts: Record<string, number>;
  last_scan_at: string;
  last_error: string;
  latest_update: string;
}

// -- mailbox settings ---------------------------------------------------

export type MailSecurity = 'ssl' | 'starttls' | 'none';
export type MailProtocol = 'imap' | 'pop3' | 'none';
export type MailTestTarget = 'smtp' | 'receive';

export interface MailSmtpView {
  host: string;
  port: number;
  security: MailSecurity;
  username: string;
  from_address: string;
  from_name: string;
  password_set: boolean;
}

export interface MailReceiveView {
  protocol: MailProtocol;
  host: string;
  port: number;
  security: MailSecurity;
  username: string;
  use_send_credentials: boolean;
  folder: string;
  password_set: boolean;
}

export interface MailSettings {
  enabled: boolean;
  address: string;
  display_name: string;
  smtp: MailSmtpView;
  receive: MailReceiveView;
  max_messages: number;
  timeout_seconds: number;
  updated_at: string;
  can_send: boolean;
  can_receive: boolean;
}

export interface MailSmtpInput {
  host: string;
  port: number;
  security: MailSecurity;
  username: string;
  /** Never rendered; null keeps the stored secret, "" clears it. */
  password: string | null;
  from_address: string;
  from_name: string;
}

export interface MailReceiveInput {
  protocol: MailProtocol;
  host: string;
  port: number;
  security: MailSecurity;
  username: string;
  password: string | null;
  use_send_credentials: boolean;
  folder: string;
}

export interface MailSettingsInput {
  enabled: boolean;
  address: string;
  display_name: string;
  smtp: MailSmtpInput;
  receive: MailReceiveInput;
  max_messages: number;
  timeout_seconds: number;
}

export interface MailTestResult {
  ok: boolean;
  target: MailTestTarget;
  detail: string;
}

export interface MailSendResult {
  ok: boolean;
  receipt: string;
}

export interface MailStats {
  messages: number;
  unread: number;
  folders: number;
}

/** A folder as the last sync saw it, with the cursor that drives the next one. */
export interface MailFolder {
  name: string;
  display_name: string;
  delimiter: string;
  selectable: boolean;
  protocol: string;
  uid_validity: number;
  last_uid: number;
  total: number;
  unread: number;
  last_synced_at: string;
  last_error: string;
}

export interface MailFolderList {
  protocol: MailProtocol;
  folders: MailFolder[];
  stats: MailStats;
}

/** A cached message. `body` is empty until the reading pane opens it. */
export interface MailCachedMessage {
  id: string;
  folder: string;
  /** The folder's human label; ``folder`` is the raw modified-UTF-7 name. */
  folder_display: string;
  protocol: string;
  uid: string;
  subject: string;
  sender: string;
  recipients: string;
  date: string;
  snippet: string;
  unread: boolean;
  size: number;
  has_body: boolean;
  /** The sender's markup, rendered in a sandboxed frame. */
  has_html: boolean;
  /** The RFC 822 source is cached and the "Raw" tab can show it. */
  has_raw: boolean;
  message_id: string;
  body: string;
  body_html: string;
  body_raw: string;
}

export interface MailSyncResult {
  protocol: MailProtocol;
  folders: number;
  fetched: number;
  errors: { folder: string; error: string }[];
  stats: MailStats;
}
