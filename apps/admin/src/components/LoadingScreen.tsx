export function LoadingScreen({ label }: { label: string }) {
  return (
    <div className="d-flex align-items-center justify-content-center vh-100">
      <div className="text-center" role="status" aria-live="polite">
        <div className="spinner-border text-primary" aria-hidden="true" />
        <p className="mt-3 mb-0 text-secondary">{label}</p>
      </div>
    </div>
  );
}
