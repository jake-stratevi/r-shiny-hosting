import { describe, expect, it } from 'vitest'
import { monogramFor, tintFor } from './Monogram'

describe('monogramFor', () => {
  it('takes the first letter of the first two words', () => {
    expect(monogramFor('Treatment Pathway Dashboard')).toBe('TP')
  })

  it('falls back to two letters of a single word', () => {
    expect(monogramFor('Model')).toBe('MO')
  })

  it('skips punctuation and bare numbers rather than showing them', () => {
    expect(monogramFor('Forecast (2025 archive)')).toBe('FA')
    expect(monogramFor('  spaced   out  ')).toBe('SO')
  })

  it('never returns nothing', () => {
    expect(monogramFor('')).toBe('??')
    expect(monogramFor('   ')).toBe('??')
    expect(monogramFor('!!!')).toBe('??')
  })
})

describe('tintFor', () => {
  it('is stable for the same seed', () => {
    expect(tintFor('dashboard.tools.stratevi.com')).toBe(
      tintFor('dashboard.tools.stratevi.com'),
    )
  })

  it('never picks a warm tint — those belong to the status chips', () => {
    const seeds = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'model', 'dashboard']
    for (const seed of seeds) {
      expect(tintFor(seed)).not.toMatch(/emerald|amber|red|rose|orange|green|yellow/)
    }
  })
})
