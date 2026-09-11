import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ApiError, api, uploadToS3 } from '../api/client'
import {
  IDLE_MINUTES_MAX,
  IDLE_MINUTES_MIN,
  MAX_SESSION_HOURS_MAX,
  MAX_SESSION_HOURS_MIN,
  MAX_ZIP_MB,
  RESERVED_ACCESS_MODES,
  TASK_SIZES,
  taskSize,
  type AccessMode,
  type TaskSizeId,
} from '../api/types'
import { Button } from '../components/Button'
import { EmailTagEditor } from '../components/EmailTagEditor'
import { ExpiryPicker } from '../components/ExpiryPicker'
import { Stepper } from '../components/Stepper'
import { ZipDropZone } from '../components/ZipDropZone'
import {
  AlertIcon,
  ArchiveIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  CalendarIcon,
  CheckIcon,
  PackageIcon,
  RocketIcon,
  SlidersIcon,
  UsersIcon,
} from '../components/icons'
import { InlineError, Notice, PageHeader, SectionCard } from '../components/states'
import { useKeyAvailability } from '../hooks/useKeyAvailability'
import {
  DEFAULT_SUFFIX_CHARS,
  WIZARD_STEPS,
  checkZip,
  emptyDraft,
  humanSize,
  localHostPreview,
  packagesFrom,
  stepBlocker,
  stepIndex,
  suggestKey,
  toCreateBody,
  type CreateDraft,
  type KeyCheck,
  type StepId,
} from '../lib/createDraft'
import { InspectionError, inspectBundle, sourceLabel } from '../lib/inspectBundle'
import { accessSummary, expiryPhrase, expirySummary } from '../lib/appDisplay'
import { defaultExpiryEpoch, formatDate } from '../lib/time'

const inputClass =
  'rounded-md border border-border bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors focus:border-azure disabled:bg-background disabled:text-muted-foreground/70'

/**
 * The four-step create wizard plus a review, behind "+ New app".
 *
 * Structure ported from assembled.work's `previews/Create.vue`: a stepper, one
 * card per step, per-step validation that gates a single Continue button, a
 * live availability check on the name, a drag-and-drop zip zone, and a review
 * that restates every choice before the irreversible click. What is ours: the
 * fields (task size, idle timeout, session cap), the honest upload progress,
 * the package confirmation, and the rule that expiry has no default.
 */
