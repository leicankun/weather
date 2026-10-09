import { beforeEach, afterEach, it, expect, vi } from 'vitest'
import http from './http.js'
const adapter = http.defaults.adapter
beforeEach(() => localStorage.clear())
afterEach(() => {
  http.defaults.adapter = adapter
})
it('sends JWT and unwraps exactly one backend response envelope', async () => {
  localStorage.setItem('wt_token', 'test-token')
  http.defaults.adapter = async (config) => {
    expect(config.headers.Authorization).toBe('Bearer test-token')
    return {
      status: 200,
      config,
      data: { code: 0, data: { data: 'business field', cold_sensitive: true } },
    }
  }
  expect(await http.get('/users/me/preferences')).toEqual({
    data: 'business field',
    cold_sensitive: true,
  })
})
it('rejects business failures', async () => {
  http.defaults.adapter = async (config) => ({
    status: 200,
    config,
    data: { code: 1, message: '保存失败' },
  })
  await expect(http.put('/itinerary', {})).rejects.toThrow('保存失败')
})
it('clears all account state after an expired token', async () => {
  localStorage.setItem('wt_token', 'expired')
  localStorage.setItem('wt_user', '{"id":1}')
  const listener = vi.fn()
  window.addEventListener('auth-expired', listener)
  http.defaults.adapter = async (config) => {
    throw { config, response: { status: 401 } }
  }
  await expect(http.get('/users/me')).rejects.toBeTruthy()
  expect(localStorage.getItem('wt_token')).toBeNull()
  expect(localStorage.getItem('wt_user')).toBeNull()
  expect(listener).toHaveBeenCalledOnce()
  window.removeEventListener('auth-expired', listener)
})
