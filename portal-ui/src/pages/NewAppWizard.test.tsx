import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { MAX_ZIP_BYTES } from '../api/types'
import { loadJSZip } from '../lib/inspectBundle'
import { renderPage } from '../test/render'
import { NewAppWizard } from './NewAppWizard'

const validateKey = vi.hoisted(() => vi.fn())
const createUpload = vi.hoisted(() => vi.fn())
const createApp = vi.hoisted(() => vi.fn())
const uploadToS3 = vi.hoisted(() => vi.fn())

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return {
    ...actual,
    uploadToS3,
    api: { ...actual.api, validateKey, createUpload, createApp },
  }
})

/**
 * A real .zip, because inspection is real: there is no inspect route to stub
 * (portal-api.md, "Bundle inspection is CLIENT-SIDE"), so the wizard reads
 * whatever bytes it is handed.
 */
async function bundle(
  files: Record<string, string> = {
    'app.R': 'library(shiny)',
    'renv.lock': JSON.stringify({ Packages: { shiny: {}, dplyr: {} } }),
  },
  name = 'app.zip',
): Promise<File> {
  const JSZip = await loadJSZip()
  const zip = new JSZip()
  for (const [path, content] of Object.entries(files)) zip.file(path, content)
  const bytes = await zip.generateAsync({ type: 'arraybuffer' })
  return new File([bytes], name, { type: 'application/zip' })
}

