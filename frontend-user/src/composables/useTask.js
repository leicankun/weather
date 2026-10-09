import { ref } from 'vue'
import { apiErrorText } from '../utils/apiError.js'
export function useTask() {
  const busy = ref(false),
    error = ref('')
  async function run(action) {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try {
      return await action()
    } catch (err) {
      error.value = apiErrorText(err, '服务暂时不可用，请稍后重试')
    } finally {
      busy.value = false
    }
  }
  return { busy, error, run }
}
