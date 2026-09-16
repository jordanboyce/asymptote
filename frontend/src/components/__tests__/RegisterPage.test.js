import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import http from '../../utils/http'
import RegisterPage from '../RegisterPage.vue'

async function mountWith(config) {
  http.get.mockResolvedValueOnce({ data: config })
  const wrapper = mount(RegisterPage)
  await flushPromises()
  return wrapper
}

describe('RegisterPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    http.get.mockReset()
    http.post.mockReset()
  })

  it('says registration is closed when the deployment reports off', async () => {
    const w = await mountWith({ enabled: false, mode: 'off', allowed_domains: [] })
    expect(w.text()).toContain('Registration is closed')
    expect(w.find('form').exists()).toBe(false)
    expect(w.find('a[href="/"]').exists()).toBe(true)
  })

  it('renders the form with the domain hint and submits a request', async () => {
    const w = await mountWith({ enabled: true, mode: 'approval', allowed_domains: ['example.com'] })
    expect(w.text()).toContain('@example.com addresses')
    expect(w.find('button[type="submit"]').text()).toContain('Request access')

    await w.find('#reg-email').setValue('Person@Example.com')
    await w.find('#reg-name').setValue('Person')
    http.post.mockResolvedValueOnce({
      data: { status: 'pending', email: 'person@example.com', message: 'Thanks — sent for review.' },
    })
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/api/register', expect.objectContaining({
      email: 'person@example.com', name: 'Person', website: '',
    }))
    expect(w.text()).toContain('Request received')
    expect(w.text()).toContain('sent for review')
  })

  it('shows a sign-in action when open mode admits immediately', async () => {
    const w = await mountWith({ enabled: true, mode: 'open', allowed_domains: [] })
    expect(w.find('button[type="submit"]').text()).toContain('Get access')
    await w.find('#reg-email').setValue('a@b.co')
    http.post.mockResolvedValueOnce({ data: { status: 'approved', email: 'a@b.co', message: "You're in." } })
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain("You're in")
    expect(w.find('a.btn-primary[href="/"]').exists()).toBe(true)
  })

  it('validates the address before calling the API and surfaces server errors', async () => {
    const w = await mountWith({ enabled: true, mode: 'approval', allowed_domains: [] })
    await w.find('#reg-email').setValue('nope')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(http.post).not.toHaveBeenCalled()
    expect(w.text()).toContain('Enter a valid email address')

    await w.find('#reg-email').setValue('x@y.zz')
    http.post.mockRejectedValueOnce({ status: 400, message: 'Registration is limited to addresses at example.com.' })
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('limited to addresses at example.com')

    http.post.mockRejectedValueOnce({ status: 429, isRateLimit: true, message: 'slow down' })
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('Too many attempts')
  })
})
