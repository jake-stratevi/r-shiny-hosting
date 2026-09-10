import { useId } from 'react'
import {
  dateInputToEpoch,
  defaultExpiryEpoch,
  epochToDateInput,
  formatDateTime,
  todayDateInput,
} from '../lib/time'

interface Props {
  /** Epoch seconds, or null for "never expires". */
  value: number | null
  onChange: (next: number | null) => void
  disabled?: boolean
}

/**
 * `expires_at`: a date, or an explicit never. portal.md is emphatic that the
 * choice must be deliberate, so "never expires" is its own switch rather than
 * an empty date field.
 */
export function ExpiryPicker({ value, onChange, disabled }: Props) {
  const id = useId()
  const dateId = `${id}-date`
  const neverId = `${id}-never`
  const never = value === null
  const expired = value !== null && value * 1000 < Date.now()

  return (
    <div className="space-y-2.5">
      <label
        htmlFor={neverId}
        className={`flex w-fit items-center gap-2 text-sm text-foreground ${
          disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
        }`}
      >
        <input
          id={neverId}
          type="checkbox"
          checked={never}
          disabled={disabled}
          className="h-4 w-4 rounded border-border text-azure accent-accent focus:ring-azure"
          onChange={(e) => onChange(e.target.checked ? null : defaultExpiryEpoch())}
        />
        Never expires
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <input
          id={dateId}
          type="date"
          aria-label="Expiry date"
          value={epochToDateInput(value)}
          min={todayDateInput()}
          disabled={disabled || never}
          className="rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-azure disabled:cursor-not-allowed disabled:bg-background disabled:text-muted-foreground/70"
          onChange={(e) => {
            const next = dateInputToEpoch(e.target.value)
            if (next !== null) onChange(next)
          }}
        />
        {never ? (
          <span className="text-xs text-muted-foreground/70">No expiry set.</span>
        ) : (
          <span className={`text-xs ${expired ? 'text-red-700 dark:text-red-300' : 'text-muted-foreground/70'}`}>
            {expired ? 'Already past: ' : 'Access ends '}
            {formatDateTime(value)}
          </span>
        )}
      </div>
    </div>
  )
}
