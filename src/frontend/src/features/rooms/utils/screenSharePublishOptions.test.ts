import { beforeEach, describe, expect, it, vi } from 'vitest'
import { supportsAV1, supportsVP9 } from 'livekit-client'
import { screenSharePublishOptions } from './screenSharePublishOptions'

vi.mock('livekit-client', () => ({
  supportsVP9: vi.fn(),
  supportsAV1: vi.fn(),
}))

describe('screenSharePublishOptions', () => {
  beforeEach(() => {
    vi.mocked(supportsVP9).mockReturnValue(true)
    vi.mocked(supportsAV1).mockReturnValue(true)
  })

  it('turns simulcast off for a VP9 screen share', () => {
    expect(screenSharePublishOptions('vp9')).toEqual({ simulcast: false })
  })

  it('turns simulcast off for an AV1 screen share', () => {
    expect(screenSharePublishOptions('av1')).toEqual({ simulcast: false })
  })

  it('returns the same object on every call', () => {
    expect(screenSharePublishOptions('vp9')).toBe(
      screenSharePublishOptions('av1')
    )
  })

  it('keeps the defaults when the browser falls back from VP9 to VP8', () => {
    vi.mocked(supportsVP9).mockReturnValue(false)
    expect(screenSharePublishOptions('vp9')).toBeUndefined()
  })

  it('keeps the defaults for codecs without SVC', () => {
    expect(screenSharePublishOptions('vp8')).toBeUndefined()
    expect(screenSharePublishOptions('h264')).toBeUndefined()
    expect(screenSharePublishOptions(undefined)).toBeUndefined()
  })
})
