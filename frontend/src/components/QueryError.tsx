/**
 * 読み込み失敗。止まったままにせず、もう一度試せるようにする。
 */
export default function QueryError({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const message = error instanceof Error ? error.message : "読み込めませんでした";

  return (
    <div className="error" role="alert">
      <div>{message}</div>
      {onRetry && (
        <button type="button" className="error-retry" onClick={onRetry}>
          もう一度読み込む
        </button>
      )}
    </div>
  );
}
