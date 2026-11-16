/** "Next action" (user) and "Agent outcome" (system) cards shown at the bottom of every screen. */
export function GuidanceCards({ next, outcome }: { next: string[]; outcome: string[] }) {
  return (
    <section className="pra-guidance">
      <div>
        <h2 className="pra-guidance__title">Next action</h2>
        <div className="pra-guidance__card">
          <ol className="pra-steps">
            {next.map((t, i) => (
              <li key={t}><span className="pra-steps__num">{i + 1}</span>{t}</li>
            ))}
          </ol>
        </div>
      </div>
      <div>
        <h2 className="pra-guidance__title">Agent outcome</h2>
        <div className="pra-guidance__card">
          <ul className="pra-steps pra-steps--outcome">
            {outcome.map((t) => (
              <li key={t}><span className="pra-steps__num">✓</span>{t}</li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
