import { useId, useRef, useState, type ClipboardEvent, type KeyboardEvent } from 'react'

/** The API's only rule: lowercased, and each entry must contain "@". */
export function normaliseEmail(raw: string): string {
  return raw.trim().toLowerCase()
}

export function isAcceptableEmail(raw: string): boolean {
  const value = normaliseEmail(raw)
  if (!value.includes('@')) return false
  const [local, ...rest] = value.split('@')
  return local.length > 0 && rest.length === 1 && rest[0].length > 0 && !/\s/.test(value)
}

/** Split a pasted blob on commas, semicolons, and whitespace. */
export function splitCandidates(raw: string): string[] {
  return raw
    .split(/[\s,;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

interface Props {
  value: string[]
  onChange: (next: string[]) => void
  disabled?: boolean
  id?: string
  describedBy?: string
}

/**
 * Tag-style editor for `allowed_emails`. Enter / comma / blur commits; paste
 * accepts a whole column out of a spreadsheet; Backspace on an empty field
 * removes the last address.
 */
export function EmailTagEditor({ value, onChange, disabled, id, describedBy }: Props) {
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const generatedId = useId()
  const inputId = id ?? generatedId
  const errorId = `${inputId}-error`

  function commit(raw: string): boolean {
    const candidates = splitCandidates(raw)
    if (candidates.length === 0) return true

    const accepted: string[] = []
    const rejected: string[] = []
    for (const candidate of candidates) {
      if (isAcceptableEmail(candidate)) accepted.push(normaliseEmail(candidate))
      else rejected.push(candidate)
    }

    if (accepted.length > 0) {
      const merged = [...value]
      for (const email of accepted) if (!merged.includes(email)) merged.push(email)
      if (merged.length !== value.length) onChange(merged)
    }

    if (rejected.length > 0) {
      setError(
        rejected.length === 1
          ? `“${rejected[0]}” is not an email address.`
          : `${rejected.length} entries were not email addresses.`,
      )
      setDraft(rejected.join(', '))
      return false
    }

    setError(null)
    setDraft('')
    return true
  }

  function remove(email: string) {
    onChange(value.filter((e) => e !== email))
    setError(null)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      // Always swallow Enter: this field lives inside a form and Enter here
      // means "add this address", never "save the app".
      event.preventDefault()
      if (draft.trim() !== '') commit(draft)
      return
    }
    if (event.key === 'Tab' && draft.trim() !== '') {
      // Commit, but let focus move on -- blur would catch it anyway.
      commit(draft)
      return
    }
    if (event.key === 'Backspace' && draft === '' && value.length > 0) {
      event.preventDefault()
      remove(value[value.length - 1])
    }
  }

  function onPaste(event: ClipboardEvent<HTMLInputElement>) {
    const text = event.clipboardData.getData('text')
    if (!/[\s,;]/.test(text)) return
    event.preventDefault()
    commit(`${draft} ${text}`)
  }

  return (
    <div>
      <div
        className={`flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-md border bg-card px-2 py-1.5 transition-colors ${
          error ? 'border-red-300 dark:border-red-400/30' : 'border-border focus-within:border-azure'
        } ${disabled ? 'cursor-not-allowed bg-background opacity-60' : ''}`}
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((email) => (
          <span
            key={email}
            className="inline-flex items-center gap-1 rounded bg-accent py-1 pl-2 pr-1 text-xs font-medium text-foreground"
          >
            {email}
            <button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${email}`}
              onClick={(e) => {
                e.stopPropagation()
                remove(email)
              }}
              className="rounded px-1 text-muted-foreground/70 transition-colors hover:bg-card hover:text-foreground disabled:cursor-not-allowed"
            >
              ×
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          id={inputId}
          type="text"
          value={draft}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          aria-describedby={[describedBy, error ? errorId : null].filter(Boolean).join(' ') || undefined}
          placeholder={value.length === 0 ? 'name@example.com' : 'Add another…'}
          className="min-w-[12rem] flex-1 bg-transparent px-1 py-1 text-sm text-foreground outline-none placeholder:text-muted-foreground/70 disabled:cursor-not-allowed"
          onChange={(e) => {
            setDraft(e.target.value)
            if (error) setError(null)
          }}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          onBlur={() => {
            if (draft.trim() !== '') commit(draft)
          }}
        />
      </div>
      {error ? (
        <p id={errorId} role="alert" className="mt-1.5 text-xs text-red-700 dark:text-red-300">
          {error}
        </p>
      ) : null}
    </div>
  )
}
