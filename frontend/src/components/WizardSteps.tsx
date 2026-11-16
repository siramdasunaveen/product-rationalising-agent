export const WIZARD_STEPS = ["Scope", "Evidence Hub", "Scoring", "Review", "Start"] as const;

/** Step pills of the Create Rationalisation Run wizard. Only steps that are built are clickable. */
export function WizardSteps({
  current,
  available,
  onSelect,
}: {
  current: number; // 1-based
  available: number[];
  onSelect?: (step: number) => void;
}) {
  return (
    <ol className="pra-wizard" aria-label="Create run steps">
      {WIZARD_STEPS.map((label, i) => {
        const step = i + 1;
        const active = step === current;
        const enabled = available.includes(step) && !active && !!onSelect;
        return (
          <li key={label}>
            <button
              type="button"
              className={`pra-wizard__step${active ? " pra-wizard__step--active" : ""}`}
              aria-current={active ? "step" : undefined}
              disabled={!enabled}
              onClick={() => onSelect?.(step)}
              title={available.includes(step) ? undefined : "Available in a later build step"}
            >
              {step} {label}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
