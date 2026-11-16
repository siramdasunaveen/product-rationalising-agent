export function PlaceholderScreen({ title, note }: { title: string; note?: string }) {
  return (
    <div className="pra-page">
      <h1 className="pra-page__title">{title}</h1>
      <div className="pra-progress" aria-hidden="true" />
      <div className="pra-card pra-empty">
        <strong>Not built yet</strong>
        {note ?? "This screen arrives in a later step of the build plan."}
      </div>
    </div>
  );
}
