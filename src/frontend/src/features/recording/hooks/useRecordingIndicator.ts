import { useMemo } from 'react'
import { useIsRecording } from '@livekit/components-react'
import { useLintoStatus } from '@/features/transcription-bot/hooks/useLintoStatus'
import { RecordingMode } from '../types'
import { useRecordingStatuses } from './useRecordingStatuses'
import { useRoomMetadata } from './useRoomMetadata'
import {
  resolveRecordingIndicator,
  type RecordingIndicator,
} from '../utils/recordingIndicator'

/**
 * The "recording in progress" indicator every participant sees (top left), or
 * `undefined` when none shows: read by the indicator itself and by the LinTO
 * pill that stacks under it.
 */
export const useRecordingIndicator = (): RecordingIndicator | undefined => {
  const screenRecording = useRecordingStatuses(RecordingMode.ScreenRecording)
  const transcript = useRecordingStatuses(RecordingMode.Transcript)
  const metadata = useRoomMetadata()
  const isRecording = useIsRecording()
  // A LinTO live run that keeps the meeting audio shows the same indicator.
  const { keepsAudio: lintoKeepsAudio } = useLintoStatus()

  const isStarted = screenRecording.isStarted || transcript.isStarted
  const isStarting = screenRecording.isStarting || transcript.isStarting

  return useMemo(
    () =>
      resolveRecordingIndicator({
        recordingMode: metadata?.recording_mode,
        recordingStatus: metadata?.recording_status,
        isStarting,
        isStarted,
        isRecording,
        lintoKeepsAudio,
      }),
    [metadata, isStarted, isStarting, isRecording, lintoKeepsAudio]
  )
}
