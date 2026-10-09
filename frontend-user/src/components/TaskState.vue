<script setup>
defineProps({ error: String, busy: Boolean })
defineEmits(['retry'])
</script>
<template>
  <div v-if="error" class="error-state" role="alert">
    <span>{{ error }}</span>
    <el-button text @click="$emit('retry')">重试</el-button>
  </div>
  <p v-else-if="busy" class="muted" role="status">正在加载，请稍候…</p>
</template>
