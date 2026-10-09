import { test, expect } from '@playwright/test'

const user = { id: 1, username: 'traveler', nickname: '旅行者', email: '', role: 'user' }
const current = {
  temperature: 26,
  feels_like: 27,
  humidity: 65,
  wind_speed: 8,
  weather_desc: '多云',
  time: '2026-10-09T10:00:00',
}
const forecast = [
  { date: '2026-10-10', temp_min: 21, temp_max: 28, weather_desc: '晴', precipitation: 0 },
]
const plan = {
  request: { city: '广州' },
  weather_hint: '',
  area_note: '',
  adjustments: ['下午有雨，已调整为室内活动'],
  plan: {
    summary: '广州周末两日游',
    plan: [
      {
        date: '2026-10-10',
        weather: '小雨',
        weather_note: '已优先安排室内活动',
        items: [
          {
            time: '10:00',
            title: '广东省博物馆',
            activity: '参观',
            reason: '室内活动避雨',
            weather_adjusted: true,
          },
        ],
      },
    ],
  },
}
async function mockApi(page, { loggedIn = true } = {}) {
  const state = {
    items: [],
    preferences: { cold_sensitive: true },
    notificationPrefs: {
      morning_enabled: true,
      morning_hour: 7,
      alert_enabled: true,
      itinerary_enabled: true,
      risk_enabled: true,
    },
    requests: [],
  }
  if (loggedIn)
    await page.addInitScript((u) => {
      localStorage.setItem('wt_token', 'test-token')
      localStorage.setItem('wt_user', JSON.stringify(u))
    }, user)
  await page.route('**/api/v1/**', async (route) => {
    const req = route.request(),
      url = new URL(req.url()),
      p = url.pathname.replace('/api/v1', ''),
      method = req.method()
    const body = method === 'POST' || method === 'PUT' ? req.postDataJSON() : null
    state.requests.push({ p, method, body })
    const ok = (data) => route.fulfill({ json: { code: 0, message: 'ok', data } })
    if (p === '/notifications/stream')
      return route.fulfill({ contentType: 'text/event-stream', body: 'data: {"type":"ping"}\n\n' })
    if (p === '/users/login') return ok({ access_token: 'logged-in', user })
    if (p === '/users/register') return ok(user)
    if (p === '/users/me') return ok(user)
    if (p === '/users/me/preferences') {
      if (method === 'PUT') state.preferences = body
      return ok(state.preferences)
    }
    if (p === '/weather/local')
      return ok({ location: { label: '广州 · 天河区', precision: 'district' }, current, forecast })
    if (p === '/weather/districts') return ok({ items: [{ name: '天河区', has_coords: true }] })
    if (p === '/weather/hourly')
      return ok({
        items: [
          { time: '2026-10-09T11:00:00', temperature: 27, weather_desc: '晴', precip_prob: 10 },
        ],
      })
    if (p === '/weather/aqi') return ok({ us_aqi: 35 })
    if (p === '/home/dashboard')
      return ok({
        tips: [{ title: '适合出行', text: '今日天气舒适' }],
        outfit: { suggestion: '建议穿轻薄外套' },
        itinerary: { upcoming: [] },
        indices: [],
      })
    if (p === '/chat/conversations') return ok({ items: [] })
    if (p === '/chat/stream')
      return route.fulfill({
        contentType: 'text/event-stream',
        body: 'data: {"type":"intent","intent":"weather"}\n\ndata: {"type":"token","content":"广州今天多云，"}\n\ndata: {"type":"token","content":"气温 26℃。"}\n\ndata: {"type":"done"}\n\n',
      })
    if (p === '/recommend/plan') return ok(plan)
    if (p === '/recommend/travel')
      return ok({
        origin: '广州南站',
        destination: '广州塔',
        weather: { desc: '晴', temp_min: 21, temp_max: 28 },
        routes: [
          {
            mode: '地铁',
            duration_min: 40,
            distance_km: 25,
            cost: 7,
            total_score: 0.9,
            detail: '乘坐地铁直达',
          },
        ],
      })
    if (p === '/recommend/outfit')
      return ok({
        city: '广州',
        weather: { desc: '晴', temp: 26 },
        suggestion: '穿轻薄外套和舒适运动鞋',
        rules: ['备好防晒帽'],
      })
    if (p === '/itinerary/batch') {
      state.items.push(...body.items.map((x, i) => ({ ...x, id: i + 1 })))
      return ok({ created: body.items.length, failed: [] })
    }
    if (p === '/itinerary/risks') return ok({ items: [], by_itinerary: {}, total: 0 })
    if (p === '/itinerary') {
      if (method === 'POST') state.items.push({ ...body, id: 1 })
      return ok(
        method === 'POST' ? state.items.at(-1) : { items: state.items, total: state.items.length },
      )
    }
    if (p === '/notifications/prefs') {
      if (method === 'PUT') state.notificationPrefs = body
      return ok(state.notificationPrefs)
    }
    if (p === '/notifications/channels')
      return ok({
        web_push: { configured: false, ready: false },
        email: { configured: false, ready: false, bound_email: '' },
      })
    if (p === '/news/1') return ok({ id: 1, title: '广州天气公告', content: '今天适合出行' })
    if (
      [
        '/news',
        '/notes',
        '/my-knowledge',
        '/notifications/logs',
        '/notifications/subscriptions',
        '/weather/analysis/daily',
        '/feedback',
        '/places/suggest',
      ].includes(p)
    )
      return ok({ items: [], total: 0 })
    return route.fulfill({ status: 404, json: { detail: 'Unmocked API: ' + method + ' ' + p } })
  })
  return state
}

