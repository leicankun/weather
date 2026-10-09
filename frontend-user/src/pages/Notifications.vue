<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import {
  getNotificationLogs,
  getPrefs,
  updatePrefs,
  listSubscriptions,
  deleteSubscription,
  getChannelStatus,
  getVapidKey,
  saveSubscription,
  sendTestNotification,
} from '../api/notifications.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const logs = ref([]),
  devices = ref([]),
  channels = ref(null),
  testResult = ref(null)
const prefs = reactive({
  morning_enabled: true,
  morning_hour: 7,
  alert_enabled: true,
  itinerary_enabled: true,
  risk_enabled: true,
})
const flags = [
  ['morning_enabled', '每日天气早报'],
  ['alert_enabled', '天气预警'],
  ['itinerary_enabled', '行程出发提醒'],
  ['risk_enabled', '行程天气风险提醒'],
]
const { busy, error, run } = useTask()
async function fetchData() {
  const [p, l, d, c] = await Promise.all([
    getPrefs(),
    getNotificationLogs(),
    listSubscriptions(),
    getChannelStatus(),
  ])
  Object.assign(prefs, p)
  logs.value = l
  devices.value = d
  channels.value = c
}
function load() {
  run(fetchData)
}
function save() {
  run(async () => {
    Object.assign(prefs, await updatePrefs({ ...prefs }))
    ElMessage.success('通知偏好已保存')
  })
}
function subscribe() {
  run(async () => {
    if (!('serviceWorker' in navigator) || !('PushManager' in window))
      throw new Error('当前浏览器不支持推送，请使用通知邮箱')
    const registration = await navigator.serviceWorker.getRegistration()
    if (!registration?.active)
      throw new Error('请在构建后的正式站点开启推送；开发模式未启用 Service Worker')
    const key = await getVapidKey()
    if (!key) throw new Error('服务端尚未配置推送通道')
    if ((await Notification.requestPermission()) !== 'granted')
      throw new Error('请在浏览器设置中允许通知')
    const padded = key
      .padEnd(Math.ceil(key.length / 4) * 4, '=')
      .replace(/-/g, '+')
      .replace(/_/g, '/')
    const applicationServerKey = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0))
    const sub =
      (await registration.pushManager.getSubscription()) ||
      (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey }))
    await saveSubscription({ ...sub.toJSON(), user_agent: navigator.userAgent })
    await fetchData()
    ElMessage.success('已开启本设备推送')
  })
}
function unsubscribe(id) {
  run(async () => {
    await deleteSubscription(id)
    await fetchData()
  })
}
function test() {
  run(async () => {
    testResult.value = await sendTestNotification()
    await fetchData()
  })
}
onMounted(load)
</script>
<template>
  <PageTitle title="通知中心" description="天气有变化、行程将开始时，及时收到提醒。">
    <el-button :loading="busy" @click="load">刷新</el-button>
  </PageTitle>
  <TaskState :error="error" :busy="busy" @retry="load" />
  <div class="grid-two">
    <div class="stack">
      <section class="panel">
        <h2>提醒偏好</h2>
        <div v-for="[key, label] in flags" :key="key" class="preference-row">
          <span>{{ label }}</span>
          <el-switch v-model="prefs[key]" :aria-label="label" />
        </div>
        <div class="preference-row">
          <span>早报时间</span>
          <el-select v-model="prefs.morning_hour" style="width: 120px">
            <el-option v-for="n in 24" :key="n" :label="n - 1 + ':00'" :value="n - 1" />
          </el-select>
        </div>
        <el-button class="section-gap" type="primary" :loading="busy" @click="save">
          保存通知设置
        </el-button>
      </section>
      <section class="panel">
        <h2>接收方式</h2>
        <p>浏览器推送：{{ channels?.web_push?.ready ? '已就绪' : '未就绪' }}</p>
        <p>
          邮件：{{ channels?.email?.ready ? '已就绪' : '未就绪' }}
          <span class="muted">{{ channels?.email?.bound_email }}</span>
        </p>
        <div class="actions">
          <el-button :loading="busy" @click="subscribe">开启本设备推送</el-button>
          <el-button :loading="busy" @click="test">发送测试通知</el-button>
          <RouterLink class="map-link" to="/profile">绑定邮箱 ↗</RouterLink>
        </div>
        <p v-for="(r, k) in testResult || {}" :key="k" class="notice section-gap">
          {{ k }}：{{ r.status }} {{ r.error }}
        </p>
        <div v-for="d in devices" :key="d.id" class="list-card">
          <p class="small">{{ d.user_agent || d.endpoint_host || '未知设备' }}</p>
          <el-button text type="danger" :disabled="!d.is_active" @click="unsubscribe(d.id)">
            {{ d.is_active ? '停用设备' : '已停用' }}
          </el-button>
        </div>
      </section>
    </div>
    <section class="panel">
      <h2>发送记录</h2>
      <el-empty v-if="!logs.length && !busy" description="还没有通知记录" />
      <article v-for="log in logs" :key="log.id" class="list-card">
        <div class="panel-title">
          <h3>{{ log.title }}</h3>
          <el-tag :type="log.status === 'sent' ? 'success' : 'info'">
            {{ log.status === 'sent' ? '已发送' : log.status === 'failed' ? '失败' : '未发送' }}
          </el-tag>
        </div>
        <p class="small">{{ log.body }}</p>
        <p v-if="log.error" class="muted">{{ log.error }}</p>
        <small class="muted">
          {{ log.created_at?.replace('T', ' ').slice(0, 16) }} · {{ log.channel }}
        </small>
      </article>
    </section>
  </div>
</template>
<style scoped>
.preference-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  font-size: 13px;
  border-bottom: 1px solid #edf0e9;
}
</style>
