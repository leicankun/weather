<script setup>
import { reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '../stores/auth.js'
import { updateProfile } from '../api/auth.js'
import { getMyPreferences, updateMyPreferences, PREFERENCE_DEFAULTS } from '../api/preferences.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const auth = useAuthStore(),
  profile = reactive({ nickname: auth.user?.nickname || '', email: auth.user?.email || '' }),
  prefs = reactive({ ...PREFERENCE_DEFAULTS })
const flags = [
  ['sun_sensitive', '注意防晒'],
  ['cold_sensitive', '比较怕冷'],
  ['with_elderly', '与长辈同行'],
  ['with_children', '带小朋友'],
  ['with_pet', '带宠物'],
  ['prefer_outdoor', '喜欢户外'],
  ['prefer_indoor', '喜欢室内'],
  ['prefer_photo', '喜欢拍照'],
  ['prefer_food', '喜欢美食'],
  ['budget_low', '优先节省预算'],
]
const { busy, error, run } = useTask()
function load() {
  run(async () => {
    await auth.refresh()
    Object.assign(profile, { nickname: auth.user.nickname || '', email: auth.user.email || '' })
    Object.assign(prefs, await getMyPreferences())
  })
}
function saveProfile() {
  run(async () => {
    if (profile.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(profile.email))
      throw new Error('请输入有效邮箱')
    await updateProfile({ nickname: profile.nickname, email: profile.email || null })
    await auth.refresh()
    ElMessage.success('个人资料已保存')
  })
}
function savePrefs() {
  run(async () => {
    Object.assign(prefs, await updateMyPreferences({ ...prefs }))
    ElMessage.success('出行偏好已保存')
  })
}
onMounted(load)
</script>
<template>
  <PageTitle
    title="更了解你，才能更好地推荐。"
    description="设置你的出行偏好，助手会在后续对话中参考这些信息。"
  />
  <TaskState :error="error" :busy="busy" @retry="load" />
  <div class="grid-two">
    <section class="panel">
      <h2>个人资料</h2>
      <p class="muted">账号：{{ auth.user?.username }}</p>
      <el-form label-position="top">
        <el-form-item label="昵称">
          <el-input v-model="profile.nickname" maxlength="64" />
        </el-form-item>
        <el-form-item label="通知邮箱">
          <el-input v-model="profile.email" type="email" placeholder="用于接收天气和行程通知" />
        </el-form-item>
      </el-form>
      <el-button type="primary" :loading="busy" @click="saveProfile">保存资料</el-button>
      <p class="muted section-gap">通知能否投递，还取决于通知中心显示的通道状态。</p>
      <RouterLink class="map-link" to="/notifications">管理通知设置 ↗</RouterLink>
    </section>
    <section class="panel">
      <h2>出行偏好</h2>
      <div class="preference-grid">
        <el-checkbox v-for="[key, label] in flags" :key="key" v-model="prefs[key]">
          {{ label }}
        </el-checkbox>
      </div>
      <el-form label-position="top" class="section-gap">
        <el-form-item label="常用交通方式">
          <el-select v-model="prefs.commute" clearable>
            <el-option
              v-for="s in ['地铁', '公交', '自驾', '步行', '骑行']"
              :key="s"
              :label="s"
              :value="s"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="饮食注意">
          <el-input v-model="prefs.diet" placeholder="例如：不吃辣、素食" maxlength="200" />
        </el-form-item>
      </el-form>
      <div class="actions">
        <el-button type="primary" :loading="busy" @click="savePrefs">保存偏好</el-button>
        <el-button @click="Object.assign(prefs, PREFERENCE_DEFAULTS)">重置表单</el-button>
      </div>
    </section>
  </div>
</template>
<style scoped>
.preference-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
</style>
