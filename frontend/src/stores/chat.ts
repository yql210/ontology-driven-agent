import { defineStore } from 'pinia'
import { ref } from 'vue'
import { sendChatStream, sendApproval } from '../api/chat'
import type { Message, MessageBlock, ToolCall, SSEEvent } from '../api/types'
import type { ApprovalData } from '../components/ApprovalCard.vue'

export const useChatStore = defineStore('chat', () => {
  const messages = ref<Message[]>([])
  const threadId = ref<string | null>(null)
  const isLoading = ref(false)
  const pendingApprovals = ref<Map<string, ApprovalData>>(new Map())

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

  /** 获取最后一个 block，如果是 text 就复用，否则创建新 text block */
  function ensureTextBlock(msg: Message): { block: MessageBlock & { type: 'text' }; index: number } {
    const blocks = msg.blocks!
    const last = blocks[blocks.length - 1]
    if (last && last.type === 'text') {
      return { block: last as MessageBlock & { type: 'text' }, index: blocks.length - 1 }
    }
    // 最后一个不是 text（可能是 tool_call），新建 text block
    const block: MessageBlock = { type: 'text', content: '' }
    blocks.push(block)
    return { block: block as MessageBlock & { type: 'text' }, index: blocks.length - 1 }
  }

  function detectApprovalData(result: string): ApprovalData | null {
    try {
      const data = JSON.parse(result)
      if (data.status === 'approval_required' && data.approval_id) {
        return {
          approval_id: data.approval_id,
          level: data.level || 'action',
          checks: data.checks || [],
          policies: data.policies || [],
          summary: `需要审批才能继续执行`,
        }
      }
      return null
    } catch {
      return null
    }
  }

  function handleApproval(approvalId: string, approved: boolean) {
    const lastMsg = messages.value[messages.value.length - 1]
    if (!lastMsg) return

    // 标记审批卡片为已处理
    pendingApprovals.value.delete(approvalId)

    // 添加用户操作记录
    lastMsg.blocks!.push({
      type: 'text',
      content: approved ? '✅ 已批准执行' : '❌ 已拒绝',
    })

    // 调用后端审批接口
    sendApproval(approvalId, approved)
      .then(result => {
        if (lastMsg) {
          lastMsg.blocks!.push({
            type: 'text',
            content: result.success ? `📋 ${result.message}` : `⚠️ ${result.message}`,
          })
        }
      })
      .catch(err => {
        if (lastMsg) {
          lastMsg.blocks!.push({
            type: 'text',
            content: `⚠️ 审批处理失败: ${err.message}`,
          })
        }
      })
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
              let matchedToolName = ''
              if (calls) {
                for (let i = calls.length - 1; i >= 0; i--) {
                  if (calls[i].tool === event.tool && calls[i].status === 'running') {
                    calls[i].status = 'completed'
                    if (event.result) calls[i].result = event.result
                    matchedToolName = event.tool
                    break
                  }
                }
              }
              // 检测 express_intent 返回审批请求
              if (matchedToolName === 'express_intent' && event.result) {
                const approvalData = detectApprovalData(event.result)
                if (approvalData) {
                  lastMsg.blocks!.push({ type: 'approval', approval: approvalData })
                  pendingApprovals.value.set(approvalData.approval_id, approvalData)
                }
              }
              // 检测 check_operation 返回约束检查结果
              if (matchedToolName === 'check_operation' && event.result) {
                try {
                  const data = JSON.parse(event.result)
                  if (data.checks && data.checks.length > 0) {
                    lastMsg.blocks!.push({
                      type: 'constraint_check',
                      checkResult: {
                        pass: data.pass ?? true,
                        checks: data.checks,
                        target: data.target,
                        block_reason: data.block_reason,
                      },
                    })
                  }
                } catch {
                  // 解析失败，忽略
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

  return { messages, threadId, isLoading, sendMessage, handleApproval }
})
