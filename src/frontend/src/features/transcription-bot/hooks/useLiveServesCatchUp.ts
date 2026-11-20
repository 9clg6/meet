import { useLintoStatus } from './useLintoStatus'

/**
 * A live run of its own whose catch-up option is on serves the late joiners
 * from its captions. Live captions on top of a recording keep nothing, and a
 * run without the option offers nothing: the recording's audio brief serves.
 */
export const useLiveServesCatchUp = (): boolean => {
  const { active, captionsOnly, catchUpEnabled } = useLintoStatus()
  return active && !captionsOnly && catchUpEnabled
}
