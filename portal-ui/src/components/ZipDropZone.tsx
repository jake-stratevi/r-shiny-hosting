import { useId, useState, type DragEvent } from 'react'
import { MAX_ZIP_MB } from '../api/types'
import { humanSize } from '../lib/createDraft'
import { ArchiveIcon, CheckIcon } from './icons'

/**
 * Drag-and-drop (or click) for one .zip. The pattern is assembled.work's:
 * the whole zone is a `<label>` wrapping a visually-hidden file input, so
 * clicking, keyboard focus and drop all reach the same control, and the zone
 * changes skin for dragging / chosen / idle.
 *
 * It does no validating of its own — `checkZip` owns that, and the caller
 * owns what happens next — but it will not accept a second file while one is
 * uploading.
 */
export function ZipDropZone({
  file,
  onFile,
  disabled,
  busy,
}: {
  file: File | null
  onFile: (file: File | null) => void
  disabled?: boolean
  /** An upload is in flight: the zone still shows the file, but is inert. */
  busy?: boolean
}) {
  const inputId = useId()
  const [dragging, setDragging] = useState(false)
  const locked = disabled || busy

  function take(next: File | null) {
    if (locked) return
    onFile(next)
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault()
    setDragging(false)
    take(event.dataTransfer?.files?.[0] ?? null)
  }

  const skin = dragging
    ? 'border-azure bg-azure/10'
    : file
      ? 'border-emerald-300 dark:border-emerald-400/30 bg-emerald-50 dark:bg-emerald-400/10'
      : 'border-border hover:border-azure/40 hover:bg-accent/40'

  return (
    <label
      htmlFor={inputId}
      onDragOver={(e) => {
        e.preventDefault()
        if (!locked) setDragging(true)
      }}
      onDragEnter={(e) => {
        e.preventDefault()
        if (!locked) setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors ${skin} ${
        locked ? 'cursor-not-allowed opacity-70' : 'cursor-pointer'
      }`}
    >
      {file && !dragging ? (
        <CheckIcon className="h-8 w-8 text-emerald-600 dark:text-emerald-400" />
      ) : (
        <ArchiveIcon className={`h-8 w-8 ${dragging ? 'text-azure' : 'text-muted-foreground/70'}`} />
      )}

      {dragging ? (
        <span className="text-sm font-medium text-azure">Drop the .zip to upload</span>
      ) : file ? (
        <>
          <span className="max-w-full truncate text-sm font-medium text-foreground">
            {file.name}
          </span>
          <span className="text-xs text-muted-foreground/70">
            {humanSize(file.size)} ·{' '}
            {busy ? 'uploading…' : 'click or drop another .zip to replace it'}
          </span>
        </>
      ) : (
        <>
          <span className="text-sm text-muted-foreground">
            Drag a .zip here, or click to choose one
          </span>
          <span className="text-xs text-muted-foreground/70">
            The app directory zipped, up to {MAX_ZIP_MB} MB. `app.R`, or `ui.R` and
            `server.R`, at the root or in one folder.
          </span>
        </>
      )}

      <input
        id={inputId}
        type="file"
        accept=".zip,application/zip"
        disabled={locked}
        className="sr-only"
        aria-label="App bundle (.zip)"
        onChange={(e) => {
          take(e.target.files?.[0] ?? null)
          // Let the same file be chosen twice in a row (after a rejection).
          e.target.value = ''
        }}
      />
    </label>
  )
}
