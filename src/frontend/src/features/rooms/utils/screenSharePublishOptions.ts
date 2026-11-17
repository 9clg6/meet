import {
  supportsAV1,
  supportsVP9,
  type TrackPublishOptions,
  type VideoCodec,
} from 'livekit-client'

const SINGLE_STREAM: TrackPublishOptions = { simulcast: false }

/** Keeps an SVC screen share on one stream, as livekit-client did before 2.23. */
export const screenSharePublishOptions = (
  codec?: VideoCodec
): TrackPublishOptions | undefined =>
  (codec === 'vp9' && supportsVP9()) || (codec === 'av1' && supportsAV1())
    ? SINGLE_STREAM
    : undefined
