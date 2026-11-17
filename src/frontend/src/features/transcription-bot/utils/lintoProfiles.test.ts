import { describe, expect, it } from 'vitest'
import { LintoBotProfile } from '../types/linto'
import { sanitizeLintoProfile } from './lintoProfiles'

describe('sanitizeLintoProfile', () => {
  it('keeps a well-formed profile as it is', () => {
    const profile = {
      id: '26',
      name: 'LinTO Live',
      languages: ['fr-FR', 'en-US'],
      translations: ['en', 'fr'],
    }
    expect(sanitizeLintoProfile(profile)).toEqual(profile)
  })

  it('drops the entries that are not language codes', () => {
    const profile = {
      id: '26',
      name: 'LinTO Live',
      languages: ['fr-FR', null, ''],
      translations: [{ translator: 'gemma', languages: ['en'] }, 'de', 3],
    } as unknown as LintoBotProfile
    expect(sanitizeLintoProfile(profile)).toEqual({
      id: '26',
      name: 'LinTO Live',
      languages: ['fr-FR'],
      translations: ['de'],
    })
  })

  it('reads a missing list as empty', () => {
    const profile = { id: '1', name: 'X' } as unknown as LintoBotProfile
    expect(sanitizeLintoProfile(profile)).toEqual({
      id: '1',
      name: 'X',
      languages: [],
      translations: [],
    })
  })
})