export function NewAppWizard() {
  const navigate = useNavigate()
  const [draft, setDraft] = useState<CreateDraft>(emptyDraft)
  const [step, setStep] = useState<StepId>('details')
  const [showBlocker, setShowBlocker] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<ApiError | null>(null)

  const keyCheck = useKeyAvailability(draft.key)
  const blocker = stepBlocker(step, draft, keyCheck)
  const index = stepIndex(step)
  const last = step === 'review'

  const patch = useCallback((next: Partial<CreateDraft>) => {
    setDraft((d) => ({ ...d, ...next }))
    setShowBlocker(false)
  }, [])

  function goto(nextIndex: number) {
    setStep(WIZARD_STEPS[nextIndex].id)
    setShowBlocker(false)
    // Guarded: jsdom has no scrollTo, and neither do some embedded webviews.
    window.scrollTo?.({ top: 0 })
  }

  function next() {
    if (blocker) {
      setShowBlocker(true)
      return
    }
    goto(index + 1)
  }

  async function create() {
    if (blocker) {
      setShowBlocker(true)
      return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const app = await api.createApp(toCreateBody(draft))
      // 202 -> the build screen owns the wait from here.
      navigate(`/admin/apps/${encodeURIComponent(app.host)}/build`, { replace: true })
    } catch (err) {
      const error = err instanceof ApiError ? err : new ApiError(0, String(err))
      setSubmitError(error)
      // A 409 is always the key, and the key lives two steps back.
      if (error.status === 409) setStep('details')
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto w-full max-w-3xl">
      <nav className="mb-4">
        <Link
          to="/admin"
          className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-azure"
        >
          <ArrowLeftIcon className="h-4 w-4" />
          All apps
        </Link>
      </nav>

      <PageHeader
        title="New app"
        description="Upload a Shiny app and get a private, protected link. Nothing here is Terraform."
        meta="It builds in its own container with its own credentials; a created app costs nothing until someone opens it."
      />

      <Stepper
        steps={WIZARD_STEPS}
        current={index}
        label="Create app steps"
        onJump={goto}
      />

      {step === 'details' ? (
        <DetailsStep draft={draft} patch={patch} keyCheck={keyCheck} />
      ) : null}
      {step === 'upload' ? <UploadStep draft={draft} patch={patch} /> : null}
      {step === 'access' ? <AccessStep draft={draft} patch={patch} /> : null}
      {step === 'expiry' ? <ExpiryStep draft={draft} patch={patch} /> : null}
      {step === 'review' ? (
        <ReviewStep draft={draft} keyCheck={keyCheck} onEdit={(id) => goto(stepIndex(id))} />
      ) : null}

      {submitError ? (
        <div className="mt-5">
          <Notice
            tone="danger"
            title={submitError.status === 409 ? 'That key is taken' : 'The app was not created'}
          >
            {submitError.message}
          </Notice>
        </div>
      ) : null}

      {showBlocker && blocker ? (
        <div className="mt-4">
          <InlineError>{blocker}</InlineError>
        </div>
      ) : null}

      <div className="mt-6 flex items-center justify-between gap-3 border-t border-border pt-5">
        <Button
          variant="ghost"
          disabled={index === 0 || submitting}
          onClick={() => goto(index - 1)}
        >
          <ArrowLeftIcon className="h-4 w-4" />
          Back
        </Button>

        <div className="flex items-center gap-3">
          {blocker && !showBlocker ? (
            <span className="hidden text-xs text-muted-foreground/70 sm:block">{blocker}</span>
          ) : null}
          {last ? (
            <Button
              onClick={() => void create()}
              disabled={submitting || blocker !== null}
            >
              <RocketIcon className="h-4 w-4" />
              {submitting ? 'Creating…' : 'Create app'}
            </Button>
          ) : (
            // Deliberately NOT disabled: clicking it says why it will not go.
            <Button onClick={next} aria-disabled={blocker !== null}>
              Continue
              <ArrowRightIcon className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}

// --- step 1: details -------------------------------------------------------

function DetailsStep({
  draft,
  patch,
  keyCheck,
}: {
  draft: CreateDraft
  patch: (next: Partial<CreateDraft>) => void
  keyCheck: ReturnType<typeof useKeyAvailability>
}) {
  // A key typed by hand stops tracking the label, the way every slug field
  // people do not hate behaves.
  const keyTouched = useRef(false)

  return (
    <StepCard
      icon={<SlidersIcon className="h-4 w-4" />}
      title="Details"
      description="What the tile says, where it lives, and how much machine it gets."
    >
      <Field label="Label" hint="Shown on the tile and in the admin list.">
        <input
          type="text"
          autoFocus
          value={draft.label}
          aria-label="Label"
          placeholder="Treatment Pathway Dashboard"
          className={`${inputClass} w-full`}
          onChange={(e) => {
            const label = e.target.value
            patch(
              keyTouched.current
                ? { label }
                : { label, key: suggestKey(label) },
            )
          }}
        />
      </Field>

      <Field
        label="Key"
        hint="3–30 characters, lowercase letters, digits and hyphens. It becomes the hostname, which clients see."
      >
        <input
          type="text"
          value={draft.key}
          aria-label="Key"
          placeholder="treatment-pathway"
          spellCheck={false}
          autoComplete="off"
          className={`${inputClass} w-full max-w-sm font-mono`}
          onChange={(e) => {
            keyTouched.current = true
            patch({ key: e.target.value.trim().toLowerCase() })
          }}
        />
        <div className="mt-2 min-h-[1.25rem]" aria-live="polite">
          <KeyVerdict check={keyCheck} typed={draft.key.trim() !== ''} />
        </div>

        {/* The payoff, visible from the first keystroke. Built from the typed
            key while the check is in flight, then from the shape the API
            confirms — which is the authority on the domain and on how long
            the random suffix is. A refused key gets no callout: that address
            is not going to be yours. */}
        {draft.key.trim() !== '' && keyCheck.state !== 'rejected' ? (
          <div className="mt-3 max-w-md">
            <HostPreviewCallout
              preview={keyCheck.hostPreview ?? localHostPreview(draft.key)}
              suffixChars={keyCheck.suffixChars ?? DEFAULT_SUFFIX_CHARS}
            />
          </div>
        ) : null}
      </Field>

      <Field label="Description" hint="One or two sentences. Clients read this.">
        <textarea
          rows={3}
          value={draft.description}
          aria-label="Description"
          className={`${inputClass} w-full resize-y leading-relaxed`}
          onChange={(e) => patch({ description: e.target.value })}
        />
      </Field>

      <Field
        label="Task size"
        hint="Fargate bills per second, so a bigger task that finishes a CPU-bound run sooner costs about the same. Size up rather than down (ADR-0001)."
      >
        <div role="radiogroup" aria-label="Task size" className="space-y-2">
          {TASK_SIZES.map((size) => {
            const chosen = draft.size === size.id
            return (
              <label
                key={size.id}
                className={`flex cursor-pointer items-start gap-3 rounded-md border px-3.5 py-3 transition-colors ${
                  chosen
                    ? 'border-azure bg-azure/10'
                    : 'border-border bg-card hover:border-azure/40'
                }`}
              >
                <input
                  type="radio"
                  name="task-size"
                  value={size.id}
                  checked={chosen}
                  className="mt-1 h-4 w-4 accent-accent"
                  onChange={() => patch({ size: size.id as TaskSizeId })}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-foreground">
                    {size.label}
                  </span>
                  <span className="block text-xs leading-relaxed text-muted-foreground">
                    {size.blurb}
                  </span>
                  <span className="mt-0.5 block text-xs text-muted-foreground/70">{size.hourly}</span>
                </span>
              </label>
            )
          })}
        </div>
      </Field>
    </StepCard>
  )
}

function KeyVerdict({
  check,
  typed,
}: {
  check: ReturnType<typeof useKeyAvailability>
  typed: boolean
}) {
  if (!typed) return null

  if (check.state === 'checking') {
    return (
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground/70">
        <span className="h-3 w-3 animate-spin rounded-full border-2 border-border border-t-accent" />
        Checking that key…
      </p>
    )
  }
  if (check.state === 'ok') {
    // The hostname itself is not repeated here — the callout below the field
    // is where the link lives, and saying it twice made the field noisy.
    return (
      <p className="flex flex-wrap items-center gap-1.5 text-xs text-emerald-700 dark:text-emerald-300">
        <CheckIcon className="h-3.5 w-3.5" />
        That key is available.
      </p>
    )
  }
  if (check.state === 'rejected') {
    // The API's own words. See useKeyAvailability for why they are not reworded.
    return (
      <p role="alert" className="text-xs text-red-700 dark:text-red-300">
        {check.reason}
      </p>
    )
  }
  if (check.state === 'unknown') {
    return (
      <p role="alert" className="text-xs text-amber-800 dark:text-amber-300">
        That key could not be checked: {check.reason}
      </p>
    )
  }
  return null
}

// --- step 2: upload --------------------------------------------------------

function UploadStep({
  draft,
  patch,
}: {
  draft: CreateDraft
  patch: (next: Partial<CreateDraft>) => void
}) {
  // Cancels an upload whose file has been replaced mid-flight.
  const inFlight = useRef<AbortController | null>(null)

  useEffect(() => () => inFlight.current?.abort(), [])

  const start = useCallback(
    async (file: File) => {
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller

      patch({
        file,
        uploadError: null,
        uploadKey: null,
        inspection: null,
        inspectionError: null,
        packagesConfirmed: false,
        packagesText: '',
        uploadProgress: 0,
        uploadPhase: 'inspecting',
      })

      // Read the zip HERE, before the upload. The proxy is in the request
      // path for every app on the platform and must not become a worker
      // (portal-api.md, "Bundle inspection is CLIENT-SIDE"); doing it first
      // also means a bundle with no entrypoint fails in a second instead of
      // after a 100 MB PUT. It is advisory — validate.py is the authority —
      // so a failure here never blocks the upload, it just means the user
      // types the list themselves.
      try {
        const inspection = await inspectBundle(file)
        if (controller.signal.aborted) return
        patch({ inspection, packagesText: inspection.packages.join('\n') })
      } catch (err) {
        if (controller.signal.aborted) return
        patch({
          inspection: null,
          inspectionError:
            err instanceof InspectionError
              ? err.message
              : `The bundle could not be read (${String(err)}).`,
        })
      }

      patch({ uploadPhase: 'requesting' })

      try {
        const ticket = await api.createUpload(file.name, file.size, controller.signal)
        if (controller.signal.aborted) return

        patch({ uploadPhase: 'uploading' })
        await uploadToS3(ticket.url, file, {
          signal: controller.signal,
          onProgress: (fraction) => {
            if (!controller.signal.aborted) patch({ uploadProgress: fraction })
          },
        })
        if (controller.signal.aborted) return

        patch({ uploadPhase: 'ready', uploadKey: ticket.upload_key })
      } catch (err) {
        if (controller.signal.aborted) return
        if (err instanceof DOMException && err.name === 'AbortError') return
        patch({
          uploadPhase: 'failed',
          uploadError: err instanceof ApiError ? err.message : String(err),
        })
      }
    },
    [patch],
  )

  function take(file: File | null) {
    if (!file) return
    const problem = checkZip(file)
    if (problem) {
      // Rejected before we ask the API for a presigned URL at all.
      patch({
        file,
        uploadError: problem.message,
        uploadPhase: 'failed',
        uploadKey: null,
        inspection: null,
        inspectionError: null,
        packagesText: '',
        packagesConfirmed: false,
        uploadProgress: 0,
      })
      return
    }
    void start(file)
  }

  const busy =
    draft.uploadPhase === 'inspecting' ||
    draft.uploadPhase === 'requesting' ||
    draft.uploadPhase === 'uploading'

  // The zip is read before the upload, so the entrypoint and package list
  // are on screen while the bytes are still going out.
  const inspected =
    draft.uploadPhase === 'requesting' ||
    draft.uploadPhase === 'uploading' ||
    draft.uploadPhase === 'ready'

  return (
    <StepCard
      icon={<ArchiveIcon className="h-4 w-4" />}
      title="Upload"
      description={`One .zip up to ${MAX_ZIP_MB} MB. It goes straight from this browser to S3 — the portal never touches the bytes.`}
    >
      <div className="px-5 py-5">
        <ZipDropZone file={draft.file} onFile={take} busy={busy} />

        {draft.uploadError ? (
          <p role="alert" className="mt-3 text-sm text-red-700 dark:text-red-300">
            {draft.uploadError}
          </p>
        ) : null}

        {busy ? <UploadProgress draft={draft} /> : null}

        {inspected ? <BundleConfirmation draft={draft} patch={patch} /> : null}

        <p className="mt-4 text-xs leading-relaxed text-muted-foreground/70">
          Nothing is scanned for malware, deliberately — an R app is code we have
          chosen to run. Containment is the control: its own IAM role under a
          permissions boundary, its own container, no NAT egress, capped compute.
        </p>
      </div>
    </StepCard>
  )
}

/**
 * Real bytes, from XHR's own progress events. The only progress bar in this
 * feature: portal-p2a.md forbids one for the build, where nothing observable
 * maps to a percentage, but an HTTP PUT knows exactly how far it has got.
 */
function UploadProgress({ draft }: { draft: CreateDraft }) {
  const pct = Math.round(draft.uploadProgress * 100)
  const bytes = draft.file ? draft.file.size * draft.uploadProgress : 0

  const caption =
    draft.uploadPhase === 'requesting'
      ? 'Asking for an upload link…'
      : draft.uploadPhase === 'inspecting'
        ? 'Reading the bundle…'
        : `${humanSize(bytes)} of ${humanSize(draft.file?.size ?? 0)}`

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{caption}</span>
        {draft.uploadPhase === 'uploading' ? <span>{pct}%</span> : null}
      </div>
      <div
        role="progressbar"
        aria-label="Upload progress"
        aria-valuenow={draft.uploadPhase === 'uploading' ? pct : undefined}
        aria-valuemin={0}
        aria-valuemax={100}
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-border"
      >
        <div
          className="h-full rounded-full bg-accent transition-[width] duration-150"
          style={{ width: draft.uploadPhase === 'uploading' ? `${pct}%` : '100%' }}
        />
      </div>
    </div>
  )
}

/**
 * What reading the zip found, shown back for an explicit yes.
 * portal-p2a.md: "Whatever the source, show the resolved list back in the
 * wizard for explicit confirmation before the build starts" — and never
 * silently guess. Everything here is advisory: CodeBuild's validate.py
 * re-checks the bundle and is the only authority.
 */
function BundleConfirmation({
  draft,
  patch,
}: {
  draft: CreateDraft
  patch: (next: Partial<CreateDraft>) => void
}) {
  const found = draft.inspection
  const packages = packagesFrom(draft)

  return (
    <div className="mt-5 space-y-4 rounded-lg border border-border bg-background/60 p-4">
      {found ? (
        <>
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
                Entrypoint
              </dt>
              <dd className="mt-0.5 font-mono text-sm text-foreground">{found.entrypoint}</dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted-foreground/70">
                Packages read from
              </dt>
              <dd className="mt-0.5 font-mono text-sm text-foreground">
                {sourceLabel(found.source)}
              </dd>
            </div>
          </dl>

          {found.warnings.length ? (
            <ul className="space-y-1">
              {found.warnings.map((warning) => (
                <li
                  key={warning}
                  className="flex items-start gap-1.5 text-xs leading-relaxed text-amber-800 dark:text-amber-300"
                >
                  <AlertIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {warning}
                </li>
              ))}
            </ul>
          ) : null}
        </>
      ) : (
        <Notice tone="warning" title="The bundle could not be inspected">
          {draft.inspectionError ??
            'The zip could not be read in this browser, so nothing was detected.'}{' '}
          Nothing is being guessed on your behalf: list the packages the app needs
          below — the build installs exactly what you write here. The build checks
          the bundle again server-side and will say the same thing if this is a
          real problem.
        </Notice>
      )}

      <div>
        <label
          htmlFor="packages"
          className="flex items-center gap-1.5 text-sm font-medium text-foreground"
        >
          <PackageIcon className="h-4 w-4 text-muted-foreground/70" />
          R packages ({packages.length})
        </label>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground/70">
          One per line. These are recorded on the release, so a rebuild installs the
          same set. Adding one costs build minutes; removing one that is used breaks
          the app at startup.
        </p>
        <textarea
          id="packages"
          rows={6}
          value={draft.packagesText}
          spellCheck={false}
          placeholder={'shiny\ndplyr\nggplot2'}
          className={`${inputClass} mt-2 w-full resize-y font-mono text-xs leading-relaxed`}
          onChange={(e) =>
            patch({ packagesText: e.target.value, packagesConfirmed: false })
          }
        />
      </div>

      <label className="flex w-fit cursor-pointer items-start gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          checked={draft.packagesConfirmed}
          disabled={packages.length === 0}
          className="mt-0.5 h-4 w-4 rounded border-border accent-accent"
          onChange={(e) => patch({ packagesConfirmed: e.target.checked })}
        />
        <span>
          That is the right entrypoint and package list.
          <span className="mt-0.5 block text-xs text-muted-foreground/70">
            First builds take 10–20 minutes because these compile from source.
          </span>
        </span>
      </label>
    </div>
  )
}

// --- step 3: access --------------------------------------------------------

function AccessStep({
  draft,
  patch,
}: {
  draft: CreateDraft
  patch: (next: Partial<CreateDraft>) => void
}) {
  const size = taskSize(draft.size)
  const suggestsShorterIdle =
    size.suggestedIdleMinutes !== Number(draft.idle_minutes) && draft.size === 'model'

  return (
    <StepCard
      icon={<UsersIcon className="h-4 w-4" />}
      title="Access"
      description="Everyone signs in through Cognito first. This narrows it from there — the proxy enforces it on every request."
    >
      <Field label="Who can open it">
        <select
          value={draft.access_mode}
          aria-label="Who can open it"
          className={`${inputClass} w-full max-w-xs`}
          onChange={(e) => patch({ access_mode: e.target.value as AccessMode })}
        >
          <option value="all_users">Everyone signed in</option>
          <option value="users">Specific email addresses</option>
          {RESERVED_ACCESS_MODES.map((mode) => (
            <option key={mode} value={mode} disabled>
              {mode.replace(/_/g, ' ')} — not available yet
            </option>
          ))}
        </select>

        {draft.access_mode === 'users' ? (
          <div className="mt-3">
            <EmailTagEditor
              value={draft.allowed_emails}
              onChange={(allowed_emails) => patch({ allowed_emails })}
            />
            <p
              className={`mt-1.5 text-xs ${
                draft.allowed_emails.length === 0 ? 'text-amber-800 dark:text-amber-300' : 'text-muted-foreground/70'
              }`}
            >
              {draft.allowed_emails.length === 0
                ? 'With no addresses listed, nobody could open this app.'
                : `${draft.allowed_emails.length} ${
                    draft.allowed_emails.length === 1 ? 'person' : 'people'
                  } will be able to open it.`}
            </p>
          </div>
        ) : null}
      </Field>

      <Field
        label="Idle timeout"
        hint={`Minutes of no traffic before the task scales to zero (${IDLE_MINUTES_MIN}–${IDLE_MINUTES_MAX}). This is the main cost dial.`}
      >
        <div className="flex items-center gap-2">
          <input
            type="number"
            min={IDLE_MINUTES_MIN}
            max={IDLE_MINUTES_MAX}
            value={draft.idle_minutes}
            aria-label="Idle timeout"
            className={`${inputClass} w-28`}
            onChange={(e) => patch({ idle_minutes: e.target.value })}
          />
          <span className="text-sm text-muted-foreground/70">minutes</span>
        </div>
        {suggestsShorterIdle ? (
          <p className="mt-2 text-xs text-muted-foreground">
            Models usually want 10 — at {size.hourly} an idle hour is real money.{' '}
            <button
              type="button"
              className="text-azure underline underline-offset-2"
              onClick={() =>
                patch({ idle_minutes: String(size.suggestedIdleMinutes) })
              }
            >
              Use 10
            </button>
          </p>
        ) : null}
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
            aria-label="Session cap"
            className={`${inputClass} w-28`}
            onChange={(e) => patch({ max_session_hours: e.target.value })}
          />
          <span className="text-sm text-muted-foreground/70">
            {Number(draft.max_session_hours) === 0 ? 'uncapped' : 'hours'}
          </span>
        </div>
      </Field>
    </StepCard>
  )
}

// --- step 4: expiry --------------------------------------------------------

/**
 * `expires_at` must be *explicitly* present in the POST body — no default.
 * `ExpiryPicker` alone cannot express "not decided", because its null means
 * never, so the choice is gated behind two buttons and the picker appears
 * once one of them is pressed.
 */
function ExpiryStep({
  draft,
  patch,
}: {
  draft: CreateDraft
  patch: (next: Partial<CreateDraft>) => void
}) {
  return (
    <StepCard
      icon={<CalendarIcon className="h-4 w-4" />}
      title="Expiry"
      description="When access ends. There is no default here on purpose — an app that quietly outlives its engagement is how client data leaks."
    >
      <div className="px-5 py-5">
        {draft.expiryChosen ? (
          <>
            <ExpiryPicker
              value={draft.expires_at}
              onChange={(expires_at) => patch({ expires_at })}
            />
            <p className="mt-3 text-xs text-muted-foreground/70">
              {draft.expires_at === null
                ? 'It stays up until someone takes it down. An admin can set a date later.'
                : `The reaper expires it at the end of ${formatDate(draft.expires_at)}. An admin can extend it at any time.`}
            </p>
          </>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              Pick one. Most client work should have a date; internal tooling is
              usually the "never" case.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() =>
                  patch({ expiryChosen: true, expires_at: defaultExpiryEpoch() })
                }
              >
                <CalendarIcon className="h-4 w-4" />
                Set an end date
              </Button>
              <Button
                variant="outline"
                onClick={() => patch({ expiryChosen: true, expires_at: null })}
              >
                Never expires
              </Button>
            </div>
          </>
        )}
      </div>
    </StepCard>
  )
}

// --- step 5: review --------------------------------------------------------

function ReviewStep({
  draft,
  keyCheck,
  onEdit,
}: {
  draft: CreateDraft
  keyCheck: KeyCheck
  onEdit: (step: StepId) => void
}) {
  const size = taskSize(draft.size)
  const packages = packagesFrom(draft)
  const expiry = expirySummary(draft.expires_at)

  return (
    <StepCard
      icon={<RocketIcon className="h-4 w-4" />}
      title="Review and create"
      description="Creating reserves the key, provisions the app's own repository and role, and starts the build. Everything below can be changed afterwards except the key."
    >
      <div className="space-y-5 px-5 py-5">
        {/* The address is what all of this is for, so it gets top billing —
            in the same callout the Details step showed it in, random suffix
            still unminted and still saying so. */}
        <HostPreviewCallout
          preview={keyCheck.hostPreview ?? localHostPreview(draft.key)}
          suffixChars={keyCheck.suffixChars ?? DEFAULT_SUFFIX_CHARS}
        />

        <ReviewGroup title="Details" onEdit={() => onEdit('details')}>
          <ReviewRow term="Label" value={draft.label} />
          <ReviewRow term="Key" value={draft.key} mono />
          <ReviewRow term="Description" value={draft.description || '—'} />
          <ReviewRow term="Task size" value={`${size.label} · ${size.hourly}`} />
        </ReviewGroup>

        <ReviewGroup title="Bundle" onEdit={() => onEdit('upload')}>
          <ReviewRow
            term="Zip"
            value={
              draft.file ? `${draft.file.name} (${humanSize(draft.file.size)})` : '—'
            }
          />
          <ReviewRow term="Entrypoint" value={draft.inspection?.entrypoint ?? '—'} mono />
          <ReviewRow
            term={`Packages (${packages.length})`}
            value={packages.join(', ') || '—'}
          />
        </ReviewGroup>

        <ReviewGroup title="Access" onEdit={() => onEdit('access')}>
          <ReviewRow
            term="Who can open it"
            value={accessSummary({
              access_mode: draft.access_mode,
              allowed_emails: draft.allowed_emails,
            })}
          />
          {draft.access_mode === 'users' ? (
            <ReviewRow term="Addresses" value={draft.allowed_emails.join(', ')} />
          ) : null}
          <ReviewRow term="Idle timeout" value={`${draft.idle_minutes} minutes`} />
          <ReviewRow
            term="Session cap"
            value={
              Number(draft.max_session_hours) === 0
                ? 'Uncapped'
                : `${draft.max_session_hours} hours`
            }
          />
        </ReviewGroup>

        <ReviewGroup title="Expiry" onEdit={() => onEdit('expiry')}>
          <ReviewRow
            term="Access ends"
            value={
              draft.expires_at === null
                ? 'Never'
                : `${formatDate(draft.expires_at)} (${expiryPhrase(expiry).toLowerCase()})`
            }
          />
        </ReviewGroup>

        <p className="text-xs leading-relaxed text-muted-foreground/70">
          The build takes 10–20 minutes the first time, because every R package is
          compiled from source. You will be taken to a build screen that says which
          phase it is in; you can close the tab and come back to it.
        </p>
      </div>
    </StepCard>
  )
}

function ReviewGroup({
  title,
  onEdit,
  children,
}: {
  title: string
  onEdit: () => void
  children: ReactNode
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3 border-b border-border/60 pb-1.5">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
          {title}
        </h3>
        <button
          type="button"
          onClick={onEdit}
          className="text-xs text-azure underline underline-offset-2"
        >
          Edit {title.toLowerCase()}
        </button>
      </div>
      <dl className="mt-2 space-y-1.5 text-sm">{children}</dl>
    </div>
  )
}

function ReviewRow({
  term,
  value,
  mono,
}: {
  term: string
  value: string
  mono?: boolean
}) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-0.5">
      <dt className="shrink-0 text-muted-foreground">{term}</dt>
      <dd
        className={`min-w-0 max-w-[28rem] break-words text-right text-foreground ${
          mono ? 'font-mono text-xs' : ''
        }`}
      >
        {value}
      </dd>
    </div>
  )
}

