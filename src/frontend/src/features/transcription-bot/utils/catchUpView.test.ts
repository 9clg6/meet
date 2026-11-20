import { describe, expect, it } from 'vitest'
import { catchUpView, isLateArrival } from './catchUpView'
import { IDLE_CATCH_UP, type LintoCatchUpState } from '../store/lintoStore'
import {
  IDLE_AUDIO_CATCH_UP,
  type AudioCatchUpState,
} from '../store/audioCatchUpStore'

const live = (patch: Partial<LintoCatchUpState>): LintoCatchUpState => ({
  ...IDLE_CATCH_UP,
  ...patch,
})
const audio = (patch: Partial<AudioCatchUpState>): AudioCatchUpState => ({
  ...IDLE_AUDIO_CATCH_UP,
  ...patch,
})
const JOINED = Date.parse('2026-11-20T10:10:00Z')

describe('catchUpView', () => {
  it('shows the live brief when a live run of its own serves', () => {
    const view = catchUpView({
      liveServes: true,
      live: live({ status: 'done', text: ' Brief ' }),
      joinedAt: JOINED,
      audio: audio({ status: 'ready', brief: { text: 'x', coveredUntil: 1 } }),
    })
    expect(view).toMatchObject({
      source: 'live',
      status: 'done',
      phase: 'text',
      text: 'Brief',
      joinedAt: JOINED,
    })
  })

  it('shows the audio brief when no live run serves', () => {
    const view = catchUpView({
      liveServes: false,
      live: live({ status: 'done', text: 'live' }),
      joinedAt: JOINED,
      audio: audio({
        status: 'ready',
        brief: { text: 'Audio', coveredUntil: JOINED },
      }),
    })
    expect(view).toMatchObject({
      source: 'audio',
      phase: 'text',
      text: 'Audio',
      coveredUntil: JOINED,
    })
  })

  it('shows the step, its progress and the estimate while the audio runs', () => {
    const view = catchUpView({
      liveServes: false,
      live: live({}),
      joinedAt: null,
      audio: audio({ status: 'transcribing', progress: 0.5, etaS: [30, 90] }),
    })
    expect(view).toMatchObject({
      phase: 'preparing',
      step: 'transcribing',
      progress: 0.5,
      etaS: [30, 90],
    })
  })

  it('waits for the first words of the live summary', () => {
    expect(
      catchUpView({
        liveServes: true,
        live: live({ status: 'loading' }),
        joinedAt: JOINED,
        audio: audio({}),
      })
    ).toMatchObject({ phase: 'preparing', step: null, progress: null })
    expect(
      catchUpView({
        liveServes: true,
        live: live({ status: 'streaming', text: 'En c' }),
        joinedAt: JOINED,
        audio: audio({}),
      })
    ).toMatchObject({ phase: 'text', text: 'En c' })
  })

  it('says when too little was said', () => {
    expect(
      catchUpView({
        liveServes: false,
        live: live({}),
        joinedAt: null,
        audio: audio({ status: 'too_short' }),
      })?.phase
    ).toBe('too_short')
  })

  it.each([
    ['idle', live({}), audio({})],
    ['live failure', live({ status: 'error' }), audio({})],
    ['live unavailable', live({ status: 'unavailable' }), audio({})],
    ['empty live result', live({ status: 'done', text: '  ' }), audio({})],
    ['closed', live({ status: 'done', text: 'x', dismissed: true }), audio({})],
  ])('shows nothing for the live source when %s', (_label, liveState, a) => {
    expect(
      catchUpView({ liveServes: true, live: liveState, joinedAt: 1, audio: a })
    ).toBeNull()
  })

  it.each([
    ['idle', audio({})],
    ['failed', audio({ status: 'failed' })],
    ['unavailable', audio({ status: 'unavailable' })],
    ['ready without brief', audio({ status: 'ready' })],
    [
      'closed',
      audio({
        status: 'ready',
        brief: { text: 'x', coveredUntil: 1 },
        dismissed: true,
      }),
    ],
  ])('shows nothing for the audio source when %s', (_label, a) => {
    expect(
      catchUpView({ liveServes: false, live: live({}), joinedAt: 1, audio: a })
    ).toBeNull()
  })
})

describe('isLateArrival', () => {
  const start = Date.parse('2026-11-20T10:00:00Z')

  it('is late only past the threshold after the start', () => {
    expect(isLateArrival(start + 60_000, start, 60)).toBe(false)
    expect(isLateArrival(start + 61_000, start, 60)).toBe(true)
    expect(isLateArrival(start - 5_000, start, 60)).toBe(false)
  })

  it('is never late without a start', () => {
    expect(isLateArrival(start, Number.NaN, 60)).toBe(false)
  })
})