/** Not a zip at all — for the checks that run before anything is read. */
function fakeFile(name = 'app.zip', size = 3 * 1024 * 1024): File {
  const file = new File(['PK'], name, { type: 'application/zip' })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

/** The wizard plus a sentinel for the route the 202 sends us to. */
function renderWizard() {
  return renderPage(
    <Routes>
      <Route path="/admin/apps/new" element={<NewAppWizard />} />
      <Route path="/admin/apps/:host/build" element={<p>Build screen</p>} />
    </Routes>,
    { route: '/admin/apps/new' },
  )
}

beforeEach(() => {
  for (const mock of [validateKey, createUpload, createApp, uploadToS3]) {
    mock.mockReset()
  }
  validateKey.mockResolvedValue({
    ok: true,
    // The SHAPE, not a hostname: the real suffix is minted server-side on
    // create, so the wizard can only ever show where it will go.
    host_preview: 'access-atlas-xxxxxx.tools.stratevi.com',
    suffix_chars: 6,
  })
  createUpload.mockResolvedValue({
    upload_key: 'uploads/abc.zip',
    url: 'https://s3.example/presigned',
    expires_in: 900,
  })
  uploadToS3.mockImplementation(
    async (_url: string, _file: File, opts: { onProgress?: (n: number) => void }) => {
      opts.onProgress?.(0.5)
      opts.onProgress?.(1)
    },
  )
  createApp.mockResolvedValue({
    // What the API actually returns: the suffixed, unguessable hostname.
    host: 'access-atlas-k4mr2t.tools.stratevi.com',
    status: 'building',
  })
})

const continueButton = () => screen.getByRole('button', { name: /Continue/ })

/**
 * The address line in the callout. It is deliberately not one text node --
 * the random part is marked up separately so the user can see which half of
 * the hostname does not exist yet -- so it is matched on textContent.
 */
const addressLine = (address: string) =>
  screen.findByText(
    (_content, element) =>
      element?.tagName === 'P' && element.textContent === address,
  )

const queryAddressLine = (address: string) =>
  screen.queryByText(
    (_content, element) =>
      element?.tagName === 'P' && element.textContent === address,
  )

/**
 * The blocker line under the card. The key field can raise an alert of its
 * own, and the blocker is always the last one in the document.
 */
const blocker = () => screen.getAllByRole('alert').at(-1)

/** The package box, by its label — its value has newlines, which ByDisplayValue normalises away. */
const packageBox = () => screen.findByLabelText(/R packages/)

async function fillDetails(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Label'), 'Access Atlas')
  // The key is suggested from the label, then checked after the debounce.
  await waitFor(() => expect(validateKey).toHaveBeenCalled())
  await screen.findByText('That key is available.')
}

async function uploadBundle(
  user: ReturnType<typeof userEvent.setup>,
  file?: File | Promise<File>,
) {
  await user.upload(screen.getByLabelText('App bundle (.zip)'), (await file) ?? (await bundle()))
}

describe('NewAppWizard — key availability', () => {
  it('debounces: a burst of typing produces one validate-key call, for the final value', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'atlas')

    await waitFor(() => expect(validateKey).toHaveBeenCalledTimes(1))
    expect(validateKey).toHaveBeenCalledWith('atlas', expect.anything())
  })

  it('shows the address the key will produce, suffix and all, from the first keystroke', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'access-atlas')

    // Projected from what has been typed while the check is still in flight…
    expect(screen.getByText('Your app’s address')).toBeInTheDocument()
    // …then confirmed against the shape the API returns. The `xxxxxx` is the
    // honest part: that half of the hostname does not exist yet.
    expect(
      await addressLine('https://access-atlas-xxxxxx.tools.stratevi.com'),
    ).toBeInTheDocument()
  })

  it('tells the user the suffix is random and added on create', async () => {
    // Otherwise `xxxxxx` reads as a bug rather than as the security control
    // it is -- and the user would expect to be able to share this address.
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'access-atlas')

    expect(
      await screen.findByText(/random and are added when you create the app/),
    ).toBeInTheDocument()
    expect(screen.getByText(/can’t be guessed/)).toBeInTheDocument()
  })

  it('drops a stale verdict the moment the key changes again', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'atlas')
    await screen.findByText('That key is available.')

    validateKey.mockResolvedValue({ ok: false, reason: 'Taken.' })
    await user.type(screen.getByLabelText('Key'), '-two')

    // The green line must not survive into the new key's check, and neither
    // may the host the old key resolved to.
    await waitFor(() =>
      expect(screen.queryByText('That key is available.')).not.toBeInTheDocument(),
    )
    expect(
      queryAddressLine('https://access-atlas-xxxxxx.tools.stratevi.com'),
    ).not.toBeInTheDocument()
    expect(await screen.findByText('Taken.')).toBeInTheDocument()
  })

  it('repeats the API’s refusal verbatim, denylist wording and all', async () => {
    const reason = 'That name can’t be used in a public hostname — pick a project codename.'
    validateKey.mockResolvedValue({ ok: false, reason })
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'tarpeyo-uptake')
    expect(await screen.findByText(reason)).toBeInTheDocument()
  })

  it('does not call validate-key for an empty field', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Label'), 'x')
    await user.clear(screen.getByLabelText('Key'))

    await new Promise((r) => setTimeout(r, 600))
    expect(validateKey).not.toHaveBeenCalledWith('', expect.anything())
  })
})

describe('NewAppWizard — per-step gating', () => {
  it('will not leave Details while the key is refused, and says why', async () => {
    validateKey.mockResolvedValue({ ok: false, reason: 'That key is reserved.' })
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Label'), 'Access Atlas')
    // Said twice on purpose: under the field, and as the hint by Continue.
    await screen.findAllByText('That key is reserved.')

    await user.click(continueButton())

    expect(blocker()).toHaveTextContent('That key is reserved.')
    expect(screen.getByLabelText('Key')).toBeInTheDocument()
    expect(screen.queryByLabelText('App bundle (.zip)')).not.toBeInTheDocument()
  })

  it('will not leave Details with no label', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.type(screen.getByLabelText('Key'), 'access-atlas')
    await screen.findByText('That key is available.')

    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/label/i)
  })

  it('will not leave Upload until the package list is confirmed', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)

    // The list is on screen, but nothing has been agreed to yet.
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/confirm/i)

    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())
    expect(await screen.findByLabelText('Who can open it')).toBeInTheDocument()
  })

  it('will not leave Access with an allowlist nobody is on', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())

    await screen.findByLabelText('Who can open it')
    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/locks everybody out/)
  })
})