// --- shared chrome ---------------------------------------------------------

function StepCard({
  icon,
  title,
  description,
  children,
}: {
  icon: ReactNode
  title: string
  description: string
  children: ReactNode
}) {
  return (
    <SectionCard
      title={
        <span className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className="flex h-7 w-7 items-center justify-center rounded-md bg-azure/10 text-azure"
          >
            {icon}
          </span>
          {title}
        </span>
      }
      description={description}
      bodyClassName="divide-y divide-border/60"
    >
      {children}
    </SectionCard>
  )
}

/**
 * Label above input, helper text between them — assembled.work's `Create.vue`
 * (`<div class="grid gap-2">`, Label, Input, help). Deliberately NOT the
 * two-column `grid-cols-field` layout `AppSettingsForm` uses: that one is for
 * a settings page, where the reader is scanning a list of existing decisions
 * for the one they came to change. Here they are answering questions in
 * order, and a single column is the shorter path down the card.
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
    <div className="px-5 py-5">
      <div className="text-sm font-medium text-foreground">{label}</div>
      {hint ? (
        <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground/70">{hint}</p>
      ) : null}
      <div className="mt-2.5 min-w-0">{children}</div>
    </div>
  )
}

/**
 * The link this whole wizard is for, as a tinted azure callout rather than a
 * caption — their `Create.vue` shows it from the first keystroke, and the
 * review step restates it in the same shape.
 */
