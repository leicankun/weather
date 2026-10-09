// Streaming boundaries need not match SSE frames, JSON lines, or UTF-8 characters.
export async function readSse(stream, onEvent) {
  const reader = stream.getReader(),
    decoder = new TextDecoder()
  let buffer = '',
    data = []
  function line(value) {
    if (value === '') {
      if (data.length) {
        const text = data.join('\n')
        data = []
        if (text !== '[DONE]') onEvent(JSON.parse(text))
      }
    } else if (value.startsWith('data:')) data.push(value.slice(5).replace(/^ /, ''))
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
      let index
      while ((index = buffer.indexOf('\n')) !== -1) {
        line(buffer.slice(0, index).replace(/\r$/, ''))
        buffer = buffer.slice(index + 1)
      }
      if (done) {
        if (buffer) line(buffer.replace(/\r$/, ''))
        line('')
        break
      }
    }
  } finally {
    reader.releaseLock()
  }
}
