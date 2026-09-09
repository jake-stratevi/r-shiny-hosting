# Roadmap

Sequenced by dependency and by what a client will actually ask for. Each item
names its ADR and its trigger.

## Step 0 — finish what's started

Before anything else. All of it is verification, not new work.

| Task | Why |
|---|---|
| Confirm the dashboard stack applied — 21 resources in state | STATUS.md records this as unverified |
| Push the image to ECR | The repository is empty |
| Create a Cognito user and sign in | The waker has never run |
| **Observe the sleep cycle** | ADR-0002 is the whole cost model and has never been tested |

Half a day. Do not build anything else until the sleep cycle has been watched
falling to zero and waking again.

## Step 1 — resolve the Cognito split

**ADR-0007.** Two pools currently exist and only one should. Trivial while
nobody has signed in; painful once external partners have accounts in the wrong
one.

Do this before inviting a single user.

## Step 2 — foundations

Cheap now, tedious later, and prerequisites for every path forward.

| Task | ADR | Effort |
|---|---|---|
| Terraform state to S3 | 0009 | Hours |
| Per-app IAM task roles | 0010 | Hours |
| ALB access logs to S3 | — | Hours |
| In-app email allowlist | 0008 | Hours |

Per-app roles in particular: two apps is easy, fifteen is a slog.

## Step 3 — real usage

Deploy the model stack. Needs the full app directory and the three R changes in
ADR-0011 and ADR-0006, plus a cap on user-supplied iteration counts.

Then put both in front of real users and find out whether people actually want
this. Everything below is expensive if the answer is "three people, occasionally".

## Step 4 — before the first external client

| Task | Why |
|---|---|
| Data out of container images into S3 with KMS, per-app prefix | Rebuilds shouldn't be required to update data, and per-app roles need something to scope to |
| Federate the client's IdP into the pool | Better than passwords you create and manage |
| Secrets Manager wired as task secrets | There is currently no mechanism at all |
| Write the security questionnaire answers | Do it before you're asked; the exercise finds gaps faster than an audit |

## Step 5 — the authorizing proxy

**ADR-0008.** The largest single piece of work, and the one that retires three
hacks: the waker Lambda, the heartbeat dependency, and the ALB target-group
ceiling.

**Trigger — any one of:**

- Allowlists are being edited more than once a week
- Someone other than you needs to grant access
- A client contract requires per-app audit

Not an app count. Administrative load is the signal.

## Step 6 — portal as control plane

**ADR-0012.** Point the existing Laravel preview portal at the proxy. Its models
already cover most of this: `PreviewApp`, `PreviewRelease`, `AccessMode`,
`ClientAccessGrant`, teams, expiry, audit events.

The work is a `ContainerReleasePublisher` replacing `StaticReleasePublisher`, a
CodeBuild step in the upload pipeline, and app-state badges in the UI. Estimated
3–6 weeks for someone who knows that codebase, because roughly 70% already
exists.

## The thing to guard against

Building the enterprise version before there is an enterprise customer.

The current stack plus Step 2 is genuinely adequate for internal use and a
friendly pilot client. Let the *second* client be what forces the proxy.

## Buy versus build

Posit Connect self-hosted is a five-figure annual licence and is the name a
pharma security team recognises — which has real value when you are the small
vendor answering a 200-line questionnaire.

The case for building is that the portal already exists, and that Connect cannot
do client magic-links the way `ClientAccessGrant` does, which matters for pharma
clients who will not federate for a single dashboard.

Price Connect anyway. If procurement blocks on "who supports this", the answer
"we do" needs to be one you want to give.
