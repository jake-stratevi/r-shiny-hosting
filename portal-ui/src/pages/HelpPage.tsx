import type { ReactNode } from 'react'
import { PageHeader, Panel } from '../components/states'

/**
 * The questions people actually ask, answered from what the platform does.
 *
 * Shaped after assembled.work's Help page — title, one line, then a vertical
 * stack of cards, every answer expanded, no accordion. Nothing to click means
 * nothing to miss, and the whole page is searchable with the browser's own
 * find.
 *
 * The CONTENT is ours and every claim is checked against the code or the
 * docs, with the source named in a comment beside it. Two rules for editing
 * this file:
 *
 * - If you cannot point at the line that makes an answer true, the answer
 *   does not go on the page. A confident wrong answer here costs more than a
 *   missing one, because this is the screen people read instead of asking.
 * - Where the platform cannot yet do something (shipping a code fix, most
 *   obviously), say so plainly. Describing a button that does not exist sends
 *   someone looking for it.
 */

interface HelpTopic {
  question: string
  answer: ReactNode
}

/** Inline identifiers, in the house style — see AdminAppsPage. */
function Code({ children }: { children: ReactNode }) {
  return <code className="font-mono text-xs">{children}</code>
}

const TOPICS: HelpTopic[] = [
  {
    question: 'What can I upload?',
    // validate.py's spec limits + resolveEntrypoint(); package precedence is
    // portal-p2a.md "Packages: accept either, confirm always".
    answer: (
      <>
        A <Code>.zip</Code> of the Shiny app itself, with either{' '}
        <Code>app.R</Code> or both <Code>ui.R</Code> and <Code>server.R</Code>{' '}
        at the top of the archive — or inside exactly one wrapper folder, which
        the build flattens for you. Include a <Code>renv.lock</Code> so the
        package versions are pinned; failing that a <Code>packages.txt</Code>,
        and failing both the wizard reads your <Code>library()</Code> calls and
        asks you to confirm the list it found. The archive must be under 100 MB,
        under 5,000 entries and under 250 MB unpacked, with no symlinks and no
        paths pointing outside it.
      </>
    ),
  },
  {
    question: 'What happens after I upload?',
    // portal-p2a.md "The pipeline" / "Provisioning order"; inspection is
    // client-side per portal-api.md and src/lib/inspectBundle.ts.
    answer: (
      <>
        Your browser reads the zip index first, before any bytes leave your
        machine, and shows you the entrypoint and packages it found. The file
        then goes straight to S3 through a single-use upload link — the portal
        never handles it. CodeBuild re-checks the bundle, builds an image from a
        pinned <Code>rocker/r-ver:4.4.1</Code> base and a pinned package
        snapshot, and then the app is provisioned with its own container
        registry, its own IAM role and a service running zero tasks. Expect
        10–20 minutes for a first build; installing R packages is nearly all of
        it.
      </>
    ),
  },
  {
    question: 'Why does my app take 30–60 seconds to open the first time?',
    // ADR-0002 (the cost model) + proxy.md "Wake-on-request".
    answer: (
      <>
        Because it was asleep. An app that nobody is using runs no tasks at all,
        and the first request wakes it: the proxy asks for one task and serves a
        starting page that refreshes itself until the container answers. 30–60
        seconds is the normal cold start here, most of it spent pulling a 2–3 GB
        R image. This is the trade that makes the platform affordable — a
        sleeping app bills nothing but its share of the load balancer.
      </>
    ),
  },
  {
    question: 'When does an app go to sleep?',
    // proxy-app/README.md "The force-sleep cap (max_session_hours, C1)" and
    // the 2026-09-11 incident in STATUS.md "Drift you need to know about".
    answer: (
      <>
        Two separate clocks, and the second one is the surprise. The idle
        timeout sleeps an app after its configured number of minutes with no
        request and no open browser session. Above that sits a session cap on
        continuous awake time, which scales the app to zero even while you are
        using it — it exists because a tab left open keeps an expensive app
        awake indefinitely, and it has already ended a model run mid-run. If
        you are starting something long, check the cap on the app first, and
        note that a single computation that blocks for more than about an hour
        loses its connection to the load balancer whatever the cap says.
      </>
    ),
  },
  {
    question: 'Who can see my app?',
    // access decision in proxy_app/access.py + portal-p2a.md "Hostnames are
    // policed, and unguessable".
    answer: (
      <>
        Only the people its entitlement names. Every request is signed in at
        the load balancer, and then the proxy checks that app's entitlement
        before it forwards anything: an app is either open to any signed-in
        platform user or limited to a list of addresses, and anyone else gets a
        refusal and a line in the audit trail. App hostnames also carry six
        random characters, so an address cannot be guessed or stumbled on — but
        that is a second fence, not the control. Entitlement is the control,
        and forwarding a link to someone does not grant it.
      </>
    ),
  },
  {
    question: 'What happens when an app expires?',
    // proxy.md "Expiry enforcement (reaper phase 1)"; revival needs both a
    // future expires_at and status active (registry.App.is_expired plus the
    // access decision's status check).
    answer: (
      <>
        Each app carries an expiry date or an explicit “never”. Once the date
        passes, the proxy refuses every request with an expired page and the
        service is scaled to zero — there is no cold start to wait through,
        because nothing has to start in order to say no. Nothing is deleted:
        the app’s row and its built image stay, so an administrator can give it
        a new date and set its status back to active from Manage apps.
      </>
    ),
  },
  {
    question: 'How do I update an app’s code?',
    // ROADMAP.md step "Releases, rollback and delete-purge (P2b)" and the
    // 2026-09-11 r2 deployment recorded in STATUS.md. Deliberately blunt.
    answer: (
      <>
        You can’t, not from here — creation is one-way today, and there is no
        release or rollback button to find. Shipping a fix to a deployed app
        means someone running the build, registering a new task definition and
        updating the service by hand, and that has to be arranged rather than
        self-served. It also leaves the release number shown in the portal
        behind what the app is really running, so treat that field as a hint
        and not as evidence. Self-service releases are the next thing on the
        platform’s list; until they land, budget real time for a code change.
      </>
    ),
  },
  {
    question: 'My app fails when I press a button in it — what do I check?',
    // GOTCHAS.md "The build's smoke test proves the app SERVES, not that it
    // RUNS" + "Log-group note" + "A base R package still has to be library()d".
    answer: (
      <>
        The logs, first: CloudWatch log group <Code>/ecs/shiny/apps</Code>, one
        stream per running task, named{' '}
        <Code>&lt;app-key&gt;/app/&lt;task-id&gt;</Code>. A green build proves
        very little about this — the smoke test starts the container and waits
        for the home page to answer, so anything that only runs behind a button
        is untested until a person presses it. The usual cause is a function
        from a package the session never attached: a package can be present in
        the image and still be missing from your session unless something calls{' '}
        <Code>library()</Code> on it.
      </>
    ),
  },
  {
    question: 'What does my R code need to do differently here?',
    // ADR-0011 / GOTCHAS.md on detectCores(); GOTCHAS.md on base packages;
    // STATUS.md on chunking parLapply and calling incProgress.
    answer: (
      <>
        Read the worker count from the <Code>SHINY_CPU_WORKERS</Code>{' '}
        environment variable instead of calling{' '}
        <Code>parallel::detectCores()</Code>, which reports the host machine’s
        cores rather than your task’s and will start more R processes than the
        container can feed. Declare every package your app attaches so the
        image is reproducible, except the base packages that ship with R —
        those must stay off the list, because installing them fails the build,
        but your code still has to attach them. And chunk long computations so
        they report progress, rather than blocking in one call for an hour.
      </>
    ),
  },
]

export function HelpPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="Help"
        description="How this platform works, and what to check when an app misbehaves."
      />

      <div className="flex flex-col gap-3">
        {TOPICS.map((topic) => (
          <Panel key={topic.question} className="px-5 py-4">
            <h2 className="text-sm font-semibold text-foreground">{topic.question}</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {topic.answer}
            </p>
          </Panel>
        ))}
      </div>
    </div>
  )
}
