<script setup>
import { watch, onUnmounted } from 'vue'
import { ElNotification } from 'element-plus'
import { useAuthStore } from '../stores/auth.js'
import { connectAlertStream } from '../api/alertStream.js'
const auth = useAuthStore()
let controller
watch(
  () => auth.token,
  (token) => {
    controller?.abort()
    if (!token) return
    controller = new AbortController()
    connectAlertStream(
      (event) =>
        ElNotification({
          title: event.title || '天气提醒',
          message: event.body || event.content || '收到新的天气预警，请查看通知中心',
          type: 'warning',
          duration: 8000,
        }),
      { signal: controller.signal },
    )
  },
  { immediate: true },
)
onUnmounted(() => controller?.abort())
</script>
<template><span hidden /></template>
