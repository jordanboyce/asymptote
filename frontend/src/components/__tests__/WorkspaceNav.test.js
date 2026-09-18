import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkspaceNav from '../WorkspaceNav.vue'

describe('Workspace navigation', () => {
  it('keeps the same three primary destinations on desktop and mobile', async () => {
    for (const mobile of [false, true]) {
      const wrapper = mount(WorkspaceNav, { props: { activeTab: 'chat', mobile } })
      const primary = wrapper.findAll('[data-nav="primary"]')
      expect(primary.map(button => button.text())).toEqual(['Ask', 'Find', 'Connect'])
      expect(primary[0].attributes('aria-current')).toBe('page')
      // Ask and Find ride in the mode switch on desktop, flat tabs on a phone
      expect(wrapper.findAll('.segmented button')).toHaveLength(mobile ? 0 : 2)
      await primary[2].trigger('click')
      expect(wrapper.emitted('navigate')[0]).toEqual(['mcp'])
      expect(wrapper.text()).not.toContain('Administration')
      wrapper.unmount()
    }
  })

  it('retains secondary destinations and closes the menu after choosing one', async () => {
    const wrapper = mount(WorkspaceNav, { props: { activeTab: 'generate', adminConsole: true } })
    expect(wrapper.get('summary').text()).toBe('Reports')
    const menu = wrapper.get('details')
    menu.element.open = true
    const admin = wrapper.findAll('li button').find(button => button.text() === 'Administration')
    await admin.trigger('click')
    expect(wrapper.emitted('navigate')[0]).toEqual(['admin'])
    expect(menu.element.open).toBe(false)
  })

  it('supports search-only deployments and keyboard dismissal', async () => {
    const wrapper = mount(WorkspaceNav, { props: { activeTab: 'search', chatEnabled: false } })
    expect(wrapper.findAll('[data-nav="primary"]').map(button => button.text())).toEqual(['Find', 'Connect'])
    // One mode is not a choice: no track, just a button
    expect(wrapper.find('.segmented').exists()).toBe(false)
    const menu = wrapper.get('details')
    menu.element.open = true
    await menu.trigger('keydown', { key: 'Escape' })
    expect(menu.element.open).toBe(false)
  })
})
