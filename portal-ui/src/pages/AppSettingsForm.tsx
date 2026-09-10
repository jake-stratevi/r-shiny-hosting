import { useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, api } from '../api/client'
import {
  IDLE_MINUTES_MAX,
  IDLE_MINUTES_MIN,
  MAX_SESSION_HOURS_MAX,
  MAX_SESSION_HOURS_MIN,
  RESERVED_ACCESS_MODES,
  type App,
  type AppPatch,
  type WritableAppStatus,
} from '../api/types'
import { EmailTagEditor } from '../components/EmailTagEditor'
import { ExpiryPicker } from '../components/ExpiryPicker'
import { InlineError, Panel } from '../components/states'

interface Draft {
  label: string
  description: string
  access_mode: string
  allowed_emails: string[]
  idle_minutes: string
  max_session_hours: string
  expires_at: number | null
  status: WritableAppStatus
}

function toDraft(app: App): Draft {
  return {
    label: app.label ?? '',
    description: app.description ?? '',
    access_mode: app.access_mode,
    allowed_emails: [...(app.allowed_emails ?? [])],
    idle_minutes: String(app.idle_minutes ?? ''),
    max_session_hours: String(app.max_session_hours ?? ''),
    expires_at: app.expires_at,
    // `expired` is the reaper's; the form can only choose between the two
    // statuses PATCH accepts.
    status: app.status === 'disabled' ? 'disabled' : 'active',
  }
}

const sameList = (a: string[], b: string[]) =>
  a.length === b.length && a.every((v, i) => v === b[i])

/** Only the fields that actually changed go on the wire. */
function buildPatch(app: App, draft: Draft): AppPatch {
  const patch: AppPatch = {}
  const original = toDraft(app)

  if (draft.label !== original.label) patch.label = draft.label
  if (draft.description !== original.description) patch.description = draft.description
  if (draft.access_mode !== original.access_mode) {
    patch.access_mode = draft.access_mode as 'all_users' | 'users'
  }
  if (!sameList(draft.allowed_emails, original.allowed_emails)) {
    patch.allowed_emails = draft.allowed_emails
  }
  if (draft.idle_minutes !== original.idle_minutes) {
    patch.idle_minutes = Number(draft.idle_minutes)
  }
  if (draft.max_session_hours !== original.max_session_hours) {
    patch.max_session_hours = Number(draft.max_session_hours)
  }
  if (draft.expires_at !== original.expires_at) patch.expires_at = draft.expires_at
  if (draft.status !== original.status) patch.status = draft.status

  return patch
}

function validate(draft: Draft): string | null {
  const idle = Number(draft.idle_minutes)
  if (!Number.isInteger(idle) || idle < IDLE_MINUTES_MIN || idle > IDLE_MINUTES_MAX) {
    return `Idle timeout must be a whole number of minutes between ${IDLE_MINUTES_MIN} and ${IDLE_MINUTES_MAX}.`
  }
  const cap = Number(draft.max_session_hours)
  if (
    !Number.isInteger(cap) ||
    cap < MAX_SESSION_HOURS_MIN ||
    cap > MAX_SESSION_HOURS_MAX
  ) {
    return `Session cap must be a whole number of hours between ${MAX_SESSION_HOURS_MIN} and ${MAX_SESSION_HOURS_MAX}.`
  }
  return null
}

