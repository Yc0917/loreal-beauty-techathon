export type MessageRole = '买家' | '客服' | '系统推送'
export type EmotionLabel = 'neutral' | 'anxious' | 'dissatisfied' | 'angry'
export type EmotionTrend = 'improving' | 'stable' | 'worsening' | 'unknown'
export type IntentLabel =
  | 'reship_exchange'
  | 'offline_payment'
  | 'logistics_ticket'
  | 'adverse_reaction'
  | 'after_sales_return'

export interface ChatMessage {
  id: string
  role: MessageRole
  text: string
  time: string
  simulated?: boolean
}

export interface EmotionAnalysisResult {
  emotion: EmotionLabel
  confidence: number
  confidence_source: 'token_logprob'
  emotion_token_logprobs: Array<{
    token: string
    logprob: number
    probability: number
  }>
  trend: EmotionTrend
  evidence: string[]
  summary: string
  prompt_version: string
  attempts: number
}

export interface IntentEvidence {
  role: '买家' | '客服'
  text: string
}

export interface IntentAnalysisResult {
  intent: IntentLabel
  need_ticket: boolean
  confidence: number
  evidence: IntentEvidence[]
  summary: string
  prompt_version: string
  attempts: number
}

export interface ConversationAnalysisResult {
  emotion: EmotionAnalysisResult | null
  intent: IntentAnalysisResult | null
  emotion_error: string | null
  intent_error: string | null
}

export interface ProductOrder {
  orderId: string
  productName: string
  quantity: number
  paidAmount: number
  status: string
  carrier: string
  trackingNo: string
  gift?: string
}

export interface TicketInfo {
  ticketId: string
  category: string
  issue: string
  status: string
}

export interface ConversationSummary {
  id: string
  buyer: string
  avatar: string
  scene: string
  subScene: string
  lastTime: string
  lastMessage: string
  messageCount: number
  unread: number
  emotionLevel: 'safe' | 'watch' | 'risk'
}

export interface ServiceSession extends ConversationSummary {
  emotion: string
  emotionScore: number
  emotionHint: string
  messages: ChatMessage[]
  orders: ProductOrder[]
  ticket: TicketInfo
  suggestions: string[]
}
