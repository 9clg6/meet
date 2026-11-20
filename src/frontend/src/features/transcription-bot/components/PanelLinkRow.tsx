import { ReactNode } from 'react'
import { Button as RACButton } from 'react-aria-components'
import { css } from '@/styled-system/css'
import { Icon, Text } from '@/primitives'

export interface PanelLinkRowProps {
  icon: ReactNode
  label: string
  // The linked tool runs: a dot after the label.
  isActive?: boolean
  // Spoken instead of the label (e.g. the label plus "running").
  ariaLabel?: string
  onPress: () => void
  testId?: string
}

/**
 * One row of a tool panel naming the tool that runs beside it, and opening its
 * panel: the live captions under a running recording, the recording above
 * running captions. A label, no description.
 */
export const PanelLinkRow = ({
  icon,
  label,
  isActive = false,
  ariaLabel,
  onPress,
  testId,
}: PanelLinkRowProps) => (
  <RACButton
    data-testid={testId}
    aria-label={ariaLabel ?? label}
    onPress={onPress}
    className={css({
      display: 'flex',
      alignItems: 'center',
      gap: '0.625rem',
      width: '100%',
      paddingY: '0.625rem',
      paddingX: '0.75rem',
      borderRadius: '6px',
      border: '1px solid',
      borderColor: 'primary.200',
      backgroundColor: 'primary.50',
      textAlign: 'start',
      cursor: 'pointer',
      _hover: {
        backgroundColor: 'primary.100',
        borderColor: 'primary.400',
      },
    })}
  >
    <span
      aria-hidden="true"
      className={css({
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: '20px',
        flexShrink: 0,
        color: 'primary.800',
      })}
    >
      {icon}
    </span>
    <Text variant="sm" margin={false}>
      {label}
    </Text>
    {isActive && (
      <span
        aria-hidden="true"
        data-testid={testId ? `${testId}-active` : undefined}
        className={css({
          width: '0.5rem',
          height: '0.5rem',
          borderRadius: '50%',
          backgroundColor: 'success.700',
          flexShrink: 0,
        })}
      />
    )}
    <Icon
      name="chevron_forward"
      size="sm"
      className={css({ marginLeft: 'auto', color: 'primary.500' })}
    />
  </RACButton>
)

/** The red dot of a recording, as the recording indicator shows it. */
export const RecordingDot = () => (
  <span
    className={css({
      width: '0.625rem',
      height: '0.625rem',
      borderRadius: '50%',
      backgroundColor: 'danger.700',
    })}
  />
)
