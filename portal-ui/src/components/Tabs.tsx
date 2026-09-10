import type { KeyboardEvent, ReactNode } from 'react'

export interface TabDef<K extends string> {
  key: K
  label: string
  icon?: ReactNode
}

/**
 * The underlined tab strip from assembled.work's Show page, with the ARIA
 * their version leaves to the browser: arrow keys move between tabs and each
 * panel is wired to its tab.
 */
export function Tabs<K extends string>({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: ReadonlyArray<TabDef<K>>
  value: K
  onChange: (next: K) => void
  label: string
}) {
  function onKeyDown(event: KeyboardEvent) {
    const delta = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (delta === 0) return
    event.preventDefault()
    const index = tabs.findIndex((t) => t.key === value)
    onChange(tabs[(index + delta + tabs.length) % tabs.length].key)
  }

  return (
    <div className="border-b border-line">
      <div role="tablist" aria-label={label} onKeyDown={onKeyDown} className="-mb-px flex gap-5">
        {tabs.map((tab) => {
          const active = tab.key === value
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              id={`tab-${tab.key}`}
              aria-selected={active}
              aria-controls={`panel-${tab.key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => onChange(tab.key)}
              className={`flex items-center gap-1.5 border-b-2 px-1 py-2.5 text-sm font-medium transition-colors ${
                active
                  ? 'border-accent text-accent'
                  : 'border-transparent text-muted hover:text-ink'
              }`}
            >
              {tab.icon}
              {tab.label}
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function TabPanel({
  tabKey,
  active,
  children,
}: {
  tabKey: string
  active: boolean
  children: ReactNode
}) {
  if (!active) return null
  return (
    <div role="tabpanel" id={`panel-${tabKey}`} aria-labelledby={`tab-${tabKey}`}>
      {children}
    </div>
  )
}
