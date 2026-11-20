import { css } from '@/styled-system/css'
import { useTranslation } from 'react-i18next'
import { useRef, useEffect } from 'react'
import { Text } from '@/primitives'
import {
  RecordingMode,
  useHasRecordingAccess,
  useRecordingStatuses,
} from '@/features/recording'
import { FeatureFlags } from '@/features/analytics/enums'
import { Button as RACButton } from 'react-aria-components'
import { useSidePanel } from '@/features/rooms/livekit/hooks/useSidePanel'
import { RecordingStatusIcon } from './RecordingStatusIcon'
import { useScreenReaderAnnounce } from '@/hooks/useScreenReaderAnnounce'
import { useRecordingIndicator } from '../hooks/useRecordingIndicator'

export const RecordingStateToast = () => {
  const { t } = useTranslation('rooms', {
    keyPrefix: 'recordingStateToast',
  })

  const { openTranscript, openScreenRecording, openLinto } = useSidePanel()

  const lastKeyRef = useRef('')
  const announce = useScreenReaderAnnounce()

  const hasTranscriptAccess = useHasRecordingAccess(
    RecordingMode.Transcript,
    FeatureFlags.Transcript
  )

  const hasScreenRecordingAccess = useHasRecordingAccess(
    RecordingMode.ScreenRecording,
    FeatureFlags.ScreenRecording
  )

  const {
    isStarted: isScreenRecordingStarted,
    isActive: isScreenRecordingActive,
  } = useRecordingStatuses(RecordingMode.ScreenRecording)

  const { isStarted: isTranscriptStarted, isActive: isTranscriptActive } =
    useRecordingStatuses(RecordingMode.Transcript)

  const isStarted = isScreenRecordingStarted || isTranscriptStarted

  const indicator = useRecordingIndicator()
  const key = indicator?.key
  const isLintoIndicator = indicator?.source === 'linto'

  // Update screen reader message only when the key actually changes
  // This prevents duplicate announcements caused by re-renders
  useEffect(() => {
    if (key && key !== lastKeyRef.current) {
      lastKeyRef.current = key
      const message = t(key)
      announce(message)
    }
  }, [announce, key, t])

  if (!key) return null

  const hasScreenRecordingAccessAndActive =
    !isLintoIndicator && isScreenRecordingActive && hasScreenRecordingAccess
  const hasTranscriptAccessAndActive =
    !isLintoIndicator && isTranscriptActive && hasTranscriptAccess

  return (
    <>
      {/* Visual banner (without aria-live to avoid duplicate announcements) */}
      <div
        data-testid="recording-state-toast"
        className={css({
          display: 'flex',
          position: 'fixed',
          top: '10px',
          left: '10px',
          paddingY: '0.25rem',
          paddingX: '0.75rem 0.75rem',
          backgroundColor: 'danger.700',
          borderColor: 'white',
          border: '1px solid',
          color: 'white',
          borderRadius: '4px',
          gap: '0.5rem',
        })}
      >
        <RecordingStatusIcon
          isStarted={isLintoIndicator || isStarted}
          isTranscriptActive={!isLintoIndicator && isTranscriptActive}
        />

        {isLintoIndicator && (
          <RACButton
            onPress={openLinto}
            className={css({
              textStyle: 'sm !important',
              fontWeight: '500 !important',
              cursor: 'pointer',
            })}
          >
            {t(key)}
          </RACButton>
        )}
        {!isLintoIndicator &&
          !hasScreenRecordingAccessAndActive &&
          !hasTranscriptAccessAndActive && (
            <Text
              variant={'sm'}
              className={css({
                fontWeight: '500 !important',
              })}
            >
              {t(key)}
            </Text>
          )}
        {hasScreenRecordingAccessAndActive && (
          <RACButton
            onPress={openScreenRecording}
            className={css({
              textStyle: 'sm !important',
              fontWeight: '500 !important',
              cursor: 'pointer',
            })}
          >
            {t(key)}
          </RACButton>
        )}
        {hasTranscriptAccessAndActive && (
          <RACButton
            onPress={openTranscript}
            className={css({
              textStyle: 'sm !important',
              fontWeight: '500 !important',
              cursor: 'pointer',
            })}
          >
            {t(key)}
          </RACButton>
        )}
      </div>
    </>
  )
}