/**
 * The address the app will get — shown honestly, which means showing that
 * part of it does not exist yet.
 *
 * A created app lives at `<key>-<random>.tools.stratevi.com`. The random
 * half is minted on the server when the app is created, so that someone who
 * is not on the platform cannot find an app by guessing its name. (It is
 * defence in depth: a guesser is refused by the access list anyway.)
 *
 * That means this callout CANNOT show the real link, and must not pretend
 * to: the suffix does not exist until Create is pressed. So it shows the
 * shape with the random part marked as such, says in one line where the
 * rest comes from, and is deliberately not a clickable link. The real
 * address — clickable — is on the build screen straight afterwards.
 */
function HostPreviewCallout({
  preview,
  suffixChars,
}: {
  preview: string
  suffixChars: number
}) {
  const [label, ...domain] = preview.split('.')
  const placeholder = 'x'.repeat(suffixChars)
  const stem = label.endsWith(`-${placeholder}`)
    ? label.slice(0, -placeholder.length)
    : `${label}-`

  return (
    <div className="rounded-lg border border-azure/30 bg-azure/[0.07] px-3.5 py-2.5 dark:border-azure/25 dark:bg-azure/10">
      <p className="text-xs text-azure">Your app’s address</p>
      <p className="scroll-x-thin whitespace-nowrap font-mono text-sm text-foreground">
        https://{stem}
        <span
          className="rounded-sm bg-azure/20 px-0.5 text-muted-foreground"
          title="A random suffix, added when the app is created"
        >
          {placeholder}
        </span>
        .{domain.join('.')}
      </p>
      <p className="mt-1.5 text-xs text-muted-foreground">
        The last {suffixChars} characters are random and are added when you
        create the app, so the address can’t be guessed by anyone who wasn’t
        sent it. You’ll get the real link on the next screen.
      </p>
    </div>
  )
}
