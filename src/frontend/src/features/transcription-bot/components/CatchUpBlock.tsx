import { useEffect } from 'react'
import { css } from '@/styled-system/css'
import { RiCloseLine } from '@remixicon/react'
import { Button, Text } from '@/primitives'
import { useTranslation } from 'react-i18next'
import { useSnapshot } from 'valtio'
import {
  audioCatchUpStore,
  dismissAudioCatchUp,
} from '../store/audioCatchUpStore'
import { lintoStore } from '../store/lintoStore'
import { dismissLintoCatchUp } from '../hooks/useLintoCatchUp'
import { useLiveServesCatchUp } from '../hooks/useLiveServesCatchUp'
import { catchUpView } from '../utils/catchUpView'
import { SimpleMarkdown } from './SimpleMarkdown'

// "Too little was said" shows this long, then the block goes.
const TOO_SHORT_MS = 8000

// Wall-clock HH:MM.
const formatTime = (ms: number | null, locale: string): string => {
  if (!ms) return ''
  try {
    return new Date(ms).toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

/**
 * "Before you arrived" (fork): the brief of what a late joiner missed, on top
 * of the Tools panel whatever tool view is shown, from a live run of its own
 * or from the recording's audio. The panel opens by itself at arrival; the
 * wait shows here (the steps and an estimate for the audio), then the brief.
 * Computed once per arrival; the cross closes it for good.
 */
export const CatchUpBlock = () => {
  const { t, i18n } = useTranslation('transcription-bot', {
    keyPrefix: 'lintoBot',
  })
  const liveServes = useLiveServesCatchUp()
  const { catchUp: live, joinedAt } = useSnapshot(lintoStore)
  const { catchUp: audio } = useSnapshot(audioCatchUpStore)
  const view = catchUpView({ liveServes, live, joinedAt, audio })
  const dismiss =
    view?.source === 'live' ? dismissLintoCatchUp : dismissAudioCatchUp

  const tooShort = view?.phase === 'too_short'
  useEffect(() => {
    if (!tooShort) return
    const timer = window.setTimeout(dismiss, TOO_SHORT_MS)
    return () => window.clearTimeout(timer)
  }, [tooShort, dismiss])

  if (!view) return null

  const eta = (() => {
    if (!view.etaS) return ''
    const [low, high] = view.etaS
    if (high < 60) return t('audioCatchup.etaUnderMinute')
    const min = Math.max(1, Math.round(low / 60))
    const max = Math.max(min, Math.ceil(high / 60))
    return min === max
      ? t('audioCatchup.etaMinutes', { count: min })
      : t('audioCatchup.etaRange', { min, max })
  })()

  return (
    <div
      className={css({
        width: '100%',
        padding: '0 1.5rem',
        marginBottom: '0.75rem',
        flexShrink: 0,
      })}
    >
      <div
        data-testid="linto-catchup"
        data-source={view.source}
        data-status={view.status}
        className={css({
          width: '100%',
          padding: '0.625rem 0.75rem',
          borderRadius: '4px',
          border: '1px solid',
          borderColor: 'control.border',
          backgroundColor: 'greyscale.50',
          maxHeight: '45vh',
          overflowY: 'auto',
        })}
      >
        <div
          className={css({
            display: 'flex',
            alignItems: 'baseline',
            justifyContent: 'space-between',
            gap: '0.5rem',
          })}
        >
          <Text
            variant="sm"
            as="p"
            className={css({ fontWeight: 'semibold', color: 'primary.700' })}
          >
            {t('catchup.heading')}
          </Text>
          <Button
            variant="text"
            size="sm"
            data-testid="linto-catchup-close"
            aria-label={t('catchup.close')}
            onPress={() => dismiss()}
          >
            <RiCloseLine size={16} aria-hidden="true" />
          </Button>
        </div>

        <div aria-live="polite" data-testid="linto-catchup-progress">
          {view.phase === 'preparing' && (
            <div
              className={css({
                display: 'flex',
                flexDirection: 'column',
                gap: '0.375rem',
              })}
            >
              <Text variant="smNote" as="p">
                {view.step
                  ? t(`audioCatchup.step.${view.step}`)
                  : t('catchup.loading')}
                {eta ? ` · ${eta}` : ''}
              </Text>
              {view.progress !== null && (
                <div
                  role="progressbar"
                  aria-label={t('audioCatchup.progress')}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(view.progress * 100)}
                  className={css({
                    width: '100%',
                    height: '4px',
                    borderRadius: '2px',
                    backgroundColor: 'greyscale.200',
                    overflow: 'hidden',
                  })}
                >
                  <div
                    className={css({
                      height: '100%',
                      backgroundColor: 'primary.500',
                      transition: 'width 0.6s ease',
                    })}
                    style={{
                      width: `${Math.max(4, Math.round(view.progress * 100))}%`,
                    }}
                  />
                </div>
              )}
            </div>
          )}
          {view.phase === 'too_short' && (
            <Text variant="smNote" as="p" data-testid="linto-catchup-too-short">
              {t('audioCatchup.tooShort')}
            </Text>
          )}
        </div>

        {view.phase === 'text' && (
          <div data-testid="linto-catchup-summary">
            <SimpleMarkdown text={view.text} />
          </div>
        )}
        {view.phase === 'text' && (view.coveredUntil || view.joinedAt) && (
          <Text variant="smNote" as="p" data-testid="linto-catchup-covered">
            {view.coveredUntil
              ? t('audioCatchup.coveredUntil', {
                  time: formatTime(view.coveredUntil, i18n.language),
                })
              : t('catchup.joined', {
                  time: formatTime(view.joinedAt, i18n.language),
                })}
          </Text>
        )}
      </div>
    </div>
  )
}