test('weather dashboard renders API fields and works on mobile', async ({ page }) => {
  await mockApi(page, { loggedIn: false })
  await page.goto('/')
  await expect(page.getByText('看看天气，再出发。')).toBeVisible()
  await expect(page.locator('.temperature')).toContainText('26')
  await expect(page.getByText('今日天气舒适')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: '打开导航' }).click()
  await page.getByRole('link', { name: 'AI 出行助手' }).click()
  await expect(page.getByRole('heading', { name: 'AI 出行助手' })).toBeVisible()
})
test('login redirects back to a protected page', async ({ page }) => {
  await mockApi(page, { loggedIn: false })
  await page.goto('/profile')
  await expect(page).toHaveURL(/login/)
  await page.getByPlaceholder('3-64 位字符').fill('traveler')
  await page.getByPlaceholder('至少 6 位').fill('password123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/profile$/)
  await expect(page.getByRole('checkbox', { name: '比较怕冷' })).toBeChecked()
})
test('chat sends authenticated messages and renders streamed tokens', async ({ page }) => {
  const state = await mockApi(page)
  await page.goto('/chat')
  await page.getByRole('textbox', { name: '对话内容' }).fill('广州今天天气如何？')
  await page.getByRole('button', { name: '发送 ↗' }).click()
  await expect(page.locator('.chat-message.assistant')).toContainText('广州今天多云，气温 26℃。')
  expect(state.requests.find((r) => r.p === '/chat/stream').body.message).toBe('广州今天天气如何？')
})
test('generated plans are only saved on an explicit save action', async ({ page }) => {
  const state = await mockApi(page)
  await page.goto('/recommend')
  await page.getByRole('textbox', { name: '行程需求' }).fill('周末去广州玩两天')
  await page.getByRole('button', { name: '生成行程草案' }).click()
  await expect(page.getByText('广东省博物馆', { exact: true })).toBeVisible()
  await expect(page.getByText('下午有雨，已调整为室内活动')).toBeVisible()
  expect(state.items).toHaveLength(0)
  await page.getByRole('button', { name: '保存到我的行程' }).click()
  await expect(page.getByRole('button', { name: '已保存，请到行程列表查看' })).toBeDisabled()
  expect(state.items[0]).toMatchObject({
    title: '广东省博物馆',
    date: '2026-10-10',
    start_time: '10:00',
  })
  await page.getByRole('link', { name: '我的行程', exact: false }).first().click()
  await expect(page.getByRole('heading', { name: '广东省博物馆' })).toBeVisible()
})
test('preferences persist across a page reload', async ({ page }) => {
  const state = await mockApi(page)
  await page.goto('/profile')
  await page.getByText('喜欢拍照', { exact: true }).click()
  await page.getByRole('button', { name: '保存偏好' }).click()
  await expect(page.getByText('出行偏好已保存')).toBeVisible()
  expect(state.preferences.prefer_photo).toBe(true)
  await page.reload()
  await expect(page.getByRole('checkbox', { name: '喜欢拍照' })).toBeChecked()
})
test('weather failures show a retry and recover', async ({ page }) => {
  await mockApi(page)
  let fail = true
  await page.route('**/api/v1/weather/local*', (route) =>
    fail
      ? route.fulfill({ status: 503, json: { detail: '天气服务暂时不可用' } })
      : route.fulfill({
          json: { code: 0, data: { location: { label: '广州' }, current, forecast } },
        }),
  )
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('天气服务暂时不可用')
  fail = false
  await page.getByRole('button', { name: '重试' }).click()
  await expect(page.locator('.temperature')).toContainText('26')
})
test('route and outfit forms use their respective API results', async ({ page }) => {
  await mockApi(page)
  await page.goto('/recommend')
  await page.getByRole('tab', { name: '路线推荐' }).click()
  await page.getByPlaceholder('例如：广州南站').fill('广州南站')
  await page.getByPlaceholder('例如：广州塔').fill('广州塔')
  await page.getByRole('button', { name: '查询出行方案' }).click()
  await expect(page.getByText('乘坐地铁直达')).toBeVisible()
  await page.getByRole('tab', { name: '穿搭建议' }).click()
  await page.getByRole('button', { name: '获取今日穿搭建议' }).click()
  await expect(page.getByText('穿轻薄外套和舒适运动鞋')).toBeVisible()
})
test('all remaining routes load without runtime exceptions', async ({ page }) => {
  await mockApi(page)
  const errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  for (const path of [
    '/notes',
    '/news',
    '/news/1',
    '/analysis',
    '/my-knowledge',
    '/notifications',
    '/feedback',
  ]) {
    await page.goto(path)
    await expect(page.locator('main')).toBeVisible()
    await expect(page.locator('main h1')).toBeVisible()
  }
  expect(errors).toEqual([])
})
