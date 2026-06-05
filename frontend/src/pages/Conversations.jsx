import { useNavigate } from "react-router-dom";

import { useConversations } from "../hooks/useConversations.js";
import { useCloseConversation } from "../hooks/useCloseConversation.js";

function when(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}

export default function Conversations() {
  const navigate = useNavigate();
  const { data, isLoading } = useConversations();
  const close = useCloseConversation();

  const startNew = () => navigate("/chat/new");

  if (isLoading) {
    return (
      <div className="page">
        <div className="page-header">
          <h1 className="page-title">Conversations</h1>
        </div>
        <div className="conv-list">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton skeleton-card" />
          ))}
        </div>
      </div>
    );
  }

  if (!data?.length) {
    return (
      <div className="page">
        <div className="empty">
          <span className="mark">◎</span>
          <span className="empty-text">No conversations yet</span>
          <button className="btn btn-primary" onClick={startNew}>
            Start a conversation →
          </button>
        </div>
      </div>
    );
  }

  // closed sorted to the bottom
  const convos = [...data].sort((a, b) => Number(a.closed) - Number(b.closed));

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Conversations</h1>
        <button className="btn btn-primary" onClick={startNew}>
          New Conversation →
        </button>
      </div>

      <div className="conv-list">
        {convos.map((c) => (
          <div key={c.id} className={`conv-card ${c.closed ? "closed" : ""}`}>
            <div className="conv-title">{c.title || "Untitled conversation"}</div>
            <div className="conv-foot">
              <span className="conv-time">{when(c.created_at)}</span>
              <span className="badge badge-muted">{c.message_count} msgs</span>
              <span className="spacer" />
              {c.closed ? (
                <span className="badge badge-muted">Closed</span>
              ) : (
                <>
                  <button className="btn" onClick={() => navigate(`/chat/${c.id}`)}>
                    Resume
                  </button>
                  <button
                    className="btn"
                    disabled={close.isPending}
                    onClick={() => close.mutate(c.id)}
                  >
                    Close
                  </button>
                </>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
