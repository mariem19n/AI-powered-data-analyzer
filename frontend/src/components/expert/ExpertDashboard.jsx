import FeedbackReviewQueue from "./FeedbackReviewQueue.jsx";
import ResponseQualityMonitor from "./ResponseQualityMonitor.jsx";
import KnowledgeGraphExplorer from "./KnowledgeGraphExplorer.jsx";
import PromptPerformance from "./PromptPerformance.jsx";
import DataFreshnessMonitor from "./DataFreshnessMonitor.jsx";
import SemanticLayerHealth from "./SemanticLayerHealth.jsx";

export default function ExpertDashboard() {
  return (
    <main className="expert-dashboard-scroll">
      <div id="expert-overview" className="expert-anchor">
        <section className="expert-panel expert-overview-panel">
          <div className="expert-section-header">
            <div>
              <span>Overview</span>
              <h2>Supervision qualité</h2>
            </div>
          </div>
          <p className="expert-empty-inline">
            Console expert pour suivre les feedbacks, la qualité des réponses, le Knowledge Graph,
            les prompts, la fraîcheur des données et la santé du Semantic Layer.
          </p>
        </section>
      </div>
      <div id="expert-feedback-queue" className="expert-anchor">
        <FeedbackReviewQueue />
      </div>
      <div className="expert-grid two">
        <div id="expert-response-quality" className="expert-anchor">
          <ResponseQualityMonitor />
        </div>
        <div id="expert-prompt-performance" className="expert-anchor">
          <PromptPerformance />
        </div>
      </div>
      <div className="expert-grid two">
        <div id="expert-knowledge-graph" className="expert-anchor">
          <KnowledgeGraphExplorer />
        </div>
        <div id="expert-semantic-health" className="expert-anchor">
          <SemanticLayerHealth />
        </div>
      </div>
      <div id="expert-data-freshness" className="expert-anchor">
        <DataFreshnessMonitor />
      </div>
    </main>
  );
}
