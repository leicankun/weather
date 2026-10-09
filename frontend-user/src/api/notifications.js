import http from './http'
/** 获取 VAPID 公钥（subscribe 的 applicationServerKey） */
export async function getVapidKey() {
  const data = await http.get('/notifications/vapid-key')
  return data.publicKey
}
/** 保存推送订阅（endpoint 为唯一键，重复订阅会刷新） */
export async function saveSubscription(payload) {
  await http.post('/notifications/subscriptions', payload)
}
/** 取消订阅 */
export async function removeSubscription(endpoint) {
  await http.post('/notifications/subscriptions/unsubscribe', { endpoint })
}
/** 发送测试通知（走全部可用通道） */
export async function sendTestNotification() {
  const data = await http.post('/notifications/test')
  return data
}
/** 当前用户的通知发送记录 */
export async function getNotificationLogs() {
  const data = await http.get('/notifications/logs', { params: { page_size: 10 } })
  return data.items
}
// ===== 每日早报偏好（Day 35）=====
/** 查询早报偏好（未设置过时后端返回默认值：开启、7 点） */
export async function getMorningReport() {
  return await http.get('/notifications/morning-report')
}
/** 设置早报开关与推送小时（关闭后分发任务不再推送该用户） */
export async function updateMorningReport(enabled, hour) {
  await http.put('/notifications/morning-report', { enabled, hour })
}
/** 读取通知偏好（未设置过时后端返回默认值：全开、7 点） */
export async function getPrefs() {
  return await http.get('/notifications/prefs')
}
/** 部分更新通知偏好：只传要改的字段，避免把另一个开关覆盖回旧值 */
export async function updatePrefs(payload) {
  return await http.put('/notifications/prefs', payload)
}
/** 当前账号的推送订阅（一台设备一条） */
export async function listSubscriptions() {
  const data = await http.get('/notifications/subscriptions')
  return data.items
}
/** 退订指定设备（按订阅 id，只影响这一台） */
export async function deleteSubscription(id) {
  await http.delete(`/notifications/subscriptions/${id}`)
}
/**
 * 查询两条通知通道的就绪状态。
 * 前端据此给出「去绑定邮箱 / 去开启推送」的明确引导，
 * 而不是让用户从发送记录的「用户未绑定邮箱」里自己猜原因。
 */
export async function getChannelStatus() {
  return await http.get('/notifications/channels')
}
