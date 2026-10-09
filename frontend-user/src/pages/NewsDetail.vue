<script setup>
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getNews } from '../api/news.js'
import { useTask } from '../composables/useTask.js'
import TaskState from '../components/TaskState.vue'
import MarkdownText from '../components/MarkdownText.vue'
const route = useRoute(),
  item = ref(null),
  { busy, error, run } = useTask()
function load() {
  run(async () => {
    item.value = await getNews(Number(route.params.id))
  })
}
watch(() => route.params.id, load, { immediate: true })
</script>
<template>
  <RouterLink class="map-link" to="/news">← 返回资讯</RouterLink>
  <TaskState :error="error" :busy="busy" @retry="load" />
  <article v-if="item" class="panel section-gap">
    <h1>{{ item.title }}</h1>
    <p class="muted">{{ item.author }} · {{ item.created_at?.slice(0, 10) }}</p>
    <MarkdownText :text="item.full_text || item.content" />
    <a
      v-if="/^https?:\/\//i.test(item.source_url || '')"
      class="map-link"
      :href="item.source_url"
      target="_blank"
      rel="noopener noreferrer"
    >
      阅读原文 ↗
    </a>
  </article>
</template>
