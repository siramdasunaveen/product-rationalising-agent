import { useEffect, useRef, useState } from "react";

type Option = { id: string; label: string };

/** Dropdown with checkboxes. Shows the selected labels in the closed state. */
export function MultiSelect({
  id,
  options,
  value,
  onChange,
  placeholder = "Select…",
  disabled,
  max,
  invalid,
}: {
  id: string;
  options: Option[];
  value: string[];
  onChange: (ids: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  max?: number;
  invalid?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const selected = options.filter((o) => value.includes(o.id)).map((o) => o.label);
  const toggle = (oid: string) =>
    onChange(value.includes(oid) ? value.filter((v) => v !== oid) : [...value, oid]);

  return (
    <div className="pra-ms" ref={ref}>
      <button
        id={id}
        type="button"
        className={`pra-input pra-ms__button${invalid ? " pra-input--invalid" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={selected.length ? "" : "pra-muted"}>{selected.length ? selected.join(", ") : placeholder}</span>
        <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="pra-ms__panel" role="listbox" aria-multiselectable="true">
          {options.map((o) => {
            const checked = value.includes(o.id);
            const full = !!max && !checked && value.length >= max;
            return (
              <label key={o.id} className={`pra-ms__option${full ? " pra-ms__option--disabled" : ""}`}>
                <input type="checkbox" checked={checked} disabled={full} onChange={() => toggle(o.id)} />
                {o.label}
              </label>
            );
          })}
          {options.length === 0 && <div className="pra-muted pra-ms__empty">No options</div>}
        </div>
      )}
    </div>
  );
}
