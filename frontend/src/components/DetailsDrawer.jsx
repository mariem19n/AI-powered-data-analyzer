import { Fragment } from "react";
import { getPlanTasks } from "../utils/externalSources.js";

function joinValues(values) {
  return values.filter(Boolean).join(", ");
}

export default function DetailsDrawer({ response }) {
  const intent = response?.intent?.primary ?? response?.intent ?? null;
  const duration = response?.total_duration_s;
  const llmCalls = response?.llm_calls;
  const planTasks = getPlanTasks(response);
  const dataItems = Array.isArray(response?.data) ? response.data : [];
  const warnings = Array.isArray(response?.warnings) ? response.warnings.filter(Boolean) : [];
  const statsKeys = Object.keys(response?.analysis_stats || {});

  return (
    <div className="details-drawer">
      <dl>
        {intent && (
          <>
            <dt>Intent</dt>
            <dd>{String(intent)}</dd>
          </>
        )}
        {planTasks.length > 0 && (
          <>
            <dt>Plan tasks</dt>
            <dd>{planTasks.join(", ")}</dd>
          </>
        )}
        {duration != null && (
          <>
            <dt>Duration</dt>
            <dd>{Number(duration).toFixed(2)} s</dd>
          </>
        )}
        {llmCalls != null && (
          <>
            <dt>LLM calls</dt>
            <dd>{llmCalls}</dd>
          </>
        )}
        {dataItems.map((item, index) => (
          <Fragment key={`data-${index}`}>
            <dt>SQL data</dt>
            <dd>
              {joinValues([
                item?.step_id,
                item?.row_count != null ? `${item.row_count} rows` : "",
                Array.isArray(item?.columns) && item.columns.length > 0
                  ? `columns: ${item.columns.join(", ")}`
                  : "",
              ])}
            </dd>
            {item?.sql && (
              <>
                <dt>Generated SQL</dt>
                <dd>{item.sql}</dd>
              </>
            )}
          </Fragment>
        ))}
        {statsKeys.length > 0 && (
          <>
            <dt>Analysis stats</dt>
            <dd>{statsKeys.join(", ")}</dd>
          </>
        )}
        {warnings.map((warning, index) => (
          <Fragment key={`warning-${index}`}>
            <dt>Warning</dt>
            <dd>{warning}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}
