<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { generateTripPlan, saveItineraryBatch } from '../api/itinerary.js'
import { useAuthStore } from '../stores/auth.js'
import { useTask } from '../composables/useTask.js'
import TaskState from './TaskState.vue'
const emit = defineEmits(['saved'])
const auth = useAuthStore(),
  query = ref(''),
  result = ref(null),
  saving = ref(false),
  saved = ref(false),
  saveError = ref('')
const { busy, error, run } = useTask()
function generate() {
  run(async () => {
    if (!auth.loggedIn) throw new Error('请先登录后生成行程草案')
    if (!query.value.trim()) throw new Error('请填写目的地、日期和偏好')
    result.value = await generateTripPlan(query.value.trim())
    saved.value = false
    saveError.value = ''
  })
}
async function save() {
  if (!auth.loggedIn || saving.value || saved.value) return
  saving.value = true
  saveError.value = ''
  try {
    const items = (result.value?.plan?.plan || []).flatMap((d) =>
      d.items.map((i) => ({
        date: d.date,
        title: i.title,
        start_time: i.time || undefined,
        location: i.title,
        activity: i.activity,
        note: i.reason,
      })),
    )
    const data = await saveItineraryBatch(items)
    saved.value = true
    if (data.failed?.length)
      saveError.value = `已保存 ${data.created} 条；未保存：${data.failed.join('；')}。请到行程列表补充，避免重复保存。`
    else ElMessage.success(`已保存 ${data.created} 条行程`)
    emit('saved')
  } catch (err) {
    saveError.value = err.response?.data?.detail || err.message || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>
<template>
  <section class="panel">
    <div class="panel-title">
      <h2>✦ AI 帮我排行程</h2>
      <el-tag type="success" effect="plain">结合天气规划</el-tag>
    </div>
    <p class="muted">写下城市、出发日期、天数和喜好，先生成草案，再决定是否保存。</p>
    <el-input
      v-model="query"
      type="textarea"
      :rows="3"
      placeholder="例如：周末去广州玩两天，喜欢美食和拍照，尽量避开下雨时的户外活动"
      aria-label="行程需求"
    />
    <div class="actions section-gap">
      <el-button type="primary" :loading="busy" @click="generate">生成行程草案</el-button>
      <small class="muted">生成可能需要一分钟，请耐心等待</small>
    </div>
    <TaskState :error="error" @retry="generate" />
    <div v-if="result" class="section-gap">
      <h3>{{ result.plan?.summary }}</h3>
      <p v-if="result.weather_hint" class="notice">{{ result.weather_hint }}</p>
      <p v-if="result.area_note" class="notice">{{ result.area_note }}</p>
      <p v-for="a in result.adjustments" :key="a" class="notice">{{ a }}</p>
      <div v-for="day in result.plan?.plan || []" :key="day.date" class="list-card">
        <h3>{{ day.date }} · {{ day.weather }}</h3>
        <p v-if="day.weather_note" class="notice">{{ day.weather_note }}</p>
        <div v-for="(item, i) in day.items" :key="i" class="plan-item">
          <b>{{ item.time }}</b>
          <div>
            <strong>{{ item.title }}</strong>
            <el-tag v-if="item.weather_adjusted" type="warning" size="small">因天气调整</el-tag>
            <p class="muted">{{ item.reason }}</p>
            <a
              class="map-link"
              :href="'https://uri.amap.com/search?keyword=' + encodeURIComponent(item.title)"
              target="_blank"
              rel="noopener noreferrer"
            >
              在地图中查看 ↗
            </a>
          </div>
        </div>
      </div>
      <p v-if="saveError" class="notice" role="alert">{{ saveError }}</p>
      <div class="actions section-gap">
        <el-button
          v-if="auth.loggedIn"
          :loading="saving"
          :disabled="saved"
          type="primary"
          @click="save"
        >
          {{ saved ? '已保存，请到行程列表查看' : '保存到我的行程' }}
        </el-button>
        <RouterLink v-else to="/login?redirect=/recommend">登录后可保存行程 ↗</RouterLink>
      </div>
    </div>
  </section>
</template>
<style scoped>
.plan-item {
  display: flex;
  gap: 20px;
  padding: 13px 0;
}
.plan-item > b {
  font-size: 12px;
  color: #8a9777;
  min-width: 48px;
}
.plan-item p {
  margin: 8px 0;
}
.plan-item strong {
  font-size: 14px;
}
</style>
