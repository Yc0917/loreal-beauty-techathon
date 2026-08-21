import type {
  ChatMessage,
  ConversationAnalysisResult,
  ConversationSummary,
  EmotionAnalysisResult,
  EmotionFeedbackPayload,
  EmotionFeedbackResult,
  IntentAnalysisResult,
  ServiceSession,
  SimulationTicketCreatePayload,
  TicketInfo,
} from './types'

const API_PREFIX = '/api'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_PREFIX}${path}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail ?? `请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function fetchConversations(): Promise<ConversationSummary[]> {
  const payload = await request<{ items: ConversationSummary[]; total: number }>('/conversations')
  return payload.items
}

export function fetchConversation(sessionId: string): Promise<ServiceSession> {
  return request<ServiceSession>(`/conversations/${encodeURIComponent(sessionId)}`)
}

export function analyzeEmotion(
  scene: string,
  messages: Pick<ChatMessage, 'role' | 'text'>[],
): Promise<EmotionAnalysisResult> {
  return request<EmotionAnalysisResult>('/emotion/analyze', {
    method: 'POST',
    body: JSON.stringify({ scene, messages }),
  })
}

export function submitEmotionFeedback(
  payload: EmotionFeedbackPayload,
): Promise<EmotionFeedbackResult> {
  return request<EmotionFeedbackResult>('/emotion/feedback', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function analyzeIntent(
  messages: Pick<ChatMessage, 'role' | 'text'>[],
): Promise<IntentAnalysisResult> {
  return request<IntentAnalysisResult>('/intent/analyze', {
    method: 'POST',
    body: JSON.stringify({ messages }),
  })
}

export function createSimulationTicket(
  sessionId: string,
  payload: SimulationTicketCreatePayload,
): Promise<TicketInfo> {
  return request<TicketInfo>(`/conversations/${encodeURIComponent(sessionId)}/tickets`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function analyzeConversation(
  scene: string,
  messages: Pick<ChatMessage, 'role' | 'text'>[],
): Promise<ConversationAnalysisResult> {
  return request<ConversationAnalysisResult>('/analysis/analyze', {
    method: 'POST',
    body: JSON.stringify({ scene, messages }),
  })
}

export function createSimulationMessage(sessionId: string, text: string): Promise<ChatMessage> {
  return request<ChatMessage>(`/conversations/${encodeURIComponent(sessionId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ text, role: '客服' }),
  })
}

export function resetSimulationMessages(): Promise<{ messages: number; tickets: number }> {
  return request<{ messages: number; tickets: number }>('/simulation', { method: 'DELETE' })
}
