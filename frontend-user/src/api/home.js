import http from './http'
export async function getDashboard(city) {
  const res = await http.get('/home/dashboard', {
    params: { city: city ?? undefined },
  })
  return res
}
