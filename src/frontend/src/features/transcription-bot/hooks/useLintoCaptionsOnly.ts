import { useSnapshot } from 'valtio'
import { useRoomRecording } from '@/features/recording/hooks/useRoomRecording'
import { lintoStore } from '../store/lintoStore'

/**
 * Whether the live tool shows its captions-only face: a running run keeps the
 * role it started with; otherwise the tool offers captions only while the
 * meeting is recorded (the recording is the meeting's memory). Read by the
 * panel and by the side panel's header.
 */
export const useLintoCaptionsOnly = (): boolean => {
  const { running, captionsOnly } = useSnapshot(lintoStore)
  const { isRecorded } = useRoomRecording()
  return running ? captionsOnly : isRecorded
}
