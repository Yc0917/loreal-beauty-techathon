<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  Bot,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardList,
  Copy,
  Ellipsis,
  Gift,
  Headphones,
  Image,
  Mic,
  PackageCheck,
  Paperclip,
  Pause,
  Play,
  RotateCcw,
  Search,
  Send,
  Settings,
  ShieldAlert,
  SmilePlus,
  Sparkles,
  WandSparkles,
  X,
  Zap,
} from 'lucide-vue-next'
import {
  analyzeEmotion,
  analyzeIntent,
  createSimulationTicket,
  fetchConversation,
  fetchConversations,
  resetSimulationMessages,
  submitEmotionFeedback,
} from './api'
import type {
  ConversationSummary,
  EmotionAnalysisResult,
  EmotionLabel,
  IntentAnalysisResult,
  SimulationTicketCreatePayload,
  ServiceSession,
} from './types'

const emptySession: ServiceSession = {
  id: '',
  buyer: '加载中',
  avatar: '客',
  scene: '正在读取数据库',
  subScene: '',
  lastTime: '',
  lastMessage: '',
  messageCount: 0,
  unread: 0,
  emotion: '分析中',
  emotionLevel: 'safe',
  emotionScore: 0,
  emotionHint: '正在加载会话上下文…',
  messages: [],
  orders: [],
  ticket: { ticketId: '暂无', category: '服务', issue: '正在加载', status: '—' },
  referenceTicket: null,
  suggestions: ['正在生成推荐回复…'],
}

const conversationSummaries = ref<ConversationSummary[]>([])
const activeSessionId = ref('')
const activeSession = ref<ServiceSession>(emptySession)
const draft = ref('')
const optimizedDraft = ref('')
const copiedSuggestion = ref<number | null>(null)
const messageList = ref<HTMLElement | null>(null)
const searchQuery = ref('')
const isLoading = ref(true)
const isDetailLoading = ref(false)
const errorMessage = ref('')
const replayActive = ref(false)
const replayVisibleCount = ref(0)
const replaySpeed = ref<1 | 2 | 5>(1)
const conversationStartIndex = ref(0)
const visibleMessageCount = ref(0)
const emotionStatus = ref<'idle' | 'loading' | 'success' | 'failed'>('idle')
const emotionResult = ref<EmotionAnalysisResult | null>(null)
const emotionAnalyzedMessageCount = ref(0)
const emotionFeedbackOpen = ref(false)
const emotionFeedbackLabel = ref<EmotionLabel | ''>('')
const emotionFeedbackNote = ref('')
const emotionFeedbackStatus = ref<'idle' | 'submitting' | 'saved' | 'failed'>('idle')
const emotionFeedbackError = ref('')
const intentStatus = ref<'idle' | 'loading' | 'success' | 'failed'>('idle')
const intentResult = ref<IntentAnalysisResult | null>(null)
const ticketCreateOpen = ref(false)
const ticketCreateStatus = ref<'idle' | 'submitting' | 'failed'>('idle')
const ticketCreateError = ref('')
const ticketDraft = ref<SimulationTicketCreatePayload>({
  intent: 'reship_exchange',
  issue: '',
  assignee: '客服薇薇',
  related_order_id: null,
})
let replayTimer: number | undefined
let detailRequestId = 0
let emotionRequestId = 0
let intentRequestId = 0

const filteredConversations = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return conversationSummaries.value
  return conversationSummaries.value.filter((session) =>
    [session.id, session.buyer, session.scene, session.subScene, session.lastMessage]
      .join(' ')
      .toLowerCase()
      .includes(keyword),
  )
})

// 固定会话只使用原始聊天记录，避免历史仿真消息混入逐轮演示。
const activeMessages = computed(() =>
  activeSession.value.messages.filter((message) => !message.simulated),
)
const visibleMessages = computed(() =>
  replayActive.value
    ? activeMessages.value.slice(0, replayVisibleCount.value)
    : activeMessages.value.slice(conversationStartIndex.value, visibleMessageCount.value),
)

// 文本框始终展示当前轮次之后的第一句固定客服回复。
const nextAgentMessage = computed(() => {
  for (let index = visibleMessageCount.value; index < activeMessages.value.length; index += 1) {
    if (activeMessages.value[index].role === '客服') {
      return { index, message: activeMessages.value[index] }
    }
  }
  return null
})
const fixedAgentReply = computed(() => nextAgentMessage.value?.message.text ?? '')

