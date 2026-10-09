<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { createFeedback, listMyFeedback } from '../api/feedback.js'
import { useAuthStore } from '../stores/auth.js'
import { useTask } from '../composables/useTask.js'
import PageTitle from '../components/PageTitle.vue'
import TaskState from '../components/TaskState.vue'
const auth = useAuthStore(),
  content = ref(''),
  contact = ref(''),
  items = ref([]),
  { busy, error, run } = useTask()
async function fetchItems() {
  if (auth.loggedIn) items.value = (await listMyFeedback()).items
}
function load() {
  run(fetchItems)
}
function submit() {
  run(async () => {
    if (!content.value.trim()) throw new Error('请填写反馈内容')
    await createFeedback({ content: content.value.trim(), contact: contact.value || undefined })
    content.value = ''
    await fetchItems()
    ElMessage.success('反馈已提交')
  })
}
onMounted(load)
</script>
<template>
  <PageTitle title="听听你的想法。" description="遇到问题，或有更好的建议，都欢迎告诉我们。" />
  <TaskState :error="error" :busy="busy" @retry="load" />
  <div class="grid-two">
    <section class="panel">
      <el-form label-position="top">
        <el-form-item label="反馈内容">
          <el-input
            v-model="content"
            type="textarea"
            :rows="7"
            placeholder="请描述你遇到的问题或建议…"
          />
        </el-form-item>
        <el-form-item label="联系方式（可选）">
          <el-input v-model="contact" placeholder="邮箱或其他联系方式" />
        </el-form-item>
      </el-form>
      <el-button type="primary" :loading="busy" @click="submit">提交反馈</el-button>
    </section>
    <section class="panel">
      <h2>我的反馈</h2>
      <p v-if="!auth.loggedIn" class="muted">登录后可以查看反馈处理进度。</p>
      <el-empty v-else-if="!items.length && !busy" description="暂无反馈" />
      <div v-for="item in items" :key="item.id" class="list-card">
        <el-tag>
          {{
            { pending: '待处理', processing: '处理中', resolved: '已解决', closed: '已关闭' }[
              item.status
            ]
          }}
        </el-tag>
        <p class="section-gap">{{ item.content }}</p>
        <p v-if="item.reply" class="notice">回复：{{ item.reply }}</p>
      </div>
    </section>
  </div>
</template>
