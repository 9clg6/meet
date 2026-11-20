import { useToast } from 'react-aria'
import { useMemo, useRef, useState } from 'react'
import { Button, Text } from '@/primitives'

import { type ToastProps } from './Toast'
import { HStack } from '@/styled-system/jsx'
import { useTranslation } from 'react-i18next'
import { useUser } from '@/features/auth/api/useUser'
import { css } from '@/styled-system/css'
import { RecordingMode } from '@/features/recording'
import { StyledToastContainer } from './StyledToastContainer'
import { useLintoStatus } from '@/features/transcription-bot/hooks/useLintoStatus'

export function ToastRecordingSaving({
  state,
  ...props
}: Readonly<ToastProps>) {
  const { t } = useTranslation('notifications', { keyPrefix: 'recordingSave' })
  const { t: tLinto } = useTranslation('transcription-bot', {
    keyPrefix: 'lintoBot.captionsOnly',
  })
  const ref = useRef(null)
  const { toastProps, contentProps } = useToast(props, state, ref)

  const { user } = useUser()
  const recipientIsMe = props.toast.content.recipientIsMe === true
  const shared = props.toast.content.shared === true
  // The captions running beside the stopped recording go on; the person who
  // stopped it may stop them too, while they still run.
  const stopCaptions =
    typeof props.toast.content.stopCaptions === 'function'
      ? (props.toast.content.stopCaptions as () => Promise<void>)
      : undefined
  const { active: lintoActive, captionsOnly } = useLintoStatus()
  const [isStopping, setIsStopping] = useState(false)
  const offerStopCaptions = !!stopCaptions && lintoActive && captionsOnly

  const modeLabel = useMemo(() => {
    const mode = props.toast.content.mode as RecordingMode
    switch (mode) {
      case RecordingMode.Transcript:
        return 'transcript'
      case RecordingMode.ScreenRecording:
        return 'screenRecording'
    }
  }, [props.toast.content])

  return (
    <StyledToastContainer {...toastProps} ref={ref}>
      <HStack
        justify="center"
        alignItems="center"
        {...contentProps}
        padding={14}
        gap={1}
      >
        <Text
          margin={false}
          className={css({
            maxWidth: '22rem',
            wordBreak: 'break-word',
            overflowWrap: 'break-word',
            whiteSpace: 'normal',
          })}
        >
          {!recipientIsMe ? (
            t(`${modeLabel}.organizer`)
          ) : user?.email ? (
            <span
              dangerouslySetInnerHTML={{
                __html: t(`${modeLabel}.message`, {
                  email: user.email,
                }),
              }}
            />
          ) : (
            t(`${modeLabel}.default`)
          )}
          {shared && modeLabel === 'transcript' && ` ${t('transcript.shared')}`}
        </Text>
        {offerStopCaptions && (
          <Button
            size="sm"
            variant="text"
            data-testid="toast-stop-captions"
            className={css({ color: 'primary.300', flexShrink: 0 })}
            isDisabled={isStopping}
            onPress={async () => {
              setIsStopping(true)
              await stopCaptions()
              state.close(props.toast.key)
            }}
          >
            {tLinto('stop')}
          </Button>
        )}
      </HStack>
    </StyledToastContainer>
  )
}