const emotionClass = computed(() => `emotion-${activeSession.value.emotionLevel}`)
const emotionOptions: Array<{ value: EmotionLabel; label: string }> = [
  { value: 'neutral', label: '平稳' },
  { value: 'anxious', label: '焦虑' },
  { value: 'dissatisfied', label: '不满' },
  { value: 'angry', label: '愤怒' },
]
// 当前会话只要已经关联工单，就不再调用工单意图模型。
const hasExistingTicket = computed(() => (
  Boolean(activeSession.value.ticket.ticketId)
  && activeSession.value.ticket.ticketId !== '暂无'
))
// 将后端的完整说明归一为紧凑的四类情绪展示。
const compactEmotion = computed(() => {
  if (emotionStatus.value === 'loading') return { emoji: '⏳', label: '识别中' }
  if (emotionStatus.value === 'failed') return { emoji: '⚠️', label: '识别失败' }
  const emotion = activeSession.value.emotion
  if (emotion === 'angry' || emotion.includes('愤怒')) return { emoji: '😠', label: '愤怒' }
  if (emotion === 'dissatisfied' || emotion.includes('不满')) return { emoji: '😕', label: '不满' }
  if (emotion === 'anxious' || emotion.includes('焦虑') || emotion.includes('着急')) {
    return { emoji: '😟', label: '焦虑' }
  }
  return { emoji: '🙂', label: '平稳' }
})

// 五类标签同时供识别结果和人工确认表单使用，避免两处文案不一致。
const ticketIntentOptions = [
  { value: 'reship_exchange', label: '补发换货' },
  { value: 'offline_payment', label: '线下打款' },
  { value: 'logistics_ticket', label: '物流' },
  { value: 'adverse_reaction', label: '不良反应' },
  { value: 'after_sales_return', label: '售后退货' },
] as const
const ticketIntentLabels = Object.fromEntries(
  ticketIntentOptions.map((option) => [option.value, option.label]),
) as Record<SimulationTicketCreatePayload['intent'], string>

// 将五类工单意图标签转换为客服可快速阅读的中文展示。
const compactIntent = computed(() => {
  if (hasExistingTicket.value) {
    const category = activeSession.value.ticket.category.trim()
    const ticketName = category.endsWith('工单') ? category : `${category}工单`
    return {
      emoji: '📋',
      label: `已有${ticketName}`,
      status: activeSession.value.ticket.status,
    }
  }
  if (intentStatus.value === 'loading') return { emoji: '⏳', label: '识别中', status: '' }
  if (intentStatus.value === 'failed') return { emoji: '⚠️', label: '识别失败', status: '' }
  if (!intentResult.value) return { emoji: '🎯', label: '待识别', status: '可创建' }

  return {
    emoji: '📋',
    label: ticketIntentLabels[intentResult.value.intent],
    status: `${Math.round(intentResult.value.confidence * 100)}%`,
  }
})

// Ground truth 仅在 Mock 工单创建后展示，避免在识别前泄露历史结果。
const ticketEvaluation = computed(() => {
  if (!hasExistingTicket.value || !activeSession.value.referenceTicket) return null
  const reference = activeSession.value.referenceTicket
  return {
    matched: activeSession.value.ticket.category === reference.category,
    referenceCategory: reference.category,
    createdAt: reference.createdAt,
  }
})

function resetTicketCreate() {
  ticketCreateOpen.value = false
  ticketCreateStatus.value = 'idle'
  ticketCreateError.value = ''
}

function openTicketCreate() {
  if (!intentResult.value || hasExistingTicket.value) return
  ticketDraft.value = {
    intent: intentResult.value.intent,
    issue: intentResult.value.summary,
    assignee: '客服薇薇',
    related_order_id: activeSession.value.orders[0]?.orderId ?? null,
  }
  ticketCreateStatus.value = 'idle'
  ticketCreateError.value = ''
  ticketCreateOpen.value = true
}

