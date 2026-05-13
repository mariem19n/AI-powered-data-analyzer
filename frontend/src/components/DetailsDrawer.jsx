import { Fragment } from "react";

export default function DetailsDrawer({ response }) {
  const intent = response?.intent?.primary ?? response?.intent ?? null;
  const duration = response?.total_duration_s;
  const llmCalls = response?.llm_calls;
  const sources = Array.isArray(response?.data)
    ? response.data.map((d) => d?.source).filter(Boolean)
    : [];

  return (
    <div className="details-drawer">
      <dl>
        {intent && (
          <>
            <dt>Intent</dt>
            <dd>{String(intent)}</dd>
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
        {sources.map((src, i) => (
          <Fragment key={i}>
            <dt>Data source</dt>
            <dd>{src}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}
