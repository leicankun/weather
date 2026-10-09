import { it, expect } from 'vitest'
import { formatWeatherTime } from './datetime.js'
it('shows UTC observations in China time instead of dropping their timezone', () => {
  expect(formatWeatherTime('2026-10-09T06:45:00+00:00')).toContain('14:45')
  expect(formatWeatherTime('2026-10-09T14:45:00+08:00')).toContain('14:45')
  expect(formatWeatherTime('2026-10-09T14:45:00')).toContain('14:45')
})
it('does not render invalid or missing timestamps as a date', () => {
  expect(formatWeatherTime(null)).toBe('以接口更新时间为准')
  expect(formatWeatherTime('bad')).toBe('更新时间未知')
})
