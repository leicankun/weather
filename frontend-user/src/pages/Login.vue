<script setup>
import { ref, reactive } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { login, register } from '../api/auth.js'
import { useAuthStore } from '../stores/auth.js'
import { useTask } from '../composables/useTask.js'
import TaskState from '../components/TaskState.vue'
const mode = ref('login'),
  form = reactive({ username: '', password: '', nickname: '' })
const auth = useAuthStore(),
  route = useRoute(),
  router = useRouter()
const { busy, error, run } = useTask()
function submit() {
  run(async () => {
    if (form.username.trim().length < 3 || form.password.length < 6)
      throw new Error('用户名至少 3 位，密码至少 6 位')
    if (mode.value === 'register') await register({ ...form, username: form.username.trim() })
    auth.accept(await login(form.username.trim(), form.password))
    const redirect = route.query.redirect
    router.replace(
      typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')
        ? redirect
        : '/',
    )
  })
}
</script>
<template>
  <div class="login-wrap">
    <section class="panel login-panel">
      <span class="muted">WELCOME TO 知天气</span>
      <h1>{{ mode === 'login' ? '欢迎回来。' : '开启你的出行计划。' }}</h1>
      <p class="muted">登录后，保存你的对话、偏好与每一次出发。</p>
      <el-segmented
        v-model="mode"
        :options="[
          { label: '登录', value: 'login' },
          { label: '注册', value: 'register' },
        ]"
      />
      <div class="section-gap">
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名">
            <el-input
              v-model="form.username"
              name="username"
              autocomplete="username"
              placeholder="3-64 位字符"
              maxlength="64"
            />
          </el-form-item>
          <el-form-item v-if="mode === 'register'" label="昵称">
            <el-input v-model="form.nickname" placeholder="怎么称呼你" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              name="password"
              type="password"
              show-password
              :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
              placeholder="至少 6 位"
            />
          </el-form-item>
          <TaskState :error="error" @retry="submit" />
          <el-button type="primary" native-type="submit" :loading="busy" style="width: 100%">
            {{ mode === 'login' ? '登录' : '注册并登录' }}
          </el-button>
        </el-form>
      </div>
    </section>
  </div>
</template>
<style scoped>
.login-wrap {
  display: grid;
  place-items: center;
  min-height: 65vh;
}
.login-panel {
  width: min(440px, 100%);
  padding: 38px;
}
.login-panel h1 {
  margin-top: 23px;
}
.login-panel > p {
  margin-bottom: 28px;
}
</style>