async function submitTicketCreate() {
  const sessionId = activeSession.value.id
  if (!sessionId || hasExistingTicket.value || !ticketDraft.value.issue.trim()) return

  ticketCreateStatus.value = 'submitting'
  ticketCreateError.value = ''
  try {
    const ticket = await createSimulationTicket(sessionId, {
      ...ticketDraft.value,
      issue: ticketDraft.value.issue.trim(),
      assignee: ticketDraft.value.assignee.trim(),
    })
    if (sessionId !== activeSession.value.id) return
    activeSession.value = { ...activeSession.value, ticket }
    ticketCreateOpen.value = false
    ticketCreateStatus.value = 'idle'
  } catch (error) {
    ticketCreateStatus.value = 'failed'
    ticketCreateError.value = error instanceof Error ? error.message : '工单创建失败'
  }
}

function resetEmotionFeedback() {
  emotionFeedbackOpen.value = false
  emotionFeedbackLabel.value = ''
  emotionFeedbackNote.value = ''
  emotionFeedbackStatus.value = 'idle'
  emotionFeedbackError.value = ''
}

function openEmotionFeedback() {
  if (!emotionResult.value || emotionFeedbackStatus.value === 'saved') return
  emotionFeedbackLabel.value = ''
  emotionFeedbackNote.value = ''
  emotionFeedbackError.value = ''
  emotionFeedbackOpen.value = true
}

async function saveEmotionFeedback() {
  const prediction = emotionResult.value
  const correctedEmotion = emotionFeedbackLabel.value
  const sessionId = activeSession.value.id
  const messageCount = emotionAnalyzedMessageCount.value
  if (!prediction || !correctedEmotion || !sessionId || !messageCount) return

  const messages = activeMessages.value
    .slice(0, messageCount)
    .map(({ role, text }) => ({ role, text }))
  emotionFeedbackStatus.value = 'submitting'
  emotionFeedbackError.value = ''
  try {
    await submitEmotionFeedback({
      session_id: sessionId,
      scene: activeSession.value.scene,
      message_count: messages.length,
      messages,
      prediction,
      corrected_emotion: correctedEmotion,
      feedback_note: emotionFeedbackNote.value.trim(),
    })
    if (sessionId !== activeSession.value.id) return

    const levelMap = {
      neutral: 'safe',
      anxious: 'watch',
      dissatisfied: 'watch',
      angry: 'risk',
    } as const
    // 当前界面展示客服纠正值，原模型结果仍完整保留在后端反馈记录中。
    activeSession.value = {
      ...activeSession.value,
      emotion: correctedEmotion,
      emotionLevel: levelMap[correctedEmotion],
    }
    emotionFeedbackStatus.value = 'saved'
    emotionFeedbackOpen.value = false
  } catch (error) {
    emotionFeedbackStatus.value = 'failed'
    emotionFeedbackError.value = error instanceof Error ? error.message : '反馈提交失败'
  }
}

async function analyzeCurrentEmotion(messageCount = visibleMessageCount.value) {
  const sessionId = activeSession.value.id
  const messages = activeMessages.value
    .slice(0, messageCount)
    .map(({ role, text }) => ({ role, text }))
  if (!sessionId || !messages.some((message) => message.role === '买家')) return

  const requestId = ++emotionRequestId
  emotionStatus.value = 'loading'
  emotionResult.value = null
  emotionAnalyzedMessageCount.value = 0
  resetEmotionFeedback()
  try {
    const result = await analyzeEmotion(activeSession.value.scene, messages)
    if (requestId !== emotionRequestId || sessionId !== activeSession.value.id) return

    const levelMap = {
      neutral: 'safe',
      anxious: 'watch',
      dissatisfied: 'watch',
      angry: 'risk',
    } as const
    activeSession.value = {
      ...activeSession.value,
      emotion: result.emotion,
      emotionLevel: levelMap[result.emotion],
      emotionScore: Math.round(result.confidence * 100),
      emotionHint: result.summary,
    }
    emotionResult.value = result
    emotionAnalyzedMessageCount.value = messageCount
    emotionStatus.value = 'success'
  } catch {
    if (requestId !== emotionRequestId || sessionId !== activeSession.value.id) return
    emotionStatus.value = 'failed'
  }
}

