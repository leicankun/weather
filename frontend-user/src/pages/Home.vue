<script setup>
import { ref, onMounted, computed } from 'vue'
import { getDashboard } from '../api/home.js'
import { getLocalWeather, listDistricts } from '../api/weather.js'
import http from '../api/http.js'
import { formatWeatherTime } from '../utils/datetime.js'
import { useAuthStore } from '../stores/auth.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const dashboard = ref(null),
  local = ref(null),
  hourly = ref([]),
  aqi = ref(null),
  districts = ref([])
const district = ref(localStorage.getItem('weather-district') || '')
const { busy, error, run } = useTask()
const auth = useAuthStore()
const extraError = ref('')
const current = computed(() => local.value?.current || {})
const forecast = computed(() => local.value?.forecast || [])
const symbol = (desc) => (/雨/.test(desc || '') ? '☂' : /云|阴/.test(desc || '') ? '☁' : '☀')
async function load(coords) {
  await run(async () => {
    extraError.value = ''
    const result = await getLocalWeather(coords || { district: district.value || undefined })
    local.value = result
    const responses = await Promise.allSettled([
      getDashboard('广州'),
      auth.loggedIn
        ? http.get('/weather/hourly', { params: { city: '广州' } })
        : Promise.resolve({ items: [] }),
      auth.loggedIn
        ? http.get('/weather/aqi', { params: { city: '广州' } })
        : Promise.resolve(null),
    ])
    dashboard.value = responses[0].status === 'fulfilled' ? responses[0].value : null
    hourly.value = responses[1].status === 'fulfilled' ? responses[1].value.items || [] : []
    aqi.value = responses[2].status === 'fulfilled' ? responses[2].value : null
    if (responses.some((r) => r.status === 'rejected'))
      extraError.value = '部分天气详情暂时无法加载，可稍后刷新。'
  })
}
function changeDistrict() {
  localStorage.setItem('weather-district', district.value)
  load()
}
function locate() {
  if (!navigator.geolocation) {
    error.value = '当前浏览器不支持定位，请手动选择区域'
    return
  }
  navigator.geolocation.getCurrentPosition(
    (p) => load({ lat: p.coords.latitude, lon: p.coords.longitude }),
    () => {
      error.value = '无法获取位置，请允许定位或手动选择区域'
    },
    { timeout: 8000 },
  )
}
onMounted(() => {
  load()
  listDistricts()
    .then((r) => {
      districts.value = r
    })
    .catch(() => {})
})
</script>
<template>
  <PageTitle title="看看天气，再出发。" description="把天气、穿搭和行程放在一起，为今天做好准备。">
    <el-select
      v-model="district"
      aria-label="选择区域"
      placeholder="广州 · 全市"
      clearable
      style="width: 150px"
      @change="changeDistrict"
    >
      <el-option v-for="d in districts" :key="d.name" :label="d.name" :value="d.name" />
    </el-select>
    <el-button :loading="busy" @click="load()">刷新天气</el-button>
  </PageTitle>
  <TaskState :error="error" :busy="busy" @retry="load()" />
  <div v-if="extraError" class="notice">{{ extraError }}</div>
  <div class="home-grid">
    <div class="stack">
      <section class="weather-hero">
        <div class="hero-top">
          <span>⌖ {{ local?.location?.label || '广州' }}</span>
          <el-button text @click="locate">定位到我</el-button>
        </div>
        <div class="hero-main">
          <div>
            <div class="temperature">
              {{ current.temperature ?? '—' }}
              <small>°</small>
            </div>
            <h2>{{ current.weather_desc || (busy ? '正在获取天气' : '等待天气数据') }}</h2>
            <p>
              {{ local?.location?.precision === 'city' ? '市中心参考天气' : '所在区域天气' }} ·
              {{ formatWeatherTime(current.time) }}
            </p>
          </div>
          <div class="weather-symbol" aria-hidden="true">{{ symbol(current.weather_desc) }}</div>
        </div>
        <div class="weather-metrics">
          <div>
            体感温度
            <strong>{{ current.feels_like ?? '—' }} ℃</strong>
          </div>
          <div>
            相对湿度
            <strong>{{ current.humidity ?? '—' }} %</strong>
          </div>
          <div>
            风速
            <strong>{{ current.wind_speed ?? '—' }} km/h</strong>
          </div>
          <div>
            空气质量
            <strong>{{ aqi?.us_aqi ?? aqi?.aqi ?? '—' }}</strong>
          </div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-title">
          <h2>未来 7 天</h2>
          <span class="muted">提前准备，从容出行</span>
        </div>
        <div v-if="forecast.length" class="forecast-grid">
          <div v-for="day in forecast" :key="day.date || day.time" class="forecast-day">
            <span>{{ (day.date || day.time)?.slice(5, 10) }}</span>
            <b>{{ symbol(day.weather_desc) }}</b>
            <span>{{ day.weather_desc }}</span>
            <strong>
              {{ day.temp_max ?? '—' }}°
              <i>{{ day.temp_min ?? '—' }}°</i>
            </strong>
            <small>降水 {{ day.precipitation ?? 0 }} mm</small>
          </div>
        </div>
        <el-empty v-else description="暂时没有预报数据" :image-size="65" />
      </section>
      <section class="panel">
        <div class="panel-title">
          <h2>逐小时天气</h2>
          <span class="muted">广州 · 未来 24 小时</span>
        </div>
        <div class="hourly-strip">
          <div v-for="h in hourly.slice(0, 24)" :key="h.time">
            <small>{{ h.time?.includes('T') ? h.time.slice(11, 16) : h.time }}</small>
            <b>{{ symbol(h.weather_desc) }}</b>
            <strong>{{ h.temperature ?? '—' }}°</strong>
            <small>{{ h.precip_prob ?? '—' }}% 降雨</small>
          </div>
          <p v-if="!hourly.length" class="muted">
            {{ auth.loggedIn ? '暂无逐小时预报' : '登录后可查看逐小时预报' }}
          </p>
        </div>
      </section>
    </div>
    <aside class="stack">
      <section class="assistant-card">
        <span class="eyebrow">YOUR WEATHER COMPANION</span>
        <h2>让 AI 帮你想周全。</h2>
        <p>
          去哪玩、怎么走、穿什么？
          <br />
          把你的想法告诉出行助手。
        </p>
        <RouterLink to="/chat"><el-button type="primary">开始规划 ↗</el-button></RouterLink>
        <div class="assistant-art" aria-hidden="true">✦</div>
      </section>
      <section class="panel">
        <div class="panel-title">
          <h2>今日提醒</h2>
          <span>☷</span>
        </div>
        <div v-for="tip in dashboard?.tips || []" :key="tip.title" class="tip">
          <strong>{{ tip.title }}</strong>
          <p>{{ tip.text }}</p>
        </div>
        <p v-if="!dashboard?.tips?.length" class="muted">天气提醒将在数据更新后展示。</p>
        <div v-if="dashboard?.outfit" class="notice">{{ dashboard.outfit.suggestion }}</div>
      </section>
      <section class="panel">
        <div class="panel-title">
          <h2>近期行程</h2>
          <RouterLink class="map-link" to="/itinerary">查看全部 ↗</RouterLink>
        </div>
        <div v-for="item in dashboard?.itinerary?.upcoming || []" :key="item.id" class="list-card">
          <small class="muted">{{ item.date }} {{ item.start_time }}</small>
          <h3>{{ item.title }}</h3>
          <p class="small">{{ item.weather_hint }}</p>
        </div>
        <p v-if="!dashboard?.itinerary?.upcoming?.length" class="muted">
          留一点时间，安排一次出发。
        </p>
      </section>
    </aside>
  </div>
  <section v-if="dashboard?.indices?.length" class="panel section-gap">
    <h2>生活指数</h2>
    <div class="grid-three">
      <div v-for="item in dashboard.indices" :key="item.type_code">
        <h3>{{ item.name }} · {{ item.category }}</h3>
        <p class="muted">{{ item.text }}</p>
      </div>
    </div>
  </section>
