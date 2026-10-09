import http from './http'
// 列表（keyword 同时匹配标题与正文）
export async function listNotes(keyword) {
  const res = await http.get('/notes', {
    params: keyword ? { keyword } : {},
  })
  return res ?? { items: [], total: 0 }
}
export async function getNote(id) {
  return http.get(`/notes/${id}`)
}
export async function createNote(payload) {
  return http.post('/notes', payload)
}
export async function updateNote(id, payload) {
  return http.put(`/notes/${id}`, payload)
}
export async function deleteNote(id) {
  return http.delete(`/notes/${id}`)
}
// 上传文件导入（.md / .txt 单篇；.json 支持批量）
export async function importNoteFile(file) {
  const form = new FormData()
  form.append('file', file)
  return http.post('/notes/import', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}
// 粘贴文本导入（标题可空，后端自动推断）
export async function importNoteText(payload) {
  return http.post('/notes/import-text', payload)
}
// 导出单篇为 .md（浏览器下载）
export async function exportNoteMd(id) {
  return await http.get(`/notes/${id}/export`, {
    responseType: 'blob',
  })
}
// 导出全部为 JSON（可再次导入）
export async function exportAllNotes() {
  return await http.get('/notes/export/all')
}
