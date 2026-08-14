import { useQuery } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { api } from "./api/client";
import QueryError from "./components/QueryError";
import { shouldRedirectToMigration } from "./lib/schemaGate";

/**
 * 表に差分があるときは専用ページへ送る。古い形のまま画面を出すと、
 * 一部だけ壊れて原因が分からない。
 */
export default function SchemaGate() {
  const location = useLocation();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["schema"],
    queryFn: api.schemaStatus,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="migration-page">
        <p>起動しています…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="migration-page">
        <h1>起動できませんでした</h1>
        <QueryError error={error} onRetry={() => refetch()} />
      </div>
    );
  }

  if (shouldRedirectToMigration(data?.phase, location.pathname)) {
    return <Navigate to="/migration" replace />;
  }

  return <Outlet />;
}
