import type { ProductFamily } from "../api/metadata";

/** Tree multi-select for product families. Checking a family also checks its descendants. */
export function FamilyTree({
  families,
  value,
  onChange,
  disabled,
  invalid,
}: {
  families: ProductFamily[];
  value: string[];
  onChange: (ids: string[]) => void;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const children = new Map<string | null, ProductFamily[]>();
  for (const f of families) {
    const key = families.some((p) => p.id === f.parent_id) ? f.parent_id : null;
    children.set(key, [...(children.get(key) ?? []), f]);
  }
  const descendants = (id: string): string[] =>
    (children.get(id) ?? []).flatMap((c) => [c.id, ...descendants(c.id)]);

  const toggle = (f: ProductFamily) => {
    const ids = [f.id, ...descendants(f.id)];
    onChange(value.includes(f.id) ? value.filter((v) => !ids.includes(v)) : [...new Set([...value, ...ids])]);
  };

  const render = (parent: string | null, depth: number) =>
    (children.get(parent) ?? []).map((f) => (
      <div key={f.id}>
        <label className="pra-tree__item" style={{ paddingLeft: depth * 22 }}>
          <input type="checkbox" checked={value.includes(f.id)} disabled={disabled} onChange={() => toggle(f)} />
          {f.name}
        </label>
        {render(f.id, depth + 1)}
      </div>
    ));

  return (
    <div className={`pra-tree${invalid ? " pra-tree--invalid" : ""}`} role="group">
      {families.length === 0 ? <span className="pra-muted">Choose a portfolio first.</span> : render(null, 0)}
    </div>
  );
}
