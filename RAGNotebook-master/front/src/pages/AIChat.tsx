import { useState, useRef, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Send, Sparkles, Bot, User, ChevronDown, ChevronRight, Loader2, FileText, BookOpen } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import { useSSE } from '../hooks/useSSE'
import { sessionsApi } from '../api/sessions'
import { notesApi } from '../api/notes'
import { useThemeStore } from '../stores/useThemeStore'
import type { SourceItem, SuggestionData } from '../types/api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  thinking?: string
  steps?: string[]
  sources?: SourceItem[]
  suggestion?: SuggestionData
  savedAt?: number
}

const quickQuestions = [
  '帮我解释一下量子计算',
  '写一首关于春天的诗',
  '推荐几本提升思维的书',
]

export default function AIChat() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const theme = useThemeStore((s) => s.theme)
  const { start, loading } = useSSE()
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState<Message[]>([])
  const [currentThinking, setCurrentThinking] = useState('')
  const [currentSteps, setCurrentSteps] = useState<string[]>([])
  const [showThinking, setShowThinking] = useState(true)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef('')
  const rafRef = useRef<number | null>(null)

  const flushContent = useCallback(() => {
    setMessages((prev) => {
      const newMsgs = [...prev]
      const last = newMsgs[newMsgs.length - 1]
      if (last?.role === 'assistant') {
        newMsgs[newMsgs.length - 1] = { ...last, content: contentRef.current }
      } else {
        newMsgs.push({ role: 'assistant', content: contentRef.current })
      }
      return newMsgs
    })
  }, [])

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
        rafRef.current = null
      }
    }
  }, [])

  useEffect(() => {
    if (sessionId) {
      setLoadingHistory(true)
      sessionsApi.get(sessionId).then((res) => {
        const data = res.data as {
          history?: [string, string][]
          assistant_sources?: (SourceItem[] | null)[]
        } | undefined
        if (data?.history) {
          const sources = data.assistant_sources || []
          let assistantIndex = -1
          setMessages(data.history.flatMap(([query, response]) => {
            assistantIndex += 1
            return [
              { role: 'user' as const, content: query },
              {
                role: 'assistant' as const,
                content: response,
                sources: sources[assistantIndex] || undefined,
              },
            ]
          }))
        }
      }).catch(() => {}).finally(() => setLoadingHistory(false))
    }
  }, [sessionId])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, currentThinking])

  useEffect(() => {
    if (!sessionId) {
      const lastId = sessionStorage.getItem('lastSessionId')
      if (lastId) {
        navigate(`/chat/${lastId}`, { replace: true })
      }
    }
  }, [sessionId, navigate])

  const handleSend = useCallback(async (query: string) => {
    if (!query.trim() || loading) return

    const userMsg: Message = { role: 'user', content: query }
    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setCurrentThinking('')
    setCurrentSteps([])
    setShowThinking(true)

    contentRef.current = ''
    const steps: string[] = []
    let hasResponseStarted = false

    await start(
      '/chat/agent/query/stream',
      { query, session_id: sessionId },
      {
        onThinking: (stage, content) => {
          if (!steps.includes(stage)) steps.push(stage)
          setCurrentSteps([...steps])
          setCurrentThinking(prev => prev ? `${prev}\n${content}` : (content || ''))
        },
        onResponse: (content, sessionId) => {
          if (!hasResponseStarted) {
            hasResponseStarted = true
            setShowThinking(false)
          }
          if (sessionId) {
            sessionStorage.setItem('lastSessionId', sessionId)
          }
          contentRef.current += content
          if (rafRef.current === null) {
            rafRef.current = requestAnimationFrame(() => {
              rafRef.current = null
              flushContent()
            })
          }
        },
        onSources: (items) => {
          // sources 事件在正文之后到达，挂到最后一条助手消息上
          setMessages((prev) => {
            const next = [...prev]
            for (let k = next.length - 1; k >= 0; k--) {
              if (next[k].role === 'assistant') {
                next[k] = { ...next[k], sources: items }
                break
              }
            }
            return next
          })
        },
        onSuggestion: (data) => {
          // suggestion 事件在正文之后到达，挂到最后一条助手消息上
          setMessages((prev) => {
            const next = [...prev]
            for (let k = next.length - 1; k >= 0; k--) {
              if (next[k].role === 'assistant') {
                next[k] = { ...next[k], suggestion: data }
                break
              }
            }
            return next
          })
        },
        onDone: (newSessionId) => {
          if (rafRef.current !== null) {
            cancelAnimationFrame(rafRef.current)
            rafRef.current = null
          }
          flushContent()
          if (newSessionId) {
            sessionStorage.setItem('lastSessionId', newSessionId)
          }
          if (newSessionId && newSessionId !== sessionId) {
            navigate(`/chat/${newSessionId}`, { replace: true })
          }
        },
        onError: (error) => {
          setMessages((prev) => [...prev, { role: 'assistant', content: `Error: ${error}` }])
        },
      }
    )
  }, [loading, sessionId, start, navigate, flushContent])

  // 把某条助手回答沉淀成笔记；失败静默，不能因为存不了就打断对话
  const handleSaveNote = useCallback(async (index: number) => {
    const msg = messages[index]
    if (!msg?.suggestion) return
    try {
      await notesApi.create({
        title: msg.suggestion.title,
        content: msg.content,
      })
      setMessages((prev) => {
        const next = [...prev]
        next[index] = { ...next[index], savedAt: Date.now() }
        return next
      })
    } catch {
      // 静默失败
    }
  }, [messages])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend(input)
    }
  }

  const isLoading = loadingHistory || loading
  const hasStreamingAssistant = loading && messages.length > 0 && messages[messages.length - 1].role === 'assistant'

  return (
    <div className="h-full flex flex-col">
      {messages.length > 0 && (
        <div className="shrink-0 px-6 py-3 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <div className="max-w-3xl mx-auto flex justify-end">
            <button
              onClick={() => {
                sessionStorage.removeItem('lastSessionId')
                setMessages([])
                navigate('/chat')
              }}
              className="px-3 py-1.5 text-xs rounded-md border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
            >
              {t('chat.newSession')}
            </button>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="max-w-3xl mx-auto space-y-6">
          {messages.length === 0 && !isLoading && (
            <div className="py-16 text-center space-y-6">
              <div className="flex justify-center">
                <div className="w-16 h-16 rounded-2xl bg-[var(--color-accent-bg)] flex items-center justify-center">
                  <Sparkles size={28} className="text-[var(--color-accent)]" />
                </div>
              </div>
              <h2 className="font-heading text-xl text-[var(--color-text)]">{t('chat.welcome')}</h2>
              <div className="flex flex-wrap justify-center gap-2 max-w-md mx-auto">
                {quickQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => handleSend(q)}
                    className="px-4 py-2 text-xs rounded-full border border-[var(--color-border)] text-[var(--color-text-secondary)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)] transition-colors"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {loadingHistory && (
            <div className="flex justify-center py-4">
              <Loader2 size={20} className="animate-spin text-[var(--color-text-tertiary)]" />
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}>
              {msg.role === 'assistant' && (
                <div className="w-8 h-8 rounded-lg bg-[var(--color-accent-bg)] flex items-center justify-center shrink-0">
                  <Bot size={16} className="text-[var(--color-accent)]" />
                </div>
              )}
              <div className={`max-w-[75%] ${msg.role === 'user' ? 'order-first' : ''}`}>
                {msg.role === 'user' ? (
                  <div className="px-4 py-2.5 rounded-2xl bg-[var(--color-accent)] text-white text-sm">
                    {msg.content}
                  </div>
                ) : (
                  <>
                    {i === messages.length - 1 && currentSteps.length > 0 && (
                      <div className="mb-3">
                        <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] overflow-hidden">
                          <button
                            onClick={() => setShowThinking(!showThinking)}
                            className="flex items-center gap-2 px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] w-full text-left"
                          >
                            {showThinking ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                            {t('chat.thinkingSteps')}
                          </button>
                          {showThinking && currentThinking && (
                            <div className="px-4 pb-3">
                              <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed whitespace-pre-line">{currentThinking}</p>
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                    <div className={`prose prose-sm max-w-none markdown-body${theme === 'dark' ? ' prose-invert' : ''}`}>
                      <ReactMarkdown rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                        {msg.content}
                      </ReactMarkdown>
                    </div>
                    {msg.sources && msg.sources.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-[var(--color-border)]">
                        <div className="flex items-center gap-1.5 mb-2 text-xs text-[var(--color-text-tertiary)]">
                          <BookOpen size={12} />
                          {t('chat.sources')}
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {msg.sources.map((src) => (
                            <div
                              key={src.source_id}
                              title={src.title}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)] text-xs text-[var(--color-text-secondary)] max-w-[240px]"
                            >
                              {src.source_type === 'note' ? <FileText size={12} className="shrink-0" /> : <BookOpen size={12} className="shrink-0" />}
                              <span className="truncate">{src.title}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {msg.suggestion && (
                      <div className="mt-3 flex items-center gap-3 px-3 py-2.5 rounded-lg bg-[var(--color-card)] border border-[var(--color-border)]">
                        <span className="text-xs text-[var(--color-text-secondary)] flex-1">
                          {t('chat.saveAsNote')}
                        </span>
                        {msg.savedAt ? (
                          <span className="text-xs text-[var(--color-accent)]">{t('chat.savedToNotes')}</span>
                        ) : (
                          <button
                            onClick={() => handleSaveNote(i)}
                            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-[var(--color-accent)] text-white text-xs hover:opacity-90"
                          >
                            <FileText size={12} />
                            {t('chat.saveToNotes')}
                          </button>
                        )}
                      </div>
                    )}
                    {hasStreamingAssistant && i === messages.length - 1 && (
                      <div className="flex gap-1 mt-3">
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '0ms' }} />
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '150ms' }} />
                        <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '300ms' }} />
                      </div>
                    )}
                  </>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-8 h-8 rounded-lg bg-[var(--color-bg-tertiary)] flex items-center justify-center shrink-0">
                  <User size={16} className="text-[var(--color-text-secondary)]" />
                </div>
              )}
            </div>
          ))}

          {loading && !hasStreamingAssistant && (
            <div className="flex gap-3">
              <div className="w-8 h-8 rounded-lg bg-[var(--color-accent-bg)] flex items-center justify-center shrink-0">
                <Bot size={16} className="text-[var(--color-accent)]" />
              </div>
              <div className="space-y-2 flex-1">
                {currentSteps.length > 0 && (
                  <div className="bg-[var(--color-card)] rounded-lg border border-[var(--color-border)] overflow-hidden">
                    <button
                      onClick={() => setShowThinking(!showThinking)}
                      className="flex items-center gap-2 px-4 py-2.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-secondary)] w-full text-left"
                    >
                      {showThinking ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      {t('chat.thinkingSteps')}
                    </button>
                    {showThinking && currentThinking && (
                      <div className="px-4 pb-3">
                        <p className="text-xs text-[var(--color-text-secondary)] leading-relaxed whitespace-pre-line">{currentThinking}</p>
                      </div>
                    )}
                  </div>
                )}
                <div className="flex gap-1">
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-2 h-2 rounded-full bg-[var(--color-accent)] animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>
      </div>

      <div className="border-t border-[var(--color-border)] bg-[var(--color-card)] px-6 py-4">
        <div className="max-w-3xl mx-auto flex gap-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('chat.input')}
            rows={1}
            className="flex-1 px-4 py-2.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-placeholder)] resize-none focus:outline-none focus:ring-2 focus:ring-[var(--color-accent)]"
          />
          <button
            onClick={() => handleSend(input)}
            disabled={!input.trim() || loading}
            className="flex items-center justify-center w-10 h-10 rounded-lg bg-[var(--color-accent)] text-white hover:bg-blue-700 disabled:opacity-40 transition-colors shrink-0"
          >
            {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
          </button>
        </div>
      </div>
    </div>
  )
}
