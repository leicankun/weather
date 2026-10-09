<script setup>
import { ref, reactive } from 'vue'
import { recommendTravel, recommendOutfit, getOutfitPosts } from '../api/recommend.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
import PlaceInput from '../components/PlaceInput.vue'
import TripPlanner from '../components/TripPlanner.vue'
const tab = ref('plan'),
  form = reactive({ origin: '', destination: '', city: '广州', scene: '通勤', preference: '' })
const travel = ref(null),
  outfit = ref(null),
  ideas = ref(null)
const { busy, error, run } = useTask()
const selectedPlaces = reactive({ origin: null, destination: null })
function route() {
  run(async () => {
    if (!form.origin.trim() || !form.destination.trim()) throw new Error('请填写出发地和目的地')
    const coords = {}
    for (const field of ['origin', 'destination']) {
      const place = selectedPlaces[field]
      if (place?.value === form[field] && place.lng != null && place.lat != null) {
        coords[`${field}_lng`] = place.lng
        coords[`${field}_lat`] = place.lat
      }
    }
    travel.value = await recommendTravel(form.origin, form.destination, form.city, coords)
    if (travel.value.error) throw new Error(travel.value.error)
  })
}
function dress() {
  run(async () => {
    outfit.value = await recommendOutfit(form.city, form.scene, form.preference)
    ideas.value = null
  })
}
function inspiration() {
  run(async () => {
    ideas.value = await getOutfitPosts(form.city, form.scene, form.preference)
  })
}
</script>
<template>
  <PageTitle
    title="为出发，做一点准备。"
    description="从一条路线到一份行程，把天气纳入你的计划。"
  />
  <el-tabs v-model="tab">
    <el-tab-pane label="AI 行程规划" name="plan"><TripPlanner /></el-tab-pane>
    <el-tab-pane label="路线推荐" name="route">
      <section class="panel">
        <el-form label-position="top" class="form-grid" @submit.prevent="route">
          <el-form-item label="出发地">
            <PlaceInput
              v-model="form.origin"
              placeholder="例如：广州南站"
              @update:model-value="selectedPlaces.origin = null"
              @select="selectedPlaces.origin = $event"
            />
          </el-form-item>
          <el-form-item label="目的地">
            <PlaceInput
              v-model="form.destination"
              placeholder="例如：广州塔"
              @update:model-value="selectedPlaces.destination = null"
              @select="selectedPlaces.destination = $event"
            />
          </el-form-item>
          <div class="actions wide">
            <el-button type="primary" :loading="busy" @click="route">查询出行方案</el-button>
          </div>
        </el-form>
        <TaskState :error="error" @retry="route" />
        <template v-if="travel">
          <p class="notice section-gap">
            {{ travel.origin }} → {{ travel.destination }} · {{ travel.weather?.desc }}
            {{ travel.weather?.temp_min }}～{{ travel.weather?.temp_max }}℃
          </p>
          <p v-if="travel.hint" class="notice">{{ travel.hint }}</p>
          <div v-for="(r, i) in travel.routes" :key="i" class="list-card">
            <div class="panel-title">
              <h3>{{ r.mode }}</h3>
              <el-tag v-if="i === 0" type="success">推荐方案</el-tag>
            </div>
            <p>{{ r.duration_min }} 分钟 · {{ r.distance_km }} 公里 · 约 ¥{{ r.cost }}</p>
            <p class="muted">{{ r.detail }}</p>
            <small class="muted">综合评分 {{ r.total_score ?? '—' }}</small>
          </div>
        </template>
      </section>
    </el-tab-pane>
    <el-tab-pane label="穿搭建议" name="outfit">
      <section class="panel">
        <el-form label-position="top" class="form-grid">
          <el-form-item label="活动场景">
            <el-select v-model="form.scene">
              <el-option
                v-for="s in ['通勤', '爬山', '逛街', '露营', '骑行', '夜游', '商务']"
                :key="s"
                :label="s"
                :value="s"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="个人偏好">
            <el-select v-model="form.preference" clearable placeholder="选择偏好">
              <el-option
                v-for="s in ['怕冷', '怕热', '运动', '休闲', '正式']"
                :key="s"
                :label="s"
                :value="s"
              />
            </el-select>
          </el-form-item>
        </el-form>
        <el-button type="primary" :loading="busy" @click="dress">获取今日穿搭建议</el-button>
        <TaskState :error="error" @retry="dress" />
        <div v-if="outfit" class="section-gap">
          <p class="notice">
            {{ outfit.city }} · {{ outfit.weather?.desc }} · {{ outfit.weather?.temp }}℃
          </p>
          <p>{{ outfit.suggestion }}</p>
          <ul>
            <li v-for="r in outfit.rules" :key="r">{{ r }}</li>
          </ul>
          <p v-if="outfit.preference_rule">{{ outfit.preference_rule }}</p>
          <el-button :loading="busy" @click="inspiration">寻找穿搭灵感</el-button>
          <div v-if="ideas" class="section-gap">
            <div v-for="post in ideas.posts" :key="post.url" class="list-card">
              <a :href="post.url" target="_blank" rel="noopener noreferrer">{{ post.title }} ↗</a>
              <p class="muted">{{ post.snippet }}</p>
            </div>
            <div class="actions">
              <a
                v-for="p in ideas.portals"
                :key="p.url"
                :href="p.url"
                target="_blank"
                rel="noopener noreferrer"
                class="map-link"
              >
                {{ p.title }} ↗
              </a>
            </div>
          </div>
        </div>
      </section>
    </el-tab-pane>
  </el-tabs>
</template>
