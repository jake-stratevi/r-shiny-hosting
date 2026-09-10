import { useAppearance, type Appearance } from '../lib/appearance'
import { MonitorIcon, MoonIcon, SunIcon } from './icons'

const OPTIONS: ReadonlyArray<{
  value: Appearance
  label: string
  Icon: typeof SunIcon
}> = [
  { value: 'light', label: 'Light', Icon: SunIcon },
  { value: 'dark', label: 'Dark', Icon: MoonIcon },
  { value: 'system', label: 'Match system', Icon: MonitorIcon },
]

/**
 * The compact, always-visible theme switcher from assembled.work's
 * `AppearanceToggle` — three states in the width of one control, pinned in
 * the sidebar footer so the choice is one click away from anywhere.
 *
 * "System" is a real third state, not the absence of a choice: it keeps
 * tracking the OS after you pick it.
 */
export function AppearanceToggle({
  /**
   * Theirs hides the toggle outright when the rail collapses, because their
   * Settings > Appearance page is the other way in. We have no settings
   * page, so a hidden toggle would be an unreachable one: collapsed, the
   * three buttons stack into the 3rem rail instead.
   */
  orientation = 'horizontal',
  className = '',
}: {
  orientation?: 'horizontal' | 'vertical'
  className?: string
}) {
  const { appearance, setAppearance } = useAppearance()
  const vertical = orientation === 'vertical'

  return (
    <div
      role="group"
      aria-label="Color theme"
      className={`flex rounded-lg border border-sidebar-border p-0.5 ${
        vertical ? 'flex-col gap-0.5' : ''
      } ${className}`}
    >
      {OPTIONS.map(({ value, label, Icon }) => {
        const selected = appearance === value
        return (
          <button
            key={value}
            type="button"
            title={label}
            aria-label={label}
            aria-pressed={selected}
            onClick={() => setAppearance(value)}
            className={`flex items-center justify-center rounded-md transition-colors ${
              vertical ? 'h-7 w-full' : 'flex-1 py-1'
            } ${
              selected
                ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                : 'text-sidebar-foreground/60 hover:text-sidebar-foreground'
            }`}
          >
            <Icon className="h-4 w-4" />
          </button>
        )
      })}
    </div>
  )
}
