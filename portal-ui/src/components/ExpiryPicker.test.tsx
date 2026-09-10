import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { dateInputToEpoch, epochToDateInput } from '../lib/time'
import { ExpiryPicker } from './ExpiryPicker'

function Harness({
  initial,
  onChange = vi.fn(),
}: {
  initial: number | null
  onChange?: (next: number | null) => void
}) {
  const [value, setValue] = useState<number | null>(initial)
  return (
    <ExpiryPicker
      value={value}
      onChange={(next) => {
        setValue(next)
        onChange(next)
      }}
    />
  )
}

const neverToggle = () => screen.getByRole('checkbox', { name: /never expires/i })
const dateField = () => screen.getByLabelText('Expiry date') as HTMLInputElement

describe('date <-> epoch conversion', () => {
  it('round-trips a local date', () => {
    const epoch = dateInputToEpoch('2026-09-30')!
    expect(epochToDateInput(epoch)).toBe('2026-09-30')
  })

  it('lands at the end of the local day, so the app works all of that day', () => {
    const d = new Date(dateInputToEpoch('2026-09-30')! * 1000)
    expect(d.getHours()).toBe(23)
    expect(d.getMinutes()).toBe(59)
    expect(d.getDate()).toBe(30)
  })

  it('rejects junk', () => {
    expect(dateInputToEpoch('')).toBeNull()
    expect(dateInputToEpoch('30/09/2026')).toBeNull()
  })

  it('renders null as an empty date field', () => {
    expect(epochToDateInput(null)).toBe('')
  })
})

describe('ExpiryPicker', () => {
  it('shows "never" checked and the date field disabled when the value is null', () => {
    render(<Harness initial={null} />)

    expect(neverToggle()).toBeChecked()
    expect(dateField()).toBeDisabled()
    expect(dateField()).toHaveValue('')
    expect(screen.getByText('No expiry set.')).toBeInTheDocument()
  })

  it('shows the date, unchecked, when there is an expiry', () => {
    const epoch = dateInputToEpoch('2026-12-01')!
    render(<Harness initial={epoch} />)

    expect(neverToggle()).not.toBeChecked()
    expect(dateField()).toBeEnabled()
    expect(dateField()).toHaveValue('2026-12-01')
  })

  it('emits null when "never expires" is switched on', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness initial={dateInputToEpoch('2026-12-01')} onChange={onChange} />)

    await user.click(neverToggle())

    expect(onChange).toHaveBeenCalledWith(null)
    expect(dateField()).toBeDisabled()
  })

  it('proposes a concrete future date when "never expires" is switched off', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<Harness initial={null} onChange={onChange} />)

    await user.click(neverToggle())

    expect(onChange).toHaveBeenCalledTimes(1)
    const proposed = onChange.mock.calls[0][0] as number
    expect(typeof proposed).toBe('number')
    expect(proposed * 1000).toBeGreaterThan(Date.now())
    expect(dateField()).toBeEnabled()
    expect(dateField().value).not.toBe('')
  })

  it('emits the chosen date as end-of-day epoch seconds', () => {
    const onChange = vi.fn()
    render(<Harness initial={dateInputToEpoch('2026-12-01')} onChange={onChange} />)

    // Date inputs are set, not typed into.
    fireEvent.change(dateField(), { target: { value: '2027-03-15' } })

    const last = onChange.mock.calls.at(-1)![0] as number
    expect(last).toBe(dateInputToEpoch('2027-03-15'))
    expect(epochToDateInput(last)).toBe('2027-03-15')
    expect(dateField()).toHaveValue('2027-03-15')
  })

  it('ignores a half-typed date rather than emitting garbage', () => {
    const onChange = vi.fn()
    render(<Harness initial={dateInputToEpoch('2026-12-01')} onChange={onChange} />)

    fireEvent.change(dateField(), { target: { value: '' } })

    expect(onChange).not.toHaveBeenCalled()
  })
})
