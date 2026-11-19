import { describe, expect, it } from 'vitest'
import type { ApiUser } from '../api/ApiUser'
import { mergeUpdatedUser } from './mergeUpdatedUser'

const ME: ApiUser = {
  id: 'u1',
  email: 'a@example.com',
  full_name: 'A',
  last_name: 'A',
  language: 'fr-fr',
  timezone: 'Europe/Paris',
  can_create: true,
  linto: {
    quickMeeting: true,
    transcription: { live: true, async: true },
    summary: true,
    translation: true,
    recording: true,
  },
}

const WRITE_RESPONSE: ApiUser = {
  id: ME.id,
  email: ME.email,
  full_name: ME.full_name,
  last_name: ME.last_name,
  language: ME.language,
  timezone: ME.timezone,
}

describe('mergeUpdatedUser', () => {
  it('keeps the LinTO capabilities a preferences update does not return', () => {
    const merged = mergeUpdatedUser(ME, {
      ...WRITE_RESPONSE,
      timezone: 'Asia/Tokyo',
    })
    expect(merged.timezone).toBe('Asia/Tokyo')
    expect(merged.linto).toEqual(ME.linto)
    expect(merged.can_create).toBe(true)
  })

  it('takes the response as is when nothing is cached', () => {
    expect(mergeUpdatedUser(undefined, ME)).toEqual(ME)
    expect(mergeUpdatedUser(false, ME)).toEqual(ME)
  })
})
