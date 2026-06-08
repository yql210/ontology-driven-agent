import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sendChatStream } from '../api/chat'
import type { Message, MessageBlock, ToolCall, SSEEvent } from '../api/types'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const threadId = ref<string | null>(null)
  const isLoading = ref(false)

  function addUserMessage(content: string) {
    messages.value.push({
      id: `user-${Date.now()}`,
      role: 'user',
      content,
      blocks: [{ type: 'text', content }],
      timestamp: Date.now(),
    })
  }

  function addAssistantMessage(): Message {
    const msg: Message = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      content: '',
      blocks: [],
      toolCalls: [],
      isStreaming: true,
      timestamp: Date.now(),
    }
    messages.value.push(msg)
    return msg
  }

  /** 获取最后一个 text block（或创建新的） */
  function ensureTextBlock(msg: Message): { block: MessageBlock & { type: 'text' }; index: number } {
    const blocks = msg.blocks!
    // 找最后一个 text block
    for (let i = blocks.length - 1; i >= 0; i--) {
      if (blocks[i].type === 'text') {
        return { block: blocks[i] as MessageBlock & { type: 'text' }, index: i }
      }
    }
    // 没有则新建
    const block: MessageBlock = { type: 'text', content: '' }
    blocks.push(block)
    return { block: block as MessageBlock & { type: 'text' }, index: blocks.length - 1 }
  }

  async function sendMessage(content: string) {
    if (!content.trim() || isLoading.value) return
    isLoading.value = true
    addUserMessage(content)
    addAssistantMessage()

    try {
      await sendChatStream(
        { message: content, thread_id: threadId.value },
        (event: SSEEvent) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (!lastMsg) return

          switch (event.type) {
            case 'token':
              lastMsg.content += event.content
              ensureTextBlock(lastMsg).block.content += event.content
              break
            case 'tool_start': {
              const toolCall: ToolCall = {
                id: `tc-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                tool: event.tool,
                args: event.args,
                status: 'running',
              }
              if (!lastMsg.toolCalls) lastMsg.toolCalls = []
              lastMsg.toolCalls.push(toolCall)
              // 插入 tool_call block（在当前 text block 之后）
              lastMsg.blocks!.push({ type: 'tool_call', toolCall })
              break
            }
            case 'tool_end': {
              // 匹配最后一个同名 running 工具
              const calls = lastMsg.toolCalls
              if (calls) {
                for (let i = calls.length - 1; i >= 0; i--) {
                  if (calls[i].tool === event.tool && calls[i].status === 'running') {
                    calls[i].status = 'completed'
                    if (event.result) calls[i].result = event.result
                    break
                  }
                }
              }
              break
            }
            case 'error':
              lastMsg.content += `\n\n⚠️ 错误: ${event.message}`
              lastMsg.blocks!.push({ type: 'text', content: `\n\n⚠️ 错误: ${event.message}` })
              break
            case 'done':
              if (event.thread_id) threadId.value = event.thread_id
              lastMsg.isStreaming = false
              isLoading.value = false
              break
          }
        },
        (err: Error) => {
          const lastMsg = messages.value[messages.value.length - 1]
          if (lastMsg) {
            lastMsg.content += `\n\n⚠️ 连接错误: ${err.message}`
            lastMsg.blocks!.push({ type: 'text', content: `\n\n⚠️ 连接错误: ${err.message}` })
            lastMsg.isStreaming = false
          }
          isLoading.value = false
        },
      )
    } catch (err) {
      const lastMsg = messages.value[messages.value.length - 1]
      if (lastMsg) {
        lastMsg.content += `\n\n⚠️ 发送失败: ${(err as Error).message}`
        lastMsg.blocks!.push({ type: 'text', content: `\n\n⚠️ 发送失败: ${(err as Error).message}` })
        lastMsg.isStreaming = false
      }
      isLoading.value = false
    }
  }

  return { messages, threadId, isLoading, sendMessage }
})
