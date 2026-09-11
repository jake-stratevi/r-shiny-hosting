import { describe, expect, it } from 'vitest'
import {
  describeAnomalies,
  formatHours,
  formatMoney,
  formatMonth,
  formatRate,
  formatUnitRate,
} from './money'

describe('formatMoney', () => {
  it('formats whole and fractional dollars', () => {
    expect(formatMoney(0)).toBe('$0.00')
    expect(formatMoney(0.35)).toBe('$0.35')
    expect(formatMoney(29.94)).toBe('$29.94')
    expect(formatMoney(1204.1)).toBe('$1,204.10')
  })

  it('never renders a real cost as $0.00', () => {
    // 90 seconds of a dashboard is about $0.0007. "$0.00" next to "2 min"
    // reads as a bug; "<$0.01" reads as a small number.
    expect(formatMoney(0.0007)).toBe('<$0.01')
  })

  it('has an em dash for absent values', () => {
    expect(formatMoney(null)).toBe('—')
    expect(formatMoney(undefined)).toBe('—')
    expect(formatMoney(Number.NaN)).toBe('—')
  })
})

describe('formatRate', () => {
  it('keeps four decimals, which is where the two known rates live', () => {
    expect(formatRate(0.02913)).toBe('$0.0291/hr')
    expect(formatRate(0.23304)).toBe('$0.2330/hr')
  })

  it('renders a null rate as an em dash, not as free', () => {
    expect(formatRate(null)).toBe('—')
  })
})

describe('formatHours', () => {
  it('uses minutes below an hour', () => {
    expect(formatHours(0.5)).toBe('30 min')
    expect(formatHours(0.01)).toBe('1 min')
  })

  it('uses one decimal up to ten hours and none above', () => {
    expect(formatHours(2.44)).toBe('2.4h')
    expect(formatHours(124.2)).toBe('124h')
  })

  it('renders zero as zero, not as an em dash', () => {
    expect(formatHours(0)).toBe('0h')
    expect(formatHours(null)).toBe('—')
  })
})

describe('formatMonth', () => {
  it('reads the contract YYYY-MM', () => {
    expect(formatMonth('2026-09')).toBe('September 2026')
    expect(formatMonth('2026-01')).toBe('January 2026')
  })

  it('passes an unreadable value through rather than inventing a month', () => {
    expect(formatMonth('nonsense')).toBe('nonsense')
    expect(formatMonth('')).toBe('—')
  })
})

describe('describeAnomalies', () => {
  it('puts the known cases in words', () => {
    expect(describeAnomalies({ unclosed: 1 })[0]).toMatch(
      /1 run never recorded stopping and was capped/,
    )
    expect(describeAnomalies({ unclosed: 3 })[0]).toMatch(/3 runs/)
    expect(describeAnomalies({ duplicate_wake: 2 })[0]).toMatch(/2 repeated start/)
  })

  it('shows an unknown key rather than dropping it', () => {
    expect(describeAnomalies({ something_new: 4 })).toEqual(['something_new: 4'])
  })

  it('ignores zero counts and absent maps', () => {
    expect(describeAnomalies({ unclosed: 0 })).toEqual([])
    expect(describeAnomalies({})).toEqual([])
    expect(describeAnomalies(null)).toEqual([])
    expect(describeAnomalies(undefined)).toEqual([])
  })
})

describe('formatUnitRate', () => {
  it('keeps the published AWS constants intact', () => {
    // Four decimals would print $0.0044 — a number nobody can look up.
    expect(formatUnitRate(0.04048)).toBe('$0.04048')
    expect(formatUnitRate(0.004445)).toBe('$0.004445')
  })

  it('trims trailing zeros rather than faking precision', () => {
    expect(formatUnitRate(0.05)).toBe('$0.05')
  })

  it('has an em dash for absent values', () => {
    expect(formatUnitRate(null)).toBe('—')
  })
})
