import { css, cx } from '@/styled-system/css'
import { useTranslation } from 'react-i18next'
import { RiClosedCaptioningLine } from '@remixicon/react'
import { Icon, Text } from '@/primitives'
import { Button as RACButton } from 'react-aria-components'
import { useSidePanel } from '@/features/rooms/livekit/hooks/useSidePanel'
import { useRecordingIndicator } from '@/features/recording/hooks/useRecordingIndicator'
import { useLintoStatus } from '../hooks/useLintoStatus'

/**
 * Ambient pill shown to EVERY participant while a LinTO bot runs — independent
 * of whether the panel is open. Red means something is kept: a run of its own
 * ("Live transcription"). Live captions beside a recording keep nothing: a
 * neutral "Live captions" pill, which stays neutral once the recording stops.
 *
 * It reads the dedicated LiveKit room-metadata flags (pushed by the backend at
 * start, cleared at stop), NOT `recording_mode`: that keeps it clear of the
 * recording indicator (RecordingStateToast), under which it stacks. Clicking it
 * opens the panel.
 */
export const LintoBanner = () => {
  const { t } = useTranslation('transcription-bot')
  const { active, captionsOnly } = useLintoStatus()
  const { openLinto } = useSidePanel()
  // Under the recording indicator when it shows, in its place otherwise.
  const underIndicator = !!useRecordingIndicator()

  if (!active) return null

  return (
    <div
      data-testid="linto-banner"
      data-captions-only={captionsOnly ? 'true' : 'false'}
      className={cx(
        css({
          display: 'flex',
          position: 'fixed',
          left: '10px',
          paddingY: '0.25rem',
          paddingX: '0.75rem',
          border: '1px solid',
          color: 'white',
          borderRadius: '4px',
          gap: '0.5rem',
          alignItems: 'center',
          zIndex: 1,
        }),
        captionsOnly
          ? css({
              backgroundColor: 'primaryDark.100',
              borderColor: 'primaryDark.300',
            })
          : css({ backgroundColor: 'danger.700', borderColor: 'white' })
      )}
      style={{ top: underIndicator ? '48px' : '10px' }}
    >
      {captionsOnly ? (
        <RiClosedCaptioningLine size={18} aria-hidden="true" />
      ) : (
        <>
          <span
            aria-hidden="true"
            className={css({
              width: '0.5rem',
              height: '0.5rem',
              borderRadius: '50%',
              backgroundColor: 'white',
              flexShrink: 0,
            })}
          />
          <Icon name="speech_to_text" />
        </>
      )}
      <RACButton
        onPress={openLinto}
        className={css({
          textStyle: 'sm !important',
          fontWeight: '500 !important',
          cursor: 'pointer',
        })}
      >
        <Text variant="sm" className={css({ fontWeight: '500 !important' })}>
          {captionsOnly
            ? t('lintoBot.captionsOnly.heading')
            : t('banner.inProgress')}
        </Text>
      </RACButton>
    </div>
  )
}
