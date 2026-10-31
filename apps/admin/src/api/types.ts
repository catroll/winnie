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
