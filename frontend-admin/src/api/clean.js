import http from './http'
export function listCleanTasks(limit = 20) {
  return http.get('/clean', { params: { limit } })
}
export function getCleanTask(taskId) {
  return http.get(`/clean/${taskId}`)
}
// 上传 CSV 触发清洗（FormData）
export function uploadCsv(file) {
  const fd = new FormData()
  fd.append('file', file)
  return http.post('/clean/upload', fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
// 下载清洗后 CSV（用带鉴权 header 的 axios blob）
export async function downloadClean(taskId, filename) {
  const resp = await http.get(`/clean/${taskId}/download`, { responseType: 'blob' })
  const blob = resp
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || `cleaned_${taskId}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
