import { useSnapshot } from 'valtio'
import { useRoomData } from '@/features/rooms/livekit/hooks/useRoomData'
import { useIsAdminOrOwner } from '@/features/rooms/livekit/hooks/useIsAdminOrOwner'
import { lintoStore, resetLintoRun } from '../store/lintoStore'
import { stopLintoLive } from '../api/lintoBotApi'
import { useLintoStatus } from './useLintoStatus'

// Same guard as the panel's own stop: a stale metadata roundtrip must not
// bring the stopped captions back.
const LOCAL_ACTION_GUARD_MS = 6000

/**
 * Stops the captions-only run from outside its panel (the toast of a stopped
 * recording). `undefined` when no such run is going or when I may not stop it
 * (only its starter or a room admin/owner may). A plain request, not a
 * mutation hook: the toast calls it after the panel that built it may be gone.
 */
export const useLiveCaptionsStopper = (): (() => Promise<void>) | undefined => {
  const apiRoomData = useRoomData()
  const roomId = apiRoomData?.livekit?.room
  const token = apiRoomData?.livekit?.token
  const { active, captionsOnly } = useLintoStatus()
  const { startedByMe } = useSnapshot(lintoStore)
  const isAdminOrOwner = useIsAdminOrOwner()

  if (!active || !captionsOnly || !roomId || !token) return undefined
  if (!startedByMe && !isAdminOrOwner) return undefined

  return async () => {
    lintoStore.localActionUntil = Date.now() + LOCAL_ACTION_GUARD_MS
    try {
      await stopLintoLive({ roomId, token })
      resetLintoRun()
    } catch (err) {
      lintoStore.localActionUntil = 0
      console.error('Failed to stop the live captions:', err)
    }
  }
}