describe('NewAppWizard — upload', () => {
  it('rejects an over-size zip client-side, without asking for a presigned URL', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user, fakeFile('huge.zip', MAX_ZIP_BYTES + 1))

    await waitFor(() => expect(blocker()).toHaveTextContent(/over the 100 MB limit/))
    expect(createUpload).not.toHaveBeenCalled()
    expect(uploadToS3).not.toHaveBeenCalled()
  })

  it('rejects a file that is not a zip', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user, fakeFile('app.tar.gz', 1024))

    await waitFor(() => expect(blocker()).toHaveTextContent(/not a \.zip/i))
    expect(createUpload).not.toHaveBeenCalled()
  })

  it('PUTs an acceptable zip to the presigned URL and shows what the zip contains', async () => {
    const user = userEvent.setup()
    renderWizard()

    const file = await bundle()
    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user, file)

    await waitFor(() =>
      expect(createUpload).toHaveBeenCalledWith('app.zip', file.size, expect.anything()),
    )
    expect(uploadToS3).toHaveBeenCalledWith(
      'https://s3.example/presigned',
      expect.any(File),
      expect.objectContaining({ onProgress: expect.any(Function) }),
    )
    expect(await screen.findByText('app.R')).toBeInTheDocument()
    expect(screen.getByText('renv.lock')).toBeInTheDocument()
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
  })

  it('reads the bundle before the upload, not after it', async () => {
    // The proxy never sees the zip's contents (portal-api.md, "Bundle
    // inspection is CLIENT-SIDE"), so the entrypoint and the list are on
    // screen while the presigned URL is still being asked for.
    let release: (ticket: unknown) => void = () => {}
    createUpload.mockImplementation(
      () => new Promise((resolve) => {
        release = resolve
      }),
    )

    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)

    expect(await screen.findByText('app.R')).toBeInTheDocument()
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    expect(uploadToS3).not.toHaveBeenCalled()

    release({ upload_key: 'uploads/abc.zip', url: 'https://s3.example/presigned', expires_in: 900 })
    await waitFor(() => expect(uploadToS3).toHaveBeenCalled())
  })

  it('reports the wrapper directory the build will flatten', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(
      user,
      bundle({ 'my-app/app.R': 'library(shiny)', 'my-app/packages.txt': 'shiny\nDT\n' }),
    )

    expect(await screen.findByText(/wrapper directory \(my-app\/\)/)).toBeInTheDocument()
    expect(screen.getByText('packages.txt')).toBeInTheDocument()
    expect(await packageBox()).toHaveValue('shiny\nDT')
  })

  it('falls back to a hand-written list when the zip cannot be read, and says why', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    // A .zip in name only: inspection fails, the upload still goes ahead,
    // because validate.py — not this — decides what is a valid bundle.
    await uploadBundle(user, fakeFile('app.zip', 2048))

    expect(
      await screen.findByText('The bundle could not be inspected'),
    ).toBeInTheDocument()
    expect(screen.getByText(/could not be read as a zip archive/)).toBeInTheDocument()
    await waitFor(() => expect(createUpload).toHaveBeenCalled())

    // Nothing is guessed: the list is empty and Continue says so.
    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/list the r packages/i)

    // The manual path still works, and still needs an explicit confirmation.
    await user.type(await packageBox(), 'shiny')
    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/confirm/i)
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())
    expect(await screen.findByLabelText('Who can open it')).toBeInTheDocument()
  })

  it('will not continue on a bundle with no entrypoint until the user says so', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user, bundle({ 'notes.txt': 'no shiny app in here' }))

    expect(
      await screen.findByText(/No app\.R or ui\.R\/server\.R found/),
    ).toBeInTheDocument()
  })
})

