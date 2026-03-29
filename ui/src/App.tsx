import { useMemo, useState } from 'react'

type CliResult = {
  exit_code: number
  stdout: string
  stderr: string
}

async function callCli(argv: string[]): Promise<CliResult> {
  const res = await fetch('/api/cli', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ argv }),
  })
  if (!res.ok) throw new Error(await res.text())
  return (await res.json()) as CliResult
}

function App() {
  const [dataDir, setDataDir] = useState<string>(`${window.location.hostname ? '' : ''}`) // keep simple
  const [output, setOutput] = useState<string>('')
  const [busy, setBusy] = useState<boolean>(false)

  const dd = useMemo(() => (dataDir || '').trim(), [dataDir])

  async function run(argv: string[]) {
    setBusy(true)
    try {
      const r = await callCli(argv)
      const text = [
        `> ${argv.join(' ')}`,
        r.stdout?.trim(),
        r.stderr?.trim(),
        `[exit ${r.exit_code}]`,
      ]
        .filter(Boolean)
        .join('\n')
      setOutput((prev) => (prev ? prev + '\n\n' + text : text))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-full">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-3xl font-semibold tracking-tight">Vertex</div>
            <div className="text-sm text-white/60">
              Glassy web UI wrapper for <code className="text-white/80">vertex-cli</code>
            </div>
          </div>
          <div className="text-xs text-white/50">Backend: <code>/api</code></div>
        </div>

        <div className="mt-8 grid grid-cols-1 gap-6 md:grid-cols-2">
          <div className="glass rounded-2xl p-5">
            <div className="text-sm font-medium text-white/80">Node data directory</div>
            <input
              className="mt-2 w-full rounded-xl border border-white/10 bg-black/20 px-3 py-2 text-sm text-white outline-none focus:border-white/25"
              placeholder="/home/you/vertex-node"
              value={dataDir}
              onChange={(e) => setDataDir(e.target.value)}
            />

            <div className="mt-6 grid grid-cols-2 gap-3">
              <button
                disabled={busy || !dd}
                className="rounded-xl bg-white/10 px-3 py-2 text-sm hover:bg-white/15 disabled:opacity-50"
                onClick={() => run(['config', '-d', dd, 'show'])}
              >
                Show config
              </button>
              <button
                disabled={busy || !dd}
                className="rounded-xl bg-white/10 px-3 py-2 text-sm hover:bg-white/15 disabled:opacity-50"
                onClick={() => run(['search', '-d', dd, 'test'])}
              >
                Search (stub)
              </button>
            </div>

            <div className="mt-4 text-xs text-white/50">
              Next: buttons for <code>download</code>, <code>config get/set</code>, daemon start/stop.
            </div>
          </div>

          <div className="glass rounded-2xl p-5">
            <div className="text-sm font-medium text-white/80">Output</div>
            <pre className="mt-2 max-h-[420px] overflow-auto rounded-xl border border-white/10 bg-black/30 p-3 text-xs text-white/80">
              {output || 'Run an action to see output here.'}
            </pre>
          </div>
        </div>
      </div>
    </div>
  )
}

export default App
