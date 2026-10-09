<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  listItinerary,
  createItinerary,
  updateItinerary,
  deleteItinerary,
  listItineraryRisks,
  downloadItineraryIcs,
} from '../api/itinerary.js'
import { posterDataUrl, downloadPoster } from '../utils/itineraryShare.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
import TripPlanner from '../components/TripPlanner.vue'
const rows = ref([]),
  risks = ref({}),
  riskError = ref(''),
  date = ref(''),
  dialog = ref(false),
  editing = ref(null),
  planner = ref(false),
  poster = ref('')
const form = reactive({ title: '', date: '', start_time: '', location: '', activity: '', note: '' })
const { busy, error, run } = useTask()
async function fetchRows() {
  rows.value = (await listItinerary(date.value || undefined)).items
  try {
    risks.value = (await listItineraryRisks()).by_itinerary
    riskError.value = ''
  } catch {
    risks.value = {}
    riskError.value = '天气风险暂时无法评估，请稍后刷新。'
  }
}
function load() {
  run(fetchRows)
}
function edit(item) {
  editing.value = item?.id || null
  Object.assign(
    form,
    { title: '', date: '', start_time: '', location: '', activity: '', note: '' },
    item || {},
  )
  dialog.value = true
}
function save() {
  run(async () => {
    if (!form.title.trim() || !form.date) throw new Error('请填写活动名称和日期')
    const payload = Object.fromEntries(
      ['title', 'date', 'start_time', 'location', 'activity', 'note'].map((k) => [
        k,
        form[k] || null,
      ]),
    )
    if (editing.value) await updateItinerary(editing.value, payload)
    else await createItinerary(payload)
    dialog.value = false
    await fetchRows()
    ElMessage.success('行程已保存')
  })
}
async function remove(item) {
  try {
    await ElMessageBox.confirm(`删除「${item.title}」？`, '删除行程', { type: 'warning' })
  } catch {
    return
  }
  run(async () => {
    await deleteItinerary(item.id)
    await fetchRows()
  })
}
onMounted(load)
</script>
<template>
  <PageTitle title="我的行程" description="把想去的地方记下来，也给天气变化留一点余地。">
    <el-button @click="planner = !planner">✦ AI 排行程</el-button>
    <el-button type="primary" @click="edit()">＋ 添加行程</el-button>
  </PageTitle>
  <TripPlanner v-if="planner" @saved="load" />
  <section class="panel section-gap">
    <div class="panel-title">
      <div class="actions">
        <el-date-picker
          v-model="date"
          type="date"
          value-format="YYYY-MM-DD"
          placeholder="按日期筛选"
          aria-label="筛选日期"
          clearable
          @change="load"
        />
        <el-button :loading="busy" @click="load">刷新</el-button>
      </div>
      <div class="actions">
        <el-button :disabled="!rows.length" @click="run(downloadItineraryIcs)">导出日历</el-button>
        <el-button :disabled="!rows.length" @click="poster = posterDataUrl(rows)">
          分享海报
        </el-button>
      </div>
    </div>
    <TaskState :error="error" :busy="busy" @retry="load" />
    <p v-if="riskError" class="notice">{{ riskError }}</p>
    <el-empty v-if="!rows.length && !busy" description="还没有行程，安排一次出发吧" />
    <article v-for="item in rows" :key="item.id" class="list-card">
      <div class="panel-title">
        <div>
          <small class="muted">{{ item.date }} {{ item.start_time }}</small>
          <h3 class="section-gap">{{ item.title }}</h3>
        </div>
        <div class="actions">
          <el-button text @click="edit(item)">编辑</el-button>
          <el-button text type="danger" @click="remove(item)">删除</el-button>
        </div>
      </div>
      <p class="muted">{{ item.location }} · {{ item.activity }}</p>
      <p v-if="item.note" class="small">{{ item.note }}</p>
      <p v-if="risks[item.id]" class="notice">{{ risks[item.id].body || risks[item.id].reason }}</p>
    </article>
  </section>
  <el-dialog v-model="dialog" :title="editing ? '编辑行程' : '添加行程'" width="550px">
    <el-form label-position="top" class="form-grid">
      <el-form-item label="活动名称" class="wide">
        <el-input v-model="form.title" aria-label="活动名称" maxlength="255" />
      </el-form-item>
      <el-form-item label="日期">
        <el-date-picker
          v-model="form.date"
          type="date"
          value-format="YYYY-MM-DD"
          aria-label="行程日期"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item label="时间">
        <el-time-select
          v-model="form.start_time"
          start="00:00"
          step="00:15"
          end="23:45"
          placeholder="可选"
        />
      </el-form-item>
      <el-form-item label="地点"><el-input v-model="form.location" /></el-form-item>
      <el-form-item label="活动类型">
        <el-input v-model="form.activity" placeholder="例如：户外游览" />
      </el-form-item>
      <el-form-item label="备注" class="wide">
        <el-input v-model="form.note" type="textarea" />
      </el-form-item>
    </el-form>
    <p v-if="error" class="error-state">{{ error }}</p>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存行程</el-button>
    </template>
  </el-dialog>
  <el-dialog :model-value="!!poster" title="行程海报" width="500px" @close="poster = ''">
    <img :src="poster" alt="行程分享海报" style="width: 100%" />
    <template #footer><el-button @click="downloadPoster(poster)">下载海报</el-button></template>
  </el-dialog>
</template>
