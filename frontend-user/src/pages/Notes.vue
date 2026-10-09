<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import {
  listNotes,
  createNote,
  updateNote,
  deleteNote,
  importNoteFile,
  exportAllNotes,
  exportNoteMd,
} from '../api/notes.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
import MarkdownText from '../components/MarkdownText.vue'
const rows = ref([]),
  keyword = ref(''),
  dialog = ref(false),
  editing = ref(null),
  form = reactive({ title: '', content: '', note_date: null, location: '' }),
  { busy, error, run } = useTask()
async function fetchRows() {
  rows.value = (await listNotes(keyword.value)).items
}
function load() {
  run(fetchRows)
}
function edit(row) {
  editing.value = row?.id || null
  Object.assign(form, { title: '', content: '', note_date: null, location: '' }, row || {})
  dialog.value = true
}
function save() {
  run(async () => {
    if (!form.title.trim() || !form.content.trim()) throw new Error('请填写标题和内容')
    const payload = {
      title: form.title,
      content: form.content,
      note_date: form.note_date,
      location: form.location || null,
    }
    if (editing.value) await updateNote(editing.value, payload)
    else await createNote(payload)
    dialog.value = false
    await fetchRows()
    ElMessage.success('笔记已保存')
  })
}
async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除「${row.title}」？`, '删除笔记', { type: 'warning' })
  } catch {
    return
  }
  run(async () => {
    await deleteNote(row.id)
    await fetchRows()
  })
}
function download(blob, name) {
  const url = URL.createObjectURL(blob),
    a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function exportAll() {
  run(async () =>
    download(
      new Blob([JSON.stringify(await exportAllNotes(), null, 2)], { type: 'application/json' }),
      '旅行笔记.json',
    ),
  )
}
function exportOne(row) {
  run(async () => download(await exportNoteMd(row.id), row.title + '.md'))
}
function pick(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (file)
    run(async () => {
      const r = await importNoteFile(file)
      await fetchRows()
      ElMessage.success(`已导入 ${r.created} 篇笔记`)
    })
}
onMounted(load)
</script>
<template>
  <PageTitle title="旅行笔记" description="攻略、清单和灵感，随时记下，随时带走。">
    <el-button @click="exportAll">导出全部</el-button>
    <el-button type="primary" @click="edit()">＋ 新建笔记</el-button>
  </PageTitle>
  <TaskState :error="error" :busy="busy" @retry="load" />
  <section class="panel">
    <div class="panel-title">
      <div class="actions">
        <el-input
          v-model="keyword"
          placeholder="搜索笔记"
          style="width: 220px"
          @keyup.enter="load"
        />
        <el-button @click="load">搜索</el-button>
      </div>
      <label class="file-label">
        导入笔记
        <input type="file" accept=".txt,.md,.json" @change="pick" />
      </label>
    </div>
    <el-empty v-if="!rows.length && !busy" description="还没有笔记" />
    <article v-for="row in rows" :key="row.id" class="list-card">
      <div class="panel-title">
        <h2>{{ row.title }}</h2>
        <div class="actions">
          <el-button text @click="edit(row)">编辑</el-button>
          <el-button text @click="exportOne(row)">导出</el-button>
          <el-button text type="danger" @click="remove(row)">删除</el-button>
        </div>
      </div>
      <p class="muted">{{ row.note_date }} {{ row.location }}</p>
      <MarkdownText :text="row.content" />
    </article>
  </section>
  <el-dialog v-model="dialog" :title="editing ? '编辑笔记' : '新建笔记'" width="720px">
    <el-form label-position="top">
      <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="内容（支持 Markdown）">
        <el-input v-model="form.content" type="textarea" :rows="12" />
      </el-form-item>
      <div class="form-grid">
        <el-form-item label="日期">
          <el-date-picker v-model="form.note_date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="地点"><el-input v-model="form.location" /></el-form-item>
      </div>
    </el-form>
    <p v-if="error" role="alert">{{ error }}</p>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="busy" @click="save">保存笔记</el-button>
    </template>
  </el-dialog>
</template>
