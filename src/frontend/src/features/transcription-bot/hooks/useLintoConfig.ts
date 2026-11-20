import { useConfig } from '@/api/useConfig'

export interface LintoFeatureConfig {
  enabled: boolean
  // The recording offers the late joiners' brief from its audio.
  catchupEnabled: boolean
  // A participant who joins within this delay of the run's (or the
  // recording's) start is not late.
  catchupLateThresholdS: number
}

export const DEFAULT_LATE_THRESHOLD_S = 60

/**
 * Reads the `linto` block of the runtime config (GET /api/v1.0/config/),
 * defaulting to disabled when the backend doesn't advertise the feature.
 */
export const useLintoConfig = (): LintoFeatureConfig => {
  const { data } = useConfig()
  return {
    enabled: data?.linto?.enabled ?? false,
    catchupEnabled: data?.linto?.catchup_enabled ?? false,
    catchupLateThresholdS:
      data?.linto?.catchup_late_threshold_s ?? DEFAULT_LATE_THRESHOLD_S,
  }
}
