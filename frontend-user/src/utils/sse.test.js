import { describe, it, expect } from 'vitest'
import { readSse } from './sse.js'
function stream(bytes, size = 1) {
  return new ReadableStream({
    start(c) {
      for (let i = 0; i < bytes.length; i += size) c.enqueue(bytes.slice(i, i + size))
      c.close()
    },
  })
}
describe('SSE decoding', () => {
  it('handles byte-split Chinese text, CRLF and a final event without a newline', async () => {
    const text =
      ': keepalive\r\nevent: message\r\ndata: {"type":"token","content":"广州☀"}\r\n\r\ndata: {"type":"done"}'
    const result = []
    await readSse(stream(new TextEncoder().encode(text)), (e) => result.push(e))
    expect(result).toEqual([{ type: 'token', content: '广州☀' }, { type: 'done' }])
  })
  it('supports data spanning multiple lines and ignores the DONE sentinel', async () => {
    const result = []
    await readSse(
      stream(
        new TextEncoder().encode(
          'data: {"type":"token",\ndata: "content":"天气"}\n\ndata: [DONE]\n\n',
        ),
        7,
      ),
      (e) => result.push(e),
    )
    expect(result).toEqual([{ type: 'token', content: '天气' }])
  })
  it('does not silently swallow server errors raised by the consumer', async () => {
    await expect(
      readSse(stream(new TextEncoder().encode('data: {"type":"error"}\n\n')), () => {
        throw new Error('上游模型不可用')
      }),
    ).rejects.toThrow('上游模型不可用')
  })
})
