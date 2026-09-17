import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import MCPTab from '../MCPTab.vue'
import http from '../../utils/http'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

const existingToken = { id: 'existing', name: 'Other device' }

beforeEach(() => {
  vi.resetAllMocks()
  http.get.mockImplementation(async (path) => ({ data: {
    '/api/collections': { collections: [{ id: 'research & notes', name: 'Research' }] },
    '/api/mcp/config': { enable_mcp: true },
    '/api/mcp/tokens': { tokens: [existingToken] },
  }[path] }))
})

async function openAnythingLLM() {
  const wrapper = mount(MCPTab, { global: { plugins: [createPinia()] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'AnythingLLM').trigger('click')
  return wrapper
}

function exportedServer(wrapper) {
  return Object.values(JSON.parse(wrapper.get('pre').text()).mcpServers)[0]
}

describe('MCP client setup', () => {
  it('exports AnythingLLM Streamable HTTP configuration with an encoded collection and no invented credential', async () => {
    const wrapper = await openAnythingLLM()
    const server = exportedServer(wrapper)
    expect(server.type).toBe('streamable')
    expect(new URL(server.url).searchParams.get('collection_id')).toBe('research & notes')
    expect(server.headers.Authorization).toContain('Bearer <PASTE_YOUR_TOKEN')
    expect(server.command).toBeUndefined()
    expect(wrapper.text()).toContain('anythingllm_mcp_servers.json')
    expect(wrapper.text()).toContain('storage/plugins')
    wrapper.unmount()
  })

  it('creates a read-only token and never substitutes its secret for another device token', async () => {
    const wrapper = await openAnythingLLM()
    http.post.mockResolvedValue({ data: { id: 'new', token: 'test-only-secret' } })
    http.get.mockResolvedValue({ data: { tokens: [existingToken, { id: 'new', name: 'AnythingLLM' }] } })
    await wrapper.get('#mcp-token-name').setValue('AnythingLLM')
    await wrapper.findAll('button').find(button => button.text() === 'Generate token').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/mcp/tokens', {
      name: 'AnythingLLM', collection_scope: [], can_write: false, expires_in_days: 90, allowlist: false,
    })
    expect(exportedServer(wrapper).headers.Authorization).toBe('Bearer test-only-secret')
    await wrapper.get('#mcp-export-token').setValue('existing')
    expect(wrapper.get('pre').text()).not.toContain('test-only-secret')
    expect(exportedServer(wrapper).headers.Authorization).toContain('Bearer <PASTE_YOUR_TOKEN')
    wrapper.unmount()
  })
})
