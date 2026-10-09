import { mount } from '@vue/test-utils'
import { it, expect } from 'vitest'
import MarkdownText from './MarkdownText.vue'
it('renders model markdown without executable HTML or javascript links', () => {
  const wrapper = mount(MarkdownText, {
    props: {
      text: '**天气** <img src=x onerror="alert(1)"> [危险](javascript:alert(1)) <script>alert(1)</script>',
    },
  })
  expect(wrapper.find('strong').text()).toBe('天气')
  expect(wrapper.html()).not.toContain('onerror')
  expect(wrapper.html()).not.toContain('javascript:')
  expect(wrapper.find('script').exists()).toBe(false)
})
