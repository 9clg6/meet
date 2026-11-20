import { RiClosedCaptioningLine } from '@remixicon/react'
import { useTranslation } from 'react-i18next'
import { useSidePanel } from '@/features/rooms/livekit/hooks/useSidePanel'
import { useLintoConfig } from '../hooks/useLintoConfig'
import { useLintoStatus } from '../hooks/useLintoStatus'
import { useLintoEntitlement } from '../hooks/useLintoEntitlement'
import { PanelLinkRow } from './PanelLinkRow'

/**
 * The "live captions" row of a running recording's panel. Beside a recording
 * the live tool only shows captions; the row opens it, and carries a dot while
 * the captions run. Offered to whoever may start them, shown to everyone while
 * they run.
 */
export const LiveCaptionsRow = () => {
  const { t } = useTranslation('transcription-bot')
  const { enabled } = useLintoConfig()
  const { active, captionsOnly } = useLintoStatus()
  const entitlement = useLintoEntitlement()
  const { openLinto } = useSidePanel()

  if (!enabled) return null
  // A run of its own (its own video add-on records the meeting): not captions.
  if (active && !captionsOnly) return null
  if (!active && entitlement !== 'entitled') return null

  const label = t('lintoBot.captionsOnly.heading')
  return (
    <PanelLinkRow
      testId="linto-captions-row"
      icon={<RiClosedCaptioningLine size={20} />}
      label={label}
      isActive={active}
      ariaLabel={active ? `${label}, ${t('tools.running')}` : label}
      onPress={openLinto}
    />
  )
}