async function analyzeCurrentTicketIntent() {
  // 已有工单时直接使用业务数据展示，避免重复识别或重复建单提示。
  if (hasExistingTicket.value) return

  const sessionId = activeSession.value.id
  const messages = activeMessages.value
    .slice(0, visibleMessageCount.value)
    .map(({ role, text }) => ({ role, text }))
  if (!sessionId || !messages.some((message) => message.role === '买家')) return

  const requestId = ++intentRequestId
  intentStatus.value = 'loading'
  try {
    const result = await analyzeIntent(messages)
    if (requestId !== intentRequestId || sessionId !== activeSession.value.id) return
    intentResult.value = result
    intentStatus.value = 'success'
  } catch {
    if (requestId !== intentRequestId || sessionId !== activeSession.value.id) return
    intentResult.value = null
    intentStatus.value = 'failed'
  }
}

function scrollToLatest() {
  nextTick(() => {
    if (messageList.value) {
      messageList.value.scrollTop = messageList.value.scrollHeight
    }
  })
}

function stopReplay() {
  replayActive.value = false
  if (replayTimer !== undefined) {
    window.clearInterval(replayTimer)
    replayTimer = undefined
  }
}

function toggleReplay() {
  if (replayActive.value) {
    stopReplay()
    return
  }
  if (!activeMessages.value.length) return

  replayVisibleCount.value = 0
  replayActive.value = true
  replayTimer = window.setInterval(() => {
    replayVisibleCount.value = Math.min(
      replayVisibleCount.value + replaySpeed.value,
      activeMessages.value.length,
    )
    scrollToLatest()
    if (replayVisibleCount.value >= activeMessages.value.length) stopReplay()
  }, 700)
}

function cycleReplaySpeed() {
  replaySpeed.value = replaySpeed.value === 1 ? 2 : replaySpeed.value === 2 ? 5 : 1
}

async function selectSession(sessionId: string) {
  if (!sessionId) return
  const requestId = ++detailRequestId
  emotionRequestId += 1
  intentRequestId += 1
  stopReplay()
  activeSessionId.value = sessionId
  draft.value = ''
  optimizedDraft.value = ''
  isDetailLoading.value = true
  emotionStatus.value = 'idle'
  emotionResult.value = null
  emotionAnalyzedMessageCount.value = 0
  resetEmotionFeedback()
  intentStatus.value = 'idle'
  intentResult.value = null
  resetTicketCreate()
  errorMessage.value = ''
  try {
    const detail = await fetchConversation(sessionId)
    if (requestId !== detailRequestId) return
    activeSession.value = detail
    // 进入会话时从第一条买家消息开始，不提前展示后续固定对话。
    const firstBuyerIndex = activeMessages.value.findIndex((message) => message.role === '买家')
    conversationStartIndex.value = firstBuyerIndex >= 0 ? firstBuyerIndex : 0
    visibleMessageCount.value = firstBuyerIndex >= 0 ? firstBuyerIndex + 1 : 0
    scrollToLatest()
    void analyzeCurrentEmotion()
  } catch (error) {
    if (requestId === detailRequestId) {
      errorMessage.value = error instanceof Error ? error.message : '会话加载失败'
    }
  } finally {
    if (requestId === detailRequestId) isDetailLoading.value = false
  }
}

function useSuggestion(text: string) {
  draft.value = text
  optimizedDraft.value = ''
}

function optimizeReply() {
  const source = draft.value.trim()
  if (!source) {
    optimizedDraft.value = activeSession.value.suggestions[0]
    return
  }

  // 首版使用可预测的本地规则模拟措辞优化，后续替换为自建后端接口。
  const cleaned = source.replace(/亲亲+/g, '亲').replace(/[！!]{2,}/g, '！')
  optimizedDraft.value = `理解您的感受，${cleaned.replace(/^理解您的感受[，,]?/, '')} 我们会持续为您跟进，请您放心。`
}

function applyOptimizedReply() {
  if (!optimizedDraft.value) return
  draft.value = optimizedDraft.value
  optimizedDraft.value = ''
}

function sendMessage() {
  const nextReply = nextAgentMessage.value
  if (!nextReply || !activeSession.value.id) return

  stopReplay()
  let nextVisibleCount = nextReply.index + 1

  // 展示本轮客服回复，并继续展示其后的买家消息，停在下一句客服回复之前。
  while (
    nextVisibleCount < activeMessages.value.length
    && activeMessages.value[nextVisibleCount].role !== '客服'
  ) {
    nextVisibleCount += 1
  }

  visibleMessageCount.value = nextVisibleCount
  optimizedDraft.value = ''
  scrollToLatest()
  void analyzeCurrentEmotion(nextVisibleCount)
}

