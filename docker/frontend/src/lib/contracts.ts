export interface Citation {
  id: number; doc_id: string; doc_version: string | null; index_version: string
  doc_name: string; page: number; page_label: string | null; chunk_id: string
  snippet: string; original_length: number; truncated: boolean; bbox: number[] | null
  regions?: number[][]; geometry?: { width: number; height: number; rotation: number; cropbox: number[]; transformation_matrix: number[]; coordinate_space: string }
  table?: { rows: (string | null)[][]; headers: string[] } | null
  source_verified: boolean; included_in_prompt: boolean; unavailable_reason?: string; score?: number | null
}
export type Outcome = 'answered' | 'chat' | 'no_evidence' | 'citation_failed' | 'cancelled' | 'error'
export type MessageStatus = 'pending' | 'complete' | 'error' | 'cancelled'
export interface Usage {
  retrieval_ms: number | null; first_token_ms: number | null; generation_ms: number | null
  input_tokens: number | null; output_tokens: number | null
}
export interface Message {
  id: string; session_id: string; role: 'user' | 'assistant'; content: string
  status: MessageStatus; outcome?: Outcome; query?: string; request_id?: string
  parent_id?: string; replaces_id?: string | null; active: boolean; error?: string
  citations: Citation[]; candidates: Citation[]; usage?: Usage
  citation_integrity?: 'passed' | 'not_applicable'; stream_delta_count?: number
  vote?: number; reason?: string; created_at: string; draft?: string; phase?: string
}
export interface Session {
  id: string; title: string; owner_id: string; workspace_id: string
  created_at: string; updated_at: string; messages?: Message[]
}
export interface RetrievalScope {
  kb_ids?: string[] | null; doc_ids?: string[] | null; tags?: string[]
  time_field?: 'published_on' | 'created_at'; from_date?: string | null; to_date?: string | null
}
export interface KnowledgeBase { id:string; name:string; document_count:number; ready_count:number; processing:boolean; mode:'single' }
export interface KnowledgePdf { id:string; name:string; pages:number; doc_version:string; index_version:string|null; state:string; error:string|null; original_available:boolean; participates_in_rag:boolean }
interface EventBase {
  protocol: 2; request_id: string; session_id: string; message_id: string; seq: number
}
export type StreamEvent = EventBase & (
  { type: 'status'; phase: string; reset_draft?: boolean; user_message?: Message; assistant_message?: Message } |
  { type: 'delta'; text: string } |
  { type: 'citations'; citations: Citation[]; candidates: Citation[] } |
  { type: 'final'; content: string; outcome: Outcome; citations: Citation[]; candidates: Citation[]; usage: Usage; citation_integrity: 'passed' | 'not_applicable'; stream_delta_count?: number } |
  { type: 'done'; outcome: Outcome } |
  { type: 'error'; message: string; outcome: 'error' }
)
