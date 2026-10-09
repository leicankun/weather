import http from './http'
import { getToken } from './auth'
import { readSse } from '../utils/sse.js'
/**
 * 发起 SSE 流式对话，逐事件回调。
 *
 * ⚠️ 这里必须用原生 fetch（axios 不适合逐块读取流），
 * 但因此**需要手动带上 Authorization** —— axios 的请求拦截器管不到 fetch。
 * 此前漏了这一步，导致已登录用户被后端当成匿名：
 * 对话不落库、左侧历史里自然也看不到。
 */
export async function streamChat(message, conversationId, onEvent, attachmentIds = [], signal) {
  const token = getToken()
  const resp = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    signal,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
      attachments: attachmentIds,
    }),
  })
  if (!resp.ok || !resp.body) {
    if (resp.status === 401) window.dispatchEvent(new Event('auth-expired'))
    const body = await resp.json().catch(() => ({}))
    throw new Error(
      typeof body.detail === 'string'
        ? body.detail
        : body.message || `对话请求失败（${resp.status}）`,
    )
  }
  await readSse(resp.body, onEvent)
}
/**
 * 上传对话附件（图片 / 语音 / 文件）。
 *
 * 后端在上传时就完成解析（图片留原图、语音转写、文件抽文本），
 * 返回的 id 在发消息时带上即可——所以这里不需要关心文件内容怎么用。
 * 超时放宽到 60s：语音转写和 PDF 解析都可能要十几秒。
 */
export async function uploadAttachment(file) {
  const form = new FormData()
  form.append('file', file)
  return http.post('/chat/attachments', form, { timeout: 60000 })
}
// 会话列表（按最后活跃时间倒序）
export async function listConversations() {
  const res = await http.get('/chat/conversations')
  return res?.items ?? []
}
// 读取某个会话的全部消息（时间正序），用于回放
export async function getConversationMessages(id) {
  const res = await http.get(`/chat/conversations/${id}`)
  return res?.items ?? []
}
export async function deleteConversation(id) {
  return http.delete(`/chat/conversations/${id}`)
}