describe('NewAppWizard — expiry has no default', () => {
  it('offers neither option pre-selected, and blocks until one is chosen', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())

    await screen.findByLabelText('Who can open it')
    await user.selectOptions(screen.getByLabelText('Who can open it'), 'all_users')
    await user.click(continueButton())

    // No date field and no "never" toggle until a choice is made.
    expect(await screen.findByRole('button', { name: /Set an end date/ })).toBeInTheDocument()
    expect(screen.queryByLabelText('Expiry date')).not.toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /never expires/i })).not.toBeInTheDocument()

    await user.click(continueButton())
    expect(blocker()).toHaveTextContent(/no default/i)

    await user.click(screen.getByRole('button', { name: 'Never expires' }))
    // Now the shared ExpiryPicker takes over, with "never" ticked.
    expect(screen.getByRole('checkbox', { name: /never expires/i })).toBeChecked()
  })
})

describe('NewAppWizard — create', () => {
  it('posts the whole draft and hands off to the build screen on 202', async () => {
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())

    await screen.findByLabelText('Who can open it')
    await user.selectOptions(screen.getByLabelText('Who can open it'), 'all_users')
    await user.click(continueButton())

    await user.click(await screen.findByRole('button', { name: 'Never expires' }))
    await user.click(continueButton())

    // The review restates the address and every choice.
    expect(
      await addressLine('https://access-atlas-xxxxxx.tools.stratevi.com'),
    ).toBeInTheDocument()
    expect(screen.getByText('shiny, dplyr')).toBeInTheDocument()
    expect(screen.getByText('Everyone signed in')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Create app/ }))

    await waitFor(() => expect(createApp).toHaveBeenCalledTimes(1))
    expect(createApp.mock.calls[0][0]).toEqual({
      key: 'access-atlas',
      label: 'Access Atlas',
      description: '',
      cpu: 512,
      memory: 2048,
      upload_key: 'uploads/abc.zip',
      access_mode: 'all_users',
      allowed_emails: [],
      idle_minutes: 20,
      max_session_hours: 12,
      expires_at: null,
      packages: ['shiny', 'dplyr'],
    })
    expect(await screen.findByText('Build screen')).toBeInTheDocument()
  })

  it('sends a 409 back to the Details step, where the key is', async () => {
    createApp.mockRejectedValue(new ApiError(409, 'An app already uses that key.'))
    const user = userEvent.setup()
    renderWizard()

    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())
    await screen.findByLabelText('Who can open it')
    await user.selectOptions(screen.getByLabelText('Who can open it'), 'all_users')
    await user.click(continueButton())
    await user.click(await screen.findByRole('button', { name: 'Never expires' }))
    await user.click(continueButton())
    await user.click(await screen.findByRole('button', { name: /Create app/ }))

    expect(await screen.findByText('That key is taken')).toBeInTheDocument()
    expect(screen.getByLabelText('Key')).toBeInTheDocument()
  })
})

describe('NewAppWizard — task size', () => {
  it('states ADR-0001’s rule next to the two sizes', async () => {
    renderWizard()

    expect(screen.getByText(/Size up rather than down/)).toBeInTheDocument()
    expect(screen.getByLabelText(/0\.5 vCPU \/ 2 GB/)).toBeChecked()
    expect(screen.getByLabelText(/4 vCPU \/ 16 GB/)).not.toBeChecked()
  })

  it('suggests the shorter idle timeout for a model rather than rewriting it', async () => {
    const user = userEvent.setup()
    renderWizard()

    await user.click(screen.getByLabelText(/4 vCPU \/ 16 GB/))
    await fillDetails(user)
    await user.click(continueButton())
    await uploadBundle(user)
    expect(await packageBox()).toHaveValue('shiny\ndplyr')
    await user.click(screen.getByRole('checkbox', { name: /right entrypoint/i }))
    await user.click(continueButton())

    const idle = await screen.findByLabelText('Idle timeout')
    expect(idle).toHaveValue(20)
    expect(screen.getByText(/Models usually want 10/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Use 10' }))
    expect(idle).toHaveValue(10)
  })
})