async function resetSimulation() {
  if (!window.confirm('确定清除全部 Mock 工单和仿真回复吗？原始业务数据不会受影响。')) return
  try {
    await resetSimulationMessages()
    await loadConversations(activeSessionId.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '仿真数据重置失败'
  }
}

async function copySuggestion(text: string, index: number) {
  try {
    await navigator.clipboard.writeText(text)
    copiedSuggestion.value = index
    window.setTimeout(() => (copiedSuggestion.value = null), 1200)
  } catch {
    useSuggestion(text)
  }
}

async function loadConversations(preferredSessionId = '') {
  isLoading.value = true
  errorMessage.value = ''
  try {
    conversationSummaries.value = await fetchConversations()
    const nextSessionId =
      preferredSessionId && conversationSummaries.value.some((item) => item.id === preferredSessionId)
        ? preferredSessionId
        : conversationSummaries.value[0]?.id
    if (nextSessionId) await selectSession(nextSessionId)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据库连接失败'
  } finally {
    isLoading.value = false
  }
}

onMounted(loadConversations)
onBeforeUnmount(stopReplay)
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-block">
        <div class="brand-mark"><Sparkles :size="18" /></div>
        <div>
          <strong>数据共情者</strong>
          <span>BEAUTY CARE DESK</span>
        </div>
      </div>

      <div class="store-switcher">
        <span class="store-dot"></span>
        测试美妆旗舰店
        <ChevronDown :size="14" />
      </div>

      <div class="topbar-actions">
        <div class="online-chip"><span></span>客服在线</div>
        <button class="icon-button" aria-label="帮助"><CircleHelp :size="18" /></button>
        <button class="icon-button" aria-label="设置"><Settings :size="18" /></button>
        <div class="agent-avatar">薇</div>
      </div>
    </header>

    <div v-if="errorMessage" class="error-banner">
      <span>{{ errorMessage }}</span>
      <button @click="errorMessage = ''"><X :size="14" />关闭</button>
    </div>

    <main class="workspace">
      <aside class="session-panel">
        <div class="panel-title-row">
          <div>
            <span class="eyebrow">CUSTOMER QUEUE</span>
            <h1>客户接待</h1>
          </div>
          <span class="count-badge">{{ filteredConversations.length }}</span>
        </div>

        <label class="search-box">
          <Search :size="16" />
          <input
            v-model="searchQuery"
            type="search"
            placeholder="搜索买家、会话或场景"
            aria-label="搜索买家、会话或场景"
          />
          <kbd>⌘ K</kbd>
        </label>

        <div class="queue-tabs">
          <button class="active">全部会话 <span>{{ conversationSummaries.length }}</span></button>
          <button>排队中 <span>0</span></button>
          <button>已结束</button>
        </div>

        <div class="session-list">
          <div v-if="isLoading" class="list-state">正在读取数据库…</div>
          <div v-else-if="!filteredConversations.length" class="list-state">未找到匹配会话</div>
          <button
            v-for="session in filteredConversations"
            :key="session.id"
            class="session-card"
            :class="{ active: session.id === activeSessionId }"
            @click="selectSession(session.id)"
          >
            <div class="buyer-avatar" :class="`avatar-${session.emotionLevel}`">{{ session.avatar }}</div>
            <div class="session-main">
              <div class="session-line">
                <strong>{{ session.buyer }}</strong>
                <time>{{ session.lastTime }}</time>
              </div>
              <p>{{ session.lastMessage }}</p>
              <div class="session-meta">
                <span>{{ session.scene }}</span>
                <span class="session-id">{{ session.id }}</span>
              </div>
            </div>
            <span v-if="session.unread" class="unread-badge">{{ session.unread }}</span>
          </button>
        </div>

        <div class="queue-footer">
          <Headphones :size="16" />
          <span>数据库会话 <strong>{{ conversationSummaries.length }}</strong> 个</span>
          <button class="reset-button" title="重置本地演示数据" @click="resetSimulation">
            <RotateCcw :size="13" />重置
          </button>
        </div>
      </aside>

      <section class="chat-panel">
        <header class="chat-header">
          <div class="chat-customer">
            <div class="buyer-avatar avatar-watch">{{ activeSession.avatar }}</div>
            <div>
              <div class="customer-name-line">
                <strong>{{ activeSession.buyer }}</strong>
                <span class="platform-chip">平台会员</span>
              </div>
              <p>{{ activeSession.scene }} · {{ activeSession.subScene }}</p>
            </div>
          </div>
          <div class="chat-header-actions">
            <button class="replay-speed" title="切换回放速度" @click="cycleReplaySpeed">
              {{ replaySpeed }}×
            </button>
            <button class="replay-button" @click="toggleReplay">
              <Pause v-if="replayActive" :size="13" />
              <Play v-else :size="13" />
              {{ replayActive ? '停止' : '回放' }}
            </button>
            <span class="response-status"><Zap :size="13" /> 平均响应 18s</span>
            <button class="icon-button" aria-label="更多操作"><Ellipsis :size="19" /></button>
          </div>
        </header>

        <div ref="messageList" class="message-list" :class="{ 'is-loading': isDetailLoading }">
          <div class="date-divider"><span>今天</span></div>
          <div v-if="isDetailLoading" class="detail-loading">正在加载会话记录…</div>
          <div
            v-for="message in visibleMessages"
            :key="message.id"
            class="message-row"
            :class="{
              'is-agent': message.role === '客服',
              'is-system': message.role === '系统推送',
            }"
          >
            <template v-if="message.role === '系统推送'">
              <div class="system-message">{{ message.text }}</div>
            </template>
            <template v-else>
              <div class="message-avatar">{{ message.role === '客服' ? '薇' : activeSession.avatar }}</div>
              <div class="message-content">
                <div class="message-author">
                  <span>{{ message.role === '客服' ? '客服薇薇' : activeSession.buyer }}</span>
                  <time>{{ message.time }}</time>
                  <em v-if="message.simulated">仿真</em>
                </div>
                <div class="message-bubble">{{ message.text }}</div>
              </div>
            </template>
          </div>
        </div>

        <footer class="composer">
          <div v-if="optimizedDraft" class="optimized-banner">
            <div><WandSparkles :size="16" /><span>{{ optimizedDraft }}</span></div>
            <div class="optimized-actions">
              <button @click="optimizedDraft = ''"><X :size="15" />忽略</button>
              <button class="apply-button" @click="applyOptimizedReply"><Check :size="15" />使用优化</button>
            </div>
          </div>
          <div class="composer-toolbar">
            <button aria-label="表情"><SmilePlus :size="19" /></button>
            <button aria-label="图片"><Image :size="19" /></button>
            <button aria-label="附件"><Paperclip :size="19" /></button>
            <button aria-label="语音"><Mic :size="19" /></button>
            <span></span>
            <button class="optimize-shortcut" @click="optimizeReply"><Sparkles :size="16" />优化表达</button>
          </div>
          <textarea
            :value="fixedAgentReply"
            :placeholder="nextAgentMessage ? '' : '当前会话已结束'"
            aria-label="下一句固定客服回复"
            readonly
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>
          <div class="composer-footer">
            <span>{{ fixedAgentReply.length }}/500</span>
            <button class="send-button" :disabled="!nextAgentMessage" @click="sendMessage">
              {{ nextAgentMessage ? '发送' : '会话结束' }} <Send :size="16" />
            </button>
          </div>
        </footer>
      </section>

      <aside class="assistant-panel">
        <header class="assistant-header">
          <div>
            <span class="assistant-icon"><Bot :size="19" /></span>
            <div>
              <span class="eyebrow">EMPATHY COPILOT</span>
              <h2>共情助手</h2>
            </div>
          </div>
          <span class="live-indicator"><i></i>实时分析</span>
        </header>

        <div class="assistant-scroll">
          <section class="assistant-section emotion-section">
            <div :class="['emotion-compact', emotionClass]">
              <span class="emotion-emoji" aria-hidden="true">{{ compactEmotion.emoji }}</span>
              <div class="emotion-copy">
                <small>情绪识别</small>
                <span>{{ compactEmotion.label }}</span>
              </div>
              <span v-if="emotionFeedbackStatus === 'saved'" class="emotion-feedback-saved">
                已反馈
              </span>
              <button
                v-else-if="emotionStatus === 'success' && emotionResult"
                class="emotion-feedback-trigger"
                type="button"
                @click="openEmotionFeedback"
              >
                纠正
              </button>
            </div>
            <form
              v-if="emotionFeedbackOpen"
              class="emotion-feedback-form"
              @submit.prevent="saveEmotionFeedback"
            >
              <label>
                <span>正确情绪</span>
                <select v-model="emotionFeedbackLabel" required>
                  <option value="" disabled>请选择</option>
                  <option
                    v-for="option in emotionOptions"
                    :key="option.value"
                    :value="option.value"
                    :disabled="option.value === emotionResult?.emotion"
                  >
                    {{ option.label }}{{ option.value === emotionResult?.emotion ? '（原结果）' : '' }}
                  </option>
                </select>
              </label>
              <label>
                <span>备注（选填）</span>
                <textarea
                  v-model="emotionFeedbackNote"
                  maxlength="300"
                  placeholder="例如：用户只是追问进度，没有明显负面表达"
                ></textarea>
              </label>
              <p v-if="emotionFeedbackError" class="emotion-feedback-error">
                {{ emotionFeedbackError }}
              </p>
              <div class="emotion-feedback-actions">
                <button type="button" @click="emotionFeedbackOpen = false">取消</button>
                <button
                  class="emotion-feedback-submit"
                  type="submit"
                  :disabled="!emotionFeedbackLabel || emotionFeedbackStatus === 'submitting'"
                >
                  {{ emotionFeedbackStatus === 'submitting' ? '提交中…' : '提交反馈' }}
                </button>
              </div>
            </form>
          </section>

          <!-- 工单意图由客服主动触发，不随买家消息自动调用模型。 -->
          <section class="assistant-section intent-section">
            <div class="intent-compact">
              <span class="intent-emoji" aria-hidden="true">{{ compactIntent.emoji }}</span>
              <div>
                <small>工单意图</small>
                <span>{{ compactIntent.label }}</span>
              </div>
              <span v-if="hasExistingTicket" class="intent-status">
                {{ compactIntent.status }}
              </span>
              <span v-else-if="intentStatus === 'loading'" class="intent-status">识别中</span>
              <span v-else-if="intentStatus === 'success'" class="intent-actions">
                <span class="intent-status">{{ compactIntent.status }}</span>
                <button class="intent-action" type="button" @click="openTicketCreate">
                  确认建单
                </button>
              </span>
              <button
                v-else
                class="intent-action"
                type="button"
                @click="analyzeCurrentTicketIntent"
              >
                {{ intentStatus === 'failed' ? '重新识别' : '开始识别' }}
              </button>
            </div>
          </section>

          <section class="assistant-section">
            <div class="section-heading">
              <div><PackageCheck :size="17" /><h3>购买与售后</h3></div>
              <button>查看全部</button>
            </div>
            <article v-for="order in activeSession.orders" :key="order.orderId" class="order-card">
              <div class="product-thumb"><Gift v-if="order.gift" :size="22" /><PackageCheck v-else :size="22" /></div>
              <div class="order-info">
                <strong>{{ order.productName }}</strong>
                <span>×{{ order.quantity }} · 实付 ¥{{ order.paidAmount }}</span>
                <small>{{ order.status }} · {{ order.carrier }}</small>
              </div>
              <span class="order-status">{{ order.status }}</span>
            </article>
            <p v-if="!activeSession.orders.length" class="empty-context">当前会话暂无关联订单</p>
            <article v-if="hasExistingTicket" class="ticket-card">
              <div class="ticket-icon"><ClipboardList :size="18" /></div>
              <div>
                <span>{{ activeSession.ticket.category }}工单</span>
                <strong>{{ activeSession.ticket.issue }}</strong>
                <small>{{ activeSession.ticket.ticketId }}</small>
              </div>
              <span>{{ activeSession.ticket.status }}</span>
            </article>
            <div v-else class="ticket-empty-state">
              <ClipboardList :size="17" />
              <div>
                <strong>当前暂无工单</strong>
                <span>完成意图识别后可创建本地 Mock 工单</span>
              </div>
            </div>
            <div
              v-if="ticketEvaluation"
              class="ticket-evaluation"
              :class="ticketEvaluation.matched ? 'is-matched' : 'is-mismatched'"
            >
              <strong>{{ ticketEvaluation.matched ? '与 Ground truth 一致' : '与 Ground truth 不一致' }}</strong>
              <span>
                历史工单：{{ ticketEvaluation.referenceCategory }}工单 ·
                {{ ticketEvaluation.createdAt }} 创建
              </span>
            </div>
          </section>

          <section class="assistant-section suggestion-section">
            <div class="section-heading">
              <div><Sparkles :size="17" /><h3>推荐回复</h3></div>
              <span class="generated-label">已生成 {{ activeSession.suggestions.length }} 条</span>
            </div>
            <article
              v-for="(suggestion, index) in activeSession.suggestions"
              :key="suggestion"
              class="suggestion-card"
            >
              <div class="suggestion-number">0{{ index + 1 }}</div>
              <p>{{ suggestion }}</p>
              <div class="suggestion-actions">
                <button @click="copySuggestion(suggestion, index)">
                  <Check v-if="copiedSuggestion === index" :size="14" />
                  <Copy v-else :size="14" />
                </button>
                <button @click="useSuggestion(suggestion)">填入输入框</button>
              </div>
            </article>
          </section>

          <section class="assistant-section polish-section">
            <div class="section-heading">
              <div><WandSparkles :size="17" /><h3>话术优化</h3></div>
            </div>
            <p>输入框已有内容时，可优化为更清晰、更有共情感的表达。</p>
            <button class="polish-button" @click="optimizeReply">
              <WandSparkles :size="16" />优化当前回复
            </button>
          </section>

          <div class="safety-note">
            <ShieldAlert :size="17" />
            <p><strong>服务提示</strong><span>涉及不良反应、退款及打款操作时，请人工确认后再发送。</span></p>
          </div>
        </div>
      </aside>
    </main>

    <div
      v-if="ticketCreateOpen"
      class="ticket-create-overlay"
      role="presentation"
      @click.self="resetTicketCreate"
    >
      <form class="ticket-create-dialog" @submit.prevent="submitTicketCreate">
        <header>
          <div>
            <small>LOCAL MOCK</small>
            <h2>确认创建工单</h2>
            <p>{{ activeSession.buyer }} · {{ activeSession.id }}</p>
          </div>
          <button type="button" aria-label="关闭建单窗口" @click="resetTicketCreate">
            <X :size="17" />
          </button>
        </header>

        <div class="ticket-create-fields">
          <label>
            <span>工单类型</span>
            <select v-model="ticketDraft.intent" required>
              <option
                v-for="option in ticketIntentOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}工单
              </option>
            </select>
          </label>
          <label>
            <span>关联订单</span>
            <select v-model="ticketDraft.related_order_id">
              <option :value="null">不关联订单</option>
              <option
                v-for="order in activeSession.orders"
                :key="order.orderId"
                :value="order.orderId"
              >
                {{ order.orderId }} · {{ order.productName }}
              </option>
            </select>
          </label>
          <label class="ticket-create-wide">
            <span>问题描述</span>
            <textarea
              v-model="ticketDraft.issue"
              maxlength="300"
              required
              placeholder="请确认或修改 Agent 生成的问题描述"
            ></textarea>
          </label>
          <label class="ticket-create-wide">
            <span>处理人</span>
            <input v-model="ticketDraft.assignee" maxlength="100" required />
          </label>
        </div>

        <p class="ticket-create-note">仅写入本地 Mock 数据，不会提交到真实千牛。</p>
        <p v-if="ticketCreateError" class="ticket-create-error">{{ ticketCreateError }}</p>
        <footer>
          <button type="button" @click="resetTicketCreate">取消</button>
          <button
            class="ticket-create-submit"
            type="submit"
            :disabled="
              ticketCreateStatus === 'submitting'
              || !ticketDraft.issue.trim()
              || !ticketDraft.assignee.trim()
            "
          >
            {{ ticketCreateStatus === 'submitting' ? '创建中…' : '确认创建' }}
          </button>
        </footer>
      </form>
    </div>
  </div>
</template>
