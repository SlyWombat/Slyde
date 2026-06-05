/** Placeholder for nav destinations not yet built (#32 shell). Each links to its tracking issue. */
export function ComingSoon({ title, issue, note }: { title: string; issue: number; note: string }) {
  return (
    <div className="mx-auto max-w-3xl px-4 py-16 text-center sm:px-6">
      <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
      <p className="mt-2 text-slate-400">{note}</p>
      <p className="mt-6 text-sm text-slate-500">
        Coming soon — tracked in{" "}
        <a
          className="text-accent hover:underline"
          href={`https://github.com/SlyWombat/slyde/issues/${issue}`}
          target="_blank"
          rel="noreferrer"
        >
          #{issue}
        </a>
        .
      </p>
    </div>
  );
}
