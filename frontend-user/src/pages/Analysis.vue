<script setup>
import { ref, computed, onMounted } from 'vue'
import http from '../api/http.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const rows = ref([]),
  days = ref(90),
  { busy, error, run } = useTask()
function load() {
  run(async () => {
    rows.value =
      (await http.get('/weather/analysis/daily', { params: { location: 'gz', days: days.value } }))
        .items || []
  })
}
const temps = computed(() =>
  rows.value.flatMap((r) => [r.temp_min, r.temp_max]).filter((v) => v != null),
)
const lo = computed(() => Math.min(...temps.value, 0)),
  hi = computed(() => Math.max(...temps.value, 1))
function points(key) {
  return rows.value
    .map((r, i) =>
      r[key] == null
        ? null
        : `${30 + (i * 660) / Math.max(rows.value.length - 1, 1)},${180 - ((r[key] - lo.value) * 145) / (hi.value - lo.value)}`,
    )
    .filter(Boolean)
    .join(' ')
}
const stats = computed(() => {
  const valid = rows.value.filter((r) => r.temp_max != null)
  return {
    max: valid.length ? Math.max(...valid.map((r) => r.temp_max)) : '—',
    rain: rows.value.filter((r) => (r.precip_sum || 0) > 1).length,
    count: rows.value.length,
  }
})
onMounted(load)
</script>
<template>
  <PageTitle title="广州 · 天气趋势" description="从持续积累的观测记录中，了解天气的变化。">
    <el-segmented
      v-model="days"
      :options="[
        { label: '30 天', value: 30 },
        { label: '90 天', value: 90 },
        { label: '180 天', value: 180 },
      ]"
      @change="load"
    />
    <el-button @click="load">刷新</el-button>
  </PageTitle>
  <TaskState :error="error" :busy="busy" @retry="load" />
  <div class="grid-three">
    <section class="panel">
      <p class="muted">有记录的天数</p>
      <div class="metric-value">
        {{ stats.count }}
        <small>天</small>
      </div>
    </section>
    <section class="panel" style="margin: 0">
      <p class="muted">期间最高温度</p>
      <div class="metric-value">
        {{ stats.max }}
        <small>℃</small>
      </div>
    </section>
    <section class="panel" style="margin: 0">
      <p class="muted">降水超过 1 mm</p>
      <div class="metric-value">
        {{ stats.rain }}
        <small>天</small>
      </div>
    </section>
  </div>
  <section class="panel section-gap">
    <h2>最高 / 最低温变化</h2>
    <el-empty v-if="rows.length < 2 && !busy" description="历史数据还在积累中" />
    <svg
      v-else
      viewBox="0 0 720 230"
      role="img"
      aria-label="最高与最低气温趋势图"
      style="width: 100%"
    >
      <line x1="30" y1="180" x2="690" y2="180" stroke="#dce5d4" />
      <polyline :points="points('temp_max')" fill="none" stroke="#a7ab62" stroke-width="3" />
      <polyline :points="points('temp_min')" fill="none" stroke="#659b93" stroke-width="3" />
      <text x="30" y="215" font-size="12" fill="#89967b">{{ rows[0]?.date }}</text>
      <text x="610" y="215" font-size="12" fill="#89967b">{{ rows.at(-1)?.date }}</text>
    </svg>
    <p class="muted">黄绿色：最高温 · 青绿色：最低温</p>
    <el-table :data="rows" max-height="400">
      <el-table-column prop="date" label="日期" />
      <el-table-column prop="temp_max" label="最高温 ℃" />
      <el-table-column prop="temp_min" label="最低温 ℃" />
      <el-table-column prop="precip_sum" label="降水 mm" />
    </el-table>
  </section>
</template>