</template>
<style scoped>
.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 23px;
}
.weather-hero {
  border-radius: 18px;
  padding: 25px 30px;
  background: #e3ebda;
  position: relative;
  overflow: hidden;
}
.hero-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13px;
}
.hero-main {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 18px 0 25px;
}
.temperature {
  font-size: 86px;
  font-weight: 400;
  letter-spacing: -6px;
  line-height: 1.2;
}
.temperature small {
  font-size: 45px;
  vertical-align: top;
}
.hero-main h2 {
  font-size: 19px;
  margin: 8px 0;
}
.hero-main p {
  font-size: 11px;
  color: #7a8a73;
  margin: 0;
}
.weather-symbol {
  font-size: 140px;
  line-height: 1;
  color: #d0af59;
  text-shadow: 0 8px 30px #f4e7b7;
}
.weather-metrics {
  border-top: 1px solid #cdd9c3;
  padding-top: 21px;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  font-size: 10px;
  color: #7b8974;
}
.weather-metrics strong {
  display: block;
  font-size: 16px;
  font-weight: 500;
  color: #405441;
  margin-top: 9px;
}
.forecast-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(65px, 1fr));
  overflow: auto;
  gap: 6px;
}
.forecast-day {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 11px;
}
.forecast-day b {
  font-size: 31px;
  color: #a4b36b;
  font-weight: 400;
}
.forecast-day strong {
  font-size: 13px;
}
.forecast-day i {
  font-style: normal;
  font-weight: 400;
  color: #98a28e;
}
.forecast-day small {
  color: #98a28e;
  font-size: 9px;
}
.assistant-card {
  border-radius: 16px;
  padding: 26px;
  background: #244d40;
  color: #fff;
  position: relative;
  overflow: hidden;
}
.assistant-card h2 {
  font-size: 22px;
  margin-top: 24px;
}
.assistant-card p {
  color: #b8cdbe;
  font-size: 12px;
  line-height: 2;
}
.assistant-card .el-button {
  background: #dfe9c6;
  border-color: #dfe9c6;
  color: #294c38;
}
.eyebrow {
  font-size: 9px;
  letter-spacing: 1.5px;
  color: #a8bca2;
}
.assistant-art {
  position: absolute;
  right: 10px;
  bottom: 3px;
  font-size: 120px;
  color: #54715a;
  line-height: 1;
  transform: rotate(15deg);
  pointer-events: none;
}
.tip {
  font-size: 12px;
  padding-bottom: 12px;
}
.tip p {
  color: #8a9585;
  margin: 6px 0 0;
}
.hourly-strip {
  display: flex;
  overflow: auto;
  gap: 24px;
  padding-bottom: 10px;
}
.hourly-strip > div {
  min-width: 58px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}
.hourly-strip small {
  font-size: 10px;
  color: #8b9886;
}
.hourly-strip b {
  font-size: 23px;
  color: #b0b66f;
}
.hourly-strip strong {
  font-size: 16px;
  font-weight: 500;
}
@media (max-width: 1150px) {
  .home-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .home-grid > aside {
    display: grid;
    grid-template-columns: 1fr 1fr;
  }
  .assistant-card {
    grid-column: 1/-1;
  }
  .assistant-art {
    right: 35px;
    bottom: 20px;
  }
}
@media (max-width: 760px) {
  .home-grid > aside {
    display: flex;
  }
  .temperature {
    font-size: 70px;
  }
  .weather-symbol {
    font-size: 100px;
  }
  .weather-hero {
    padding: 21px;
  }
  .weather-metrics strong {
    font-size: 13px;
  }
  .forecast-grid {
    gap: 16px;
  }
  .hero-main p {
    max-width: 190px;
  }
}
</style>
