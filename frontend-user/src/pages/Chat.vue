<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import {
  streamChat,
  listConversations,
  getConversationMessages,
  deleteConversation,
  uploadAttachment,
} from '../api/chat.js'
import { useAuthStore } from '../stores/auth.js'
import { useTask } from '../composables/useTask.js'
import { prepareUpload } from '../utils/prepareUpload.js'
import { apiErrorText } from '../utils/apiError.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
import MarkdownText from '../components/MarkdownText.vue'
const auth = useAuthStore(),
  route = useRoute()
const conversations = ref([]),
  messages = ref([]),
  input = ref(String(route.query.q || '')),
  conversationId = ref(crypto.randomUUID())
const attachments = ref([]),
  sending = ref(false),
  uploading = ref(false),
  intent = ref(''),
  bottom = ref(null)
const { busy, error, run } = useTask()
let controller, recorder, mediaStream
const recording = ref(false)
async function history() {
  if (auth.loggedIn) conversations.value = await listConversations()
}
function newChat() {
  if (sending.value) return
  conversationId.value = crypto.randomUUID()
  messages.value = []
  attachments.value = []
  error.value = ''
  intent.value = ''
}
async function openChat(id) {
  if (sending.value) return
  await run(async () => {
    const rows = await getConversationMessages(id)
    conversationId.value = id
    messages.value = rows
    attachments.value = []
  })
}
async function remove(id) {
  if (sending.value) return
  try {
    await ElMessageBox.confirm('删除这个会话及其聊天记录？', '删除会话', { type: 'warning' })
  } catch {
    return
  }
  await run(async () => {
    await deleteConversation(id)
    if (id === conversationId.value) newChat()
    await history()
  })
}
async function scroll() {
  await nextTick()
  bottom.value?.scrollIntoView({ block: 'nearest' })
}
async function send() {
  if (sending.value || uploading.value || (!input.value.trim() && !attachments.value.length)) return
  const text = input.value.trim(),
    files = [...attachments.value]
  messages.value.push({
    role: 'user',
    content: text + (files.length ? '\n附件：' + files.map((f) => f.name).join('、') : ''),
  })
  messages.value.push({ role: 'assistant', content: '' })
  const index = messages.value.length - 1
  input.value = ''
  attachments.value = []
  sending.value = true
  error.value = ''
  intent.value = ''
  controller = new AbortController()
  try {
    await streamChat(
      text,
      conversationId.value,
      (event) => {
        if (event.type === 'token') {
          messages.value[index].content += event.content || ''
          scroll()
        }
        if (event.type === 'intent') intent.value = event.intent
        if (event.type === 'error') throw new Error(event.message || event.content || '对话失败')
      },
      files.map((f) => f.id),
      controller.signal,
    )
    await history()
  } catch (err) {
    if (err.name === 'AbortError') {
      messages.value[index].content += '\n\n[已停止生成]'
    } else {
      error.value = apiErrorText(err, '对话失败，请重试')
      if (!messages.value[index].content) messages.value[index].content = '本次回答未能生成。'
      input.value = text
    }
  } finally {
    sending.value = false
    await scroll()
  }
}
async function upload(file) {
  if (!file) return
  uploading.value = true
  error.value = ''
  try {
    const prepared = await prepareUpload(file)
    attachments.value.push(await uploadAttachment(prepared.file))
  } catch (err) {
    error.value = apiErrorText(err, '附件上传失败')
  } finally {
    uploading.value = false
  }
}
async function pick(event) {
  for (const file of event.target.files || []) await upload(file)
  event.target.value = ''
}
async function record() {
  if (recording.value) {
    recorder.stop()
    recording.value = false
    return
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    recorder = new MediaRecorder(mediaStream)
    const chunks = []
    recorder.ondataavailable = (e) => chunks.push(e.data)
    recorder.onstop = () => {
      mediaStream.getTracks().forEach((t) => t.stop())
      upload(new File(chunks, '语音.webm', { type: recorder.mimeType }))
    }
    recorder.start()
    recording.value = true
  } catch {
    error.value = '录音不可用，请允许麦克风权限或上传音频文件'
  }
}
onMounted(() => run(history))
onUnmounted(() => {
  controller?.abort()
  if (recorder?.state === 'recording') {
    recorder.onstop = null
    recorder.stop()
  }
  mediaStream?.getTracks().forEach((t) => t.stop())
})
</script>
<template>
  <PageTitle title="AI 出行助手" description="查天气、规划路线、安排日程，让想法变成一次出发。">
    <el-button :disabled="sending" @click="newChat">＋ 新对话</el-button>
  </PageTitle>
  <div class="chat-grid">
    <aside class="panel history-panel">
      <h3>历史对话</h3>
      <p v-if="!auth.loggedIn" class="muted">登录后可保存和继续对话。</p>
      <div
        v-for="c in conversations"
        :key="c.conversation_id"
        class="conversation"
        :class="{ selected: c.conversation_id === conversationId }"
      >
        <button :disabled="sending" @click="openChat(c.conversation_id)">
          {{ c.title || '新对话' }}
        </button>
        <el-button
          text
          :disabled="sending"
          aria-label="删除会话"
          @click="remove(c.conversation_id)"
        >
          ×
        </el-button>
      </div>
    </aside>
    <section class="panel chat-panel">
      <TaskState :error="error" :busy="busy" @retry="input.trim() ? send() : run(history)" />
      <div class="messages" aria-live="polite">
        <div v-if="!messages.length" class="chat-empty">
          <div class="chat-star">✦</div>
          <h2>下一站，想去哪里？</h2>
          <p class="muted">告诉我目的地、日期和偏好，我会结合天气帮你规划。</p>
          <div class="suggestions">
            <button
              v-for="q in [
                '广州今天适合户外活动吗？',
                '从广州南站去广州塔怎么走？',
                '周末去广州玩两天，喜欢美食和拍照',
              ]"
              :key="q"
              @click="input = q"
            >
              {{ q }} ↗
            </button>
          </div>
        </div>
        <article v-for="(m, i) in messages" :key="i" class="chat-message" :class="m.role">
          <small>{{ m.role === 'user' ? '你' : '✦ 出行助手' }}</small>
          <MarkdownText :text="m.content || (sending ? '正在查询和整理信息…' : '')" />
        </article>
        <div ref="bottom" />
      </div>
      <div class="composer">
        <div class="tag-row">
          <el-tag
            v-for="(a, i) in attachments"
            :key="a.id"
            closable
            @close="attachments.splice(i, 1)"
          >
            {{ a.name }}
          </el-tag>
          <span v-if="uploading" class="muted">附件解析中…</span>
        </div>
        <el-input
          v-model="input"
          type="textarea"
          :rows="3"
          placeholder="例如：周六去广州塔，帮我看看天气和出行方案…"
          aria-label="对话内容"
          @keydown.ctrl.enter.prevent="send"
        />
        <div class="composer-actions">
          <div class="actions">
            <label class="file-label">
              ＋ 附件
              <input
                type="file"
                multiple
                accept="image/*,audio/*,.pdf,.docx,.xlsx,.txt,.md,.csv,.json"
                :disabled="sending || uploading"
                hidden
                @change="pick"
              />
            </label>
            <el-button text :disabled="sending || uploading" @click="record">
              {{ recording ? '停止录音' : '录音' }}
            </el-button>
            <small class="muted">{{ intent ? '正在处理：' + intent : 'Ctrl + Enter 发送' }}</small>
          </div>
          <el-button v-if="sending" @click="controller?.abort()">停止生成</el-button>
          <el-button
            v-else
            type="primary"
            :disabled="uploading || recording || (!input.trim() && !attachments.length)"
            @click="send"
          >
            发送 ↗
          </el-button>
        </div>
      </div>
    </section>
  </div>
