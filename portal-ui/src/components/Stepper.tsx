import { CheckIcon } from './icons'

export interface StepperStep {
  id: string
  label: string
}

/**
 * The wizard's step indicator, ported from assembled.work's `Create.vue`
 * stepper: numbered circles, a filled tick for what is done, a ring on where
 * you are, and a connector that fills in behind you. Their sky becomes our
 * one accent, and the circles stay decorative — the `<ol>` and
 * `aria-current="step"` carry the meaning.
 *
 * A finished step is a button back to itself. Forward is the Next button's
 * job, because forward is where the validation lives.
 */
export function Stepper({
  steps,
  current,
  label,
  onJump,
}: {
  steps: ReadonlyArray<StepperStep>
  /** Index of the active step. */
  current: number
  label: string
  /** Given, completed steps become clickable. */
  onJump?: (index: number) => void
}) {
  return (
    <ol aria-label={label} className="mb-6 flex items-center gap-1 sm:gap-2">
      {steps.map((step, index) => {
        const done = index < current
        const active = index === current
        const clickable = done && onJump !== undefined

        const circle = (
          <span
            aria-hidden="true"
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-[13px] font-semibold transition-colors ${
              done
                ? 'border-accent bg-accent text-white'
                : active
                  ? 'border-accent bg-accent-soft text-accent ring-2 ring-accent/20'
                  : 'border-line bg-surface text-faint'
            }`}
          >
            {done ? <CheckIcon className="h-4 w-4" /> : index + 1}
          </span>
        )

        const text = (
          <span
            className={`hidden text-sm sm:inline ${
              active
                ? 'font-semibold text-accent'
                : done
                  ? 'font-medium text-ink'
                  : 'text-faint'
            }`}
          >
            {step.label}
          </span>
        )

        return (
          <li
            key={step.id}
            aria-current={active ? 'step' : undefined}
            className="flex flex-1 items-center gap-2 last:flex-none"
          >
            {clickable ? (
              <button
                type="button"
                onClick={() => onJump(index)}
                className="flex items-center gap-2 rounded-tile px-0.5 py-0.5 transition-opacity hover:opacity-80"
              >
                {circle}
                {text}
                <span className="sr-only">Back to {step.label}</span>
              </button>
            ) : (
              <span className="flex items-center gap-2 px-0.5 py-0.5">
                {circle}
                {text}
              </span>
            )}

            {index < steps.length - 1 ? (
              <span
                aria-hidden="true"
                className={`h-0.5 min-w-[0.75rem] flex-1 rounded-full ${
                  done ? 'bg-accent' : 'bg-line'
                }`}
              />
            ) : null}
          </li>
        )
      })}
    </ol>
  )
}
