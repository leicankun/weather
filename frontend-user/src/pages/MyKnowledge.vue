<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http from '../api/http.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const docs = ref([]),
  hits = ref(null),
  query = ref(''),
  form = reactive({ title: '', content: '' })
const { busy, error, run } = useTask()
async function fetchDocs() {
  docs.value = (await http.get('/my-knowledge')).items || []
}
function load() {
  run(fetchDocs)
}
function upload() {
  run(async () => {
    if (!form.title.trim() || !form.content.trim()) throw new Error('请填写标题和内容')
    await http.post('/my-knowledge', { title: form.title.trim(), content: form.content.trim() })
    form.title = ''
    form.content = ''
    await fetchDocs()
    ElMessage.success('已加入个人知识库')
  })
}
function pick(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (file)
    run(async () => {
      if (file.size > 512 * 1024) throw new Error('文件上限为 512 KB，请拆分后上传')
      form.content = await file.text()
      if (!form.title) form.title = file.name.replace(/\.[^.]+$/, '')
    })
}
function search() {
  run(async () => {
    if (!query.value.trim()) return
    hits.value =
      (await http.post('/my-knowledge/search', { query: query.value, top_k: 5 })).items || []
  })
}
async function remove(title) {
  try {
    await ElMessageBox.confirm(`删除「${title}」？`, '删除知识文档', { type: 'warning' })
  } catch {
    return
  }
  run(async () => {
    await http.delete('/my-knowledge/' + encodeURIComponent(title))
    hits.value = null
    await fetchDocs()
  })
}
onMounted(load)
</script>
<template>
  <PageTitle
    title="个人知识库"
    description="收藏攻略、旅行计划和笔记，出行助手会结合你的资料回答。"
  />
  <TaskState :error="error" :busy="busy" @retry="load" />
  <div class="grid-two">
    <section class="panel">
      <h2>加入一份资料</h2>
      <el-form label-position="top">
        <el-form-item label="标题">
          <el-input v-model="form.title" maxlength="80" placeholder="例如：我的广州周末攻略" />
        </el-form-item>
        <el-form-item label="内容">
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="10"
            placeholder="粘贴攻略、笔记或旅行计划…"
          />
        </el-form-item>
      </el-form>
      <div class="actions">
        <el-button type="primary" :loading="busy" @click="upload">加入知识库</el-button>
        <label class="file-label">
          导入文件
          <input type="file" accept=".txt,.md,.markdown" @change="pick" />
        </label>
      </div>
    </section>
    <div class="stack">
      <section class="panel">
        <h2>检索我的资料</h2>
        <div class="actions">
          <el-input
            v-model="query"
            placeholder="例如：我收藏的餐厅有哪些？"
            style="flex: 1"
            @keyup.enter="search"
          />
          <el-button :loading="busy" @click="search">检索</el-button>
        </div>
        <div v-for="(hit, i) in hits || []" :key="i" class="list-card">
          <h3>{{ hit.title }}</h3>
          <p class="small">{{ hit.content }}</p>
          <small class="muted">相关度 {{ hit.similarity }}</small>
        </div>
        <p v-if="hits?.length === 0" class="empty-message">没有找到相关资料</p>
      </section>
      <section class="panel">
        <h2>已收藏 · {{ docs.length }}</h2>
        <el-empty v-if="!docs.length && !busy" description="还没有资料" :image-size="65" />
        <div v-for="d in docs" :key="d.title" class="list-card panel-title">
          <div>
            <h3>{{ d.title }}</h3>
            <small class="muted">{{ d.chunk_count }} 个片段</small>
          </div>
          <el-button text type="danger" @click="remove(d.title)">删除</el-button>
        </div>
      </section>
    </div>
  </div>
</template>