export function AppSettingsForm({
  app,
  onSaved,
}: {
  app: App
  onSaved: (updated: App) => void
}) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(app))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [confirmingDisable, setConfirmingDisable] = useState(false)

  const patch = useMemo(() => buildPatch(app, draft), [app, draft])
  const dirty = Object.keys(patch).length > 0
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDraft((d) => ({ ...d, [key]: value }))
    setSaved(false)
    setError(null)
  }

  const reserved = RESERVED_ACCESS_MODES.includes(draft.access_mode as never)
  const lockingEveryoneOut =
    draft.access_mode === 'users' && draft.allowed_emails.length === 0

  async function save() {
    const message = validate(draft)
    if (message) {
      setError(message)
      return
    }
    setSaving(true)
    setError(null)
    try {
      const updated = await api.patchApp(app.host, patch)
      setSaved(true)
      setConfirmingDisable(false)
      onSaved(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setSaving(false)
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    // Disabling takes the app away from its users; make it deliberate.
    if (patch.status === 'disabled' && !confirmingDisable) {
      setConfirmingDisable(true)
      return
    }
    void save()
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <Panel className="divide-y divide-line-soft">
        <Field label="Label" hint="Shown on the tile and in this list.">
          <input
            type="text"
            value={draft.label}
            onChange={(e) => set('label', e.target.value)}
            className={`${wideInputClass} max-w-xl`}
          />
        </Field>

        <Field label="Description" hint="One or two sentences. Clients read this.">
          <textarea
            rows={3}
            value={draft.description}
            onChange={(e) => set('description', e.target.value)}
            className={`${wideInputClass} max-w-xl resize-y leading-relaxed`}
          />
        </Field>

        <Field label="Access" hint="Who may open the app. The proxy enforces this on every request.">
          <select
            value={draft.access_mode}
            onChange={(e) => set('access_mode', e.target.value)}
            className={`${inputClass} w-full max-w-xs`}
          >
            <option value="all_users">Everyone signed in</option>
            <option value="users">Specific email addresses</option>
            {RESERVED_ACCESS_MODES.map((mode) => (
              <option key={mode} value={mode} disabled>
                {mode.replace(/_/g, ' ')} — not available yet
              </option>
            ))}
          </select>

          {reserved ? (
            <p className="mt-2 text-xs text-amber-800">
              This app carries a reserved access mode, so the proxy refuses every request
              to it. Pick one of the two supported modes to fix it.
            </p>
          ) : null}

          {draft.access_mode === 'users' ? (
            <div className="mt-3 max-w-xl">
              <EmailTagEditor
                value={draft.allowed_emails}
                onChange={(next) => set('allowed_emails', next)}
              />
              {lockingEveryoneOut ? (
                <p className="mt-1.5 text-xs text-amber-800">
                  With no addresses listed, nobody can open this app.
                </p>
              ) : null}
            </div>
          ) : null}
        </Field>

        <Field
          label="Idle timeout"
          hint={`Minutes of no traffic before the task is scaled to zero (${IDLE_MINUTES_MIN}–${IDLE_MINUTES_MAX}).`}
        >
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={IDLE_MINUTES_MIN}
              max={IDLE_MINUTES_MAX}
              value={draft.idle_minutes}
              onChange={(e) => set('idle_minutes', e.target.value)}
              className={`${inputClass} w-28`}
            />
            <span className="text-sm text-faint">minutes</span>
          </div>
        </Field>

        <Field
          label="Session cap"
          hint={`Hard stop after this many hours awake (${MAX_SESSION_HOURS_MIN}–${MAX_SESSION_HOURS_MAX}; 0 means uncapped).`}
        >
          <div className="flex items-center gap-2">
            <input
              type="number"
              min={MAX_SESSION_HOURS_MIN}
              max={MAX_SESSION_HOURS_MAX}
              value={draft.max_session_hours}
              onChange={(e) => set('max_session_hours', e.target.value)}
              className={`${inputClass} w-28`}
            />
            <span className="text-sm text-faint">
              {Number(draft.max_session_hours) === 0 ? 'uncapped' : 'hours'}
            </span>
          </div>
        </Field>

        <Field label="Expiry" hint="A date, or a deliberate never. No silent default.">
          <ExpiryPicker value={draft.expires_at} onChange={(next) => set('expires_at', next)} />
          {app.status === 'expired' ? (
            <p className="mt-2 text-xs text-amber-800">
              This app has already expired. Give it a future date to bring it back.
            </p>
          ) : null}
        </Field>

        <Field label="Status" hint="Disabling keeps the row and the data; it just refuses requests.">
          <div className="flex gap-2">
            {(['active', 'disabled'] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => set('status', value)}
                className={`rounded-md border px-3 py-1.5 text-sm font-medium capitalize transition-colors ${
                  draft.status === value
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line bg-surface text-muted hover:bg-canvas'
                }`}
              >
                {value}
              </button>
            ))}
          </div>
        </Field>
      </Panel>

      {confirmingDisable ? (
        <div className="rounded-card border border-amber-300 bg-amber-50 px-5 py-4">
          <p className="text-sm font-semibold text-amber-900">
            Disable {app.label || app.host}?
          </p>
          <p className="mt-1 text-sm leading-relaxed text-amber-900/90">
            Anyone who opens it will get the refusal page, and its running task is scaled
            to zero. Nothing is deleted, and you can re-enable it here at any time.
          </p>
          <div className="mt-3.5 flex gap-2">
            <button
              type="submit"
              disabled={saving}
              className="rounded-md bg-amber-700 px-3.5 py-2 text-sm font-medium text-white transition-colors hover:bg-amber-800 disabled:opacity-60"
            >
              {saving ? 'Disabling…' : 'Yes, disable it'}
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDisable(false)}
              className="rounded-md border border-line bg-surface px-3.5 py-2 text-sm font-medium text-muted hover:bg-canvas"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {error ? <InlineError>{error}</InlineError> : null}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!dirty || saving}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => {
            setDraft(toDraft(app))
            setConfirmingDisable(false)
            setError(null)
          }}
          className="rounded-md border border-line bg-surface px-4 py-2 text-sm font-medium text-muted transition-colors hover:bg-canvas disabled:cursor-not-allowed disabled:opacity-50"
        >
          Discard
        </button>
        {saved && !dirty ? <span className="text-sm text-emerald-700">Saved.</span> : null}
        {dirty && !saved ? <span className="text-sm text-faint">Unsaved changes</span> : null}
      </div>
    </form>
  )
}

const inputClass =
  'rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-accent disabled:bg-canvas'
const wideInputClass = `${inputClass} w-full`

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-x-8 gap-y-2 px-6 py-5 md:grid-cols-[13rem_minmax(0,1fr)]">
      <div>
        <div className="text-sm font-medium text-ink">{label}</div>
        {hint ? <p className="mt-1 text-xs leading-relaxed text-faint">{hint}</p> : null}
      </div>
      <div>{children}</div>
    </div>
  )
}
