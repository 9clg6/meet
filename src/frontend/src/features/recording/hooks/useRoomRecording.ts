import { useMemo } from 'react'
import { useRoomMetadata } from './useRoomMetadata'
import { RecordingStatus } from './useRecordingStatuses'

export interface RoomRecording {
  // The meeting is being recorded right now (deferred transcription or video):
  // an egress starting or started — not one saving after its stop.
  isRecorded: boolean
  // The tool the recording runs in ('transcript' / 'screen_recording').
  mode?: string
}

export const useRoomRecording = (): RoomRecording => {
  const metadata = useRoomMetadata()
  return useMemo(() => {
    const mode = metadata?.recording_mode as string | undefined
    const status = String(metadata?.recording_status ?? '')
    return {
      isRecorded:
        !!mode &&
        [RecordingStatus.Starting, RecordingStatus.Started].includes(
          status as RecordingStatus
        ),
      mode,
    }
  }, [metadata])
}
