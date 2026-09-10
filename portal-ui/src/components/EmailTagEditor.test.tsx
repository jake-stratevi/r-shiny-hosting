import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { EmailTagEditor, isAcceptableEmail, splitCandidates } from './EmailTagEditor'

function Harness({
  initial = [] as string[],
  onChange = vi.fn(),
}: {
  initial?: string[]
  onChange?: (next: string[]) => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <EmailTagEditor
      value={value}
      onChange={(next) => {
        setValue(next)
        onChange(next)
      }}
    />
  )
}

const field = () => screen.getByRole('textbox')
const tags = () =>
  screen
    .queryAllByRole('button', { name: /^Remove / })
    .map((b) => b.getAttribute('aria-label')!.replace('Remove ', ''))

describe('helpers', () => {
  it('accepts anything with a single @ and no whitespace', () => {
    expect(isAcceptableEmail('Jake@Stratevi.com')).toBe(true)
    expect(isAcceptableEmail('nope')).toBe(false)
    expect(isAcceptableEmail('@nope.com')).toBe(false)
    expect(isAcceptableEmail('nope@')).toBe(false)
    expect(isAcceptableEmail('two@@at.com')).toBe(false)
  })

  it('splits on commas, semicolons and whitespace', () => {
    expect(splitCandidates('a@b.com, c@d.com;e@f.com\n g@h.com')).toEqual([
      'a@b.com',
      'c@d.com',
      'e@f.com',
      'g@h.com',
    ])
  })
})

describe('EmailTagEditor', () => {
  it('commits on Enter, lowercased', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await user.type(field(), 'Jake@Stratevi.COM{Enter}')

    expect(onChange).toHaveBeenCalledWith(['jake@stratevi.com'])
    expect(tags()).toEqual(['jake@stratevi.com'])
    expect(field()).toHaveValue('')
  })

  it('commits on a comma keystroke', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(field(), 'a@b.com,')

    expect(tags()).toEqual(['a@b.com'])
  })

  it('rejects an entry with no @ and says so', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)

    await user.type(field(), 'not-an-email{Enter}')

    expect(onChange).not.toHaveBeenCalled()
    expect(tags()).toEqual([])
    expect(screen.getByRole('alert')).toHaveTextContent('is not an email address')
    // The bad text stays put so it can be corrected rather than retyped.
    expect(field()).toHaveValue('not-an-email')
  })

  it('does not add the same address twice', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['a@b.com']} />)

    await user.type(field(), 'A@B.com{Enter}')

    expect(tags()).toEqual(['a@b.com'])
  })

  it('splits a pasted list into tags', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(field())
    await user.paste('a@b.com, c@d.com; e@f.com')

    expect(tags()).toEqual(['a@b.com', 'c@d.com', 'e@f.com'])
  })

  it('removes the last tag on Backspace in an empty field', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['a@b.com', 'c@d.com']} />)

    await user.click(field())
    await user.keyboard('{Backspace}')

    expect(tags()).toEqual(['a@b.com'])
  })

  it('removes a tag with its remove button', async () => {
    const user = userEvent.setup()
    render(<Harness initial={['a@b.com', 'c@d.com']} />)

    await user.click(screen.getByRole('button', { name: 'Remove a@b.com' }))

    expect(tags()).toEqual(['c@d.com'])
  })

  it('commits a pending draft on blur', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.type(field(), 'a@b.com')
    await user.tab()

    expect(tags()).toEqual(['a@b.com'])
  })
})
