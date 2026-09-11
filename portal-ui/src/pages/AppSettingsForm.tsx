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
import { Button } from '../components/Button'
import { EmailTagEditor } from '../components/EmailTagEditor'
import { ExpiryPicker } from '../components/ExpiryPicker'
import { CheckIcon } from '../components/icons'
import { InlineError, SectionCard } from '../components/states'

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

/**
 * The PATCH form. assembled.work splits this across one card per concern
 * (Details / Access / Domain / Danger zone), each saving on its own; ours is
 * a single diffed PATCH, so the cards group the fields and one sticky bar
 * owns the save.
 */
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
    <form onSubmit={submit} className="space-y-5">
      <SectionCard
        title="Details"
        description="What the tile says. Clients read both of these."
        bodyClassName="divide-y divide-border/60"
      >
        <Field label="Label" hint="Shown on the tile and in the admin list.">
          <input
            type="text"
            value={draft.label}
            onChange={(e) => set('label', e.target.value)}
            className={`${inputClass} w-full max-w-xl`}
          />
        </Field>

        <Field label="Description" hint="One or two sentences.">
          <textarea
            rows={3}
            value={draft.description}
            onChange={(e) => set('description', e.target.value)}
            className={`${inputClass} w-full max-w-xl resize-y leading-relaxed`}
          />
        </Field>
      </SectionCard>

      <SectionCard
        title="Access"
        description="Everyone signs in through Cognito first. This narrows it from there — the proxy enforces it on every request."
        bodyClassName="divide-y divide-border/60"
      >
        <Field label="Who can open it">
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
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">
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
                <p className="mt-1.5 text-xs text-amber-800 dark:text-amber-300">
                  With no addresses listed, nobody can open this app.
                </p>
              ) : (
                <p className="mt-1.5 text-xs text-muted-foreground/70">
                  {draft.allowed_emails.length}{' '}
                  {draft.allowed_emails.length === 1 ? 'person' : 'people'} can open it.
                </p>
              )}
            </div>
          ) : null}
        </Field>
      </SectionCard>

      <SectionCard
        title="Runtime"
        description="How long a task stays up. Fargate bills per second, so these are the cost dials."
        bodyClassName="divide-y divide-border/60"
      >
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
            <span className="text-sm text-muted-foreground/70">minutes</span>
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
            <span className="text-sm text-muted-foreground/70">
              {Number(draft.max_session_hours) === 0 ? 'uncapped' : 'hours'}
            </span>
          </div>
        </Field>
      </SectionCard>

      <SectionCard
        title="Lifecycle"
        description="When access ends, and whether the app answers at all."
        bodyClassName="divide-y divide-border/60"
      >
        {/* NOTE on the expired-app message below. Do NOT restore the old
            "give it a future date to bring it back" wording, and do not tell
            the admin to set Status to Active either. Neither works today:

              - `access.decide` refuses on `status == "expired"` OR a lapsed
                date, so a future date alone is not enough, and
              - `toDraft` maps any non-`disabled` status to `active`, so an
                expired app's Status control ALREADY reads Active,
                `buildPatch` sees no change, and `status` never goes on the
                wire. PATCH would accept `active` -- the form never sends it.

            So an expired app cannot currently be revived from this screen at
            all. Until that is fixed (ROADMAP Step 4b), say so rather than
            describing a recovery that silently does nothing. */}
        <Field label="Expiry" hint="A date, or a deliberate never. No silent default.">
          <ExpiryPicker value={draft.expires_at} onChange={(next) => set('expires_at', next)} />
          {app.status === 'expired' ? (
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-300">
              This app has expired and <strong>cannot be brought back from this
              screen yet</strong> — changing the date here will not revive it.
              Reviving it currently needs a direct write to the app&rsquo;s row.
            </p>
          ) : null}
        </Field>

        <Field label="Status" hint="Disabling keeps the row and the data; it just refuses requests.">
          <div className="flex gap-2">
            {(['active', 'disabled'] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={draft.status === value}
                onClick={() => set('status', value)}
                className={`rounded-md border px-3 py-1.5 text-sm font-medium capitalize transition-colors ${
                  draft.status === value
                    ? 'border-azure bg-azure/10 text-azure'
                    : 'border-border bg-card text-muted-foreground hover:bg-background'
                }`}
              >
                {value}
              </button>
            ))}
          </div>
        </Field>
      </SectionCard>

      {confirmingDisable ? (
        <div className="rounded-lg border border-amber-300 dark:border-amber-400/30 bg-amber-50 dark:bg-amber-400/10 px-5 py-4">
          <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">
            Disable {app.label || app.host}?
          </p>
          <p className="mt-1 text-sm leading-relaxed text-amber-900/90 dark:text-amber-200/90">
            Anyone who opens it will get the refusal page, and its running task is scaled
            to zero. Nothing is deleted, and you can re-enable it here at any time.
          </p>
          <div className="mt-3.5 flex gap-2">
            <Button type="submit" variant="danger" disabled={saving}>
              {saving ? 'Disabling…' : 'Yes, disable it'}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => setConfirmingDisable(false)}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {error ? <InlineError>{error}</InlineError> : null}

      {/* Sticky, so the save is reachable from anywhere in a long form. */}
      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-3 rounded-t-lg border-t border-border/60 bg-background/95 px-1 py-4 backdrop-blur">
        <Button type="submit" disabled={!dirty || saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={!dirty || saving}
          onClick={() => {
            setDraft(toDraft(app))
            setConfirmingDisable(false)
            setError(null)
          }}
        >
          Discard
        </Button>
        {saved && !dirty ? (
          <span className="flex items-center gap-1.5 text-sm text-emerald-700 dark:text-emerald-300">
            <CheckIcon className="h-4 w-4" />
            Saved.
          </span>
        ) : null}
        {dirty && !saved ? (
          <span className="text-sm text-muted-foreground/70">
            {Object.keys(patch).length}{' '}
            {Object.keys(patch).length === 1 ? 'unsaved change' : 'unsaved changes'}
          </span>
        ) : null}
      </div>
    </form>
  )
}

const inputClass =
  'rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-azure disabled:bg-background'

/**
 * The two-column definition layout: the label and its explanation on the
 * left, the control on the right, so a form reads as a list of decisions.
 */
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
    <div className="grid gap-x-8 gap-y-2 px-5 py-5 md:grid-cols-field">
      <div>
        <div className="text-sm font-medium text-foreground">{label}</div>
        {hint ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground/70">{hint}</p> : null}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  )
}
