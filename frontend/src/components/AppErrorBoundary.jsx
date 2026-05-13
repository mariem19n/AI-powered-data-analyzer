import { Component } from "react";

export default class AppErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
    this.handleReset = this.handleReset.bind(this);
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    console.error("Frontend render error", error, info);
  }

  handleReset() {
    this.setState({ hasError: false });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-shell">
          <div className="error-boundary-panel">
            <div className="error-boundary-icon">⚡</div>
            <h1>Something went wrong</h1>
            <p>
              Something went wrong rendering this answer. Try asking again or
              refresh the page.
            </p>
            <div className="error-boundary-actions">
              <button
                type="button"
                className="error-retry-btn"
                onClick={this.handleReset}
              >
                Try again
              </button>
              <button
                type="button"
                className="error-secondary-btn"
                onClick={() => window.location.reload()}
              >
                Refresh page
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
