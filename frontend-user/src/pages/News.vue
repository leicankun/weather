<script setup>
import { ref, onMounted } from 'vue'
import { listNews } from '../api/news.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const category = ref(''),
  keyword = ref(''),
  page = ref(1),
  total = ref(0),
  items = ref([])
const { busy, error, run } = useTask()
function load() {
  run(async () => {
    const d = await listNews({
      category: category.value || undefined,
      keyword: keyword.value,
      page: page.value,
      page_size: 12,
    })
    items.value = d.items
    total.value = d.total
  })
}
function search() {
  page.value = 1
  load()
}
onMounted(load)
</script>
<template>
  <PageTitle title="天气资讯" description="了解最新天气动态，让出行多一份准备。" />
  <section class="panel">
    <div class="panel-title">
      <el-segmented
        v-model="category"
        :options="[
          { label: '全部', value: '' },
          { label: '资讯', value: 'news' },
          { label: '公告', value: 'notice' },
          { label: '预警', value: 'alert' },
        ]"
        @change="search"
      />
      <div class="actions">
        <el-input
          v-model="keyword"
          placeholder="搜索资讯"
          style="width: 180px"
          @keyup.enter="search"
        />
        <el-button @click="search">搜索</el-button>
      </div>
    </div>
    <TaskState :error="error" :busy="busy" @retry="load" />
    <el-empty v-if="!items.length && !busy" description="暂时没有相关资讯" />
    <article v-for="item in items" :key="item.id" class="list-card">
      <RouterLink :to="'/news/' + item.id">
        <h2>{{ item.title }} ↗</h2>
      </RouterLink>
      <p class="muted">{{ item.content?.slice(0, 160) }}</p>
      <small class="muted">
        {{ item.created_at?.slice(0, 10) }} · {{ item.author || '天气资讯' }}
      </small>
    </article>
    <el-pagination
      v-if="total > 12"
      v-model:current-page="page"
      :total="total"
      :page-size="12"
      layout="prev,pager,next"
      class="section-gap"
      @current-change="load"
    />
  </section>
</template>