</template>
<style scoped>
.chat-grid {
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  gap: 20px;
}
.history-panel {
  padding: 20px 12px;
}
.history-panel h3,
.history-panel > p {
  padding: 0 8px;
}
.conversation {
  display: flex;
  border-radius: 8px;
  align-items: center;
}
.conversation.selected {
  background: #eef3e9;
}
.conversation > button:first-child {
  border: 0;
  background: transparent;
  text-align: left;
  padding: 12px 9px;
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  text-overflow: ellipsis;
  overflow: hidden;
  font-size: 12px;
  color: #617065;
  cursor: pointer;
}
.chat-panel {
  margin: 0 !important;
  display: flex;
  flex-direction: column;
  min-height: 650px;
}
.messages {
  height: 440px;
  overflow: auto;
  padding: 8px 4px 20px;
}
.chat-empty {
  text-align: center;
  padding: 40px 0;
}
.chat-star {
  font-size: 45px;
  color: #7f9a69;
  margin-bottom: 22px;
}
.suggestions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 450px;
  margin: 22px auto;
}
.suggestions button {
  background: #f4f7f0;
  border: 1px solid #e5ebdf;
  border-radius: 8px;
  padding: 13px;
  color: #72806a;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.chat-message {
  padding: 16px 18px;
  background: #f6f8f2;
  border-radius: 12px;
  margin-bottom: 18px;
}
.chat-message.user {
  margin-left: 12%;
  background: #e8f0e5;
}
.chat-message small {
  display: block;
  color: #8b9a80;
  margin-bottom: 10px;
}
.composer {
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid #edf0e9;
}
.composer-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}
.composer .tag-row {
  margin-bottom: 10px;
}
@media (max-width: 900px) {
  .chat-grid {
    grid-template-columns: 1fr;
  }
  .history-panel {
    max-height: 170px;
    overflow: auto;
  }
  .chat-panel {
    min-height: 600px;
  }
  .messages {
    height: 400px;
  }
  .composer-actions .muted {
    display: none;
  }
}
</style>
