import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AnswerEvidence from '../AnswerEvidence.vue'
import { answerWithReferences, renderEvidenceMarkdown, sourceHref } from '../../utils/answerEvidence'

const source = {
  filename: 'Equipment policy.pdf', document_id: 'doc-1', page_number: 3,
  text_snippet: 'Equipment must be returned within five working days.',
  page_url: 'https://old-host.example/documents/doc-1/pdf?collection_id=operations#page=3',
}

describe('Answer evidence review', () => {
  it('opens the cited passage in place and moves keyboard focus to it', async () => {
    const wrapper = mount(AnswerEvidence, {
      props: { content: 'Return it within five days. [Source 1]', sources: [source] },
      attachTo: document.body,
    })
    expect(wrapper.get('details').element.open).toBe(false)
    await wrapper.get('button[data-source-number="1"]').trigger('click')
    expect(wrapper.get('details').element.open).toBe(true)
    expect(document.activeElement).toBe(wrapper.get('li').element)
    expect(wrapper.get('li').text()).toContain(source.text_snippet)
    expect(wrapper.get('li a').attributes('href')).toBe('/documents/doc-1/pdf?collection_id=operations#page=3')
    wrapper.unmount()
  })

  it('does not manufacture evidence for invalid citations or code examples', () => {
    const wrapper = mount(AnswerEvidence, { props: {
      content: '[Source 8] and `[Source 1]` and [Source 1]', sources: [source],
    } })
    expect(wrapper.findAll('button[data-source-number]')).toHaveLength(1)
    expect(wrapper.get('code').text()).toBe('[Source 1]')
    expect(wrapper.text()).toContain('[Source 8]')
  })

  it('removes executable markup and model-authored citation controls', () => {
    const html = renderEvidenceMarkdown('<img src=x onerror="alert(1)"><button data-source-number="99">Fake</button> [Source 1]', [source])
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('data-source-number="99"')
    expect(html).toContain('data-source-number="1"')
    expect(sourceHref({ ...source, page_url: 'javascript:alert(1)' })).toBe('')
    expect(sourceHref({ ...source, page_url: 'https://elsewhere.example/collect' })).toBe('')
  })

  it('copies the answer with matching source numbers, excerpts, and current-host links', () => {
    const copied = answerWithReferences({ content: 'Five days. [Source 1]', sources: [source] })
    expect(copied).toContain('[Source 1] Equipment policy.pdf, page / section 3')
    expect(copied).toContain(source.text_snippet)
    expect(copied).not.toContain('old-host.example')
    expect(copied).toContain('collection_id=operations#page=3')
  })

  it('distinguishes missing document passages from verified evidence and streaming', async () => {
    const wrapper = mount(AnswerEvidence, { props: { content: 'An answer', streaming: true } })
    expect(wrapper.text()).not.toContain('No document passages attached')
    await wrapper.setProps({ streaming: false })
    expect(wrapper.text()).toContain('No document passages attached')
  })
})
