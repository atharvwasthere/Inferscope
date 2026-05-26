import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";

import MessageBubble from "../components/MessageBubble.jsx";
import ProviderSelector from "../components/ProviderSelector.jsx";
import { useMessages } from "../hooks/useMessages.js";
import { useModels } from "../hooks/useModels.js";

const CHATBOT_BASE = "/api/chatbot";

export default function Chat() {
  const { id } = useParams();
  const [conversationId, setConversationId] = useState(id);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");

  // Provider/model catalog comes from the backend (single source of truth).
  const { data: models } = useModels();
  useEffect(() => {
    if (models && !provider) {
      const first = Object.keys(models)[0];
      if (first) {
        setProvider(first);
        setModel(models[first][0]);
      }
    }
  }, [models, provider]);

  const abortRef = useRef(null);
  const scrollRef = useRef(null);

  // Seed from history when resuming an existing conversation.
  const { data: history } = useMessages(id);
  useEffect(() => {
    if (history && id !== "new") {
      setMessages(history.map((m) => ({ role: m.role, content: m.content, metadata: null })));
    }
  }, [history, id]);

  // Keep conversationId in sync if the route param changes.
  useEffect(() => setConversationId(id), [id]);

  // Autoscroll to newest token.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  const onProviderChange = (p, m) => {
    setProvider(p);
    setModel(m);
  };

  const cancel = () => {
    abortRef.current?.abort();
    setStreaming(false);
  };

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || streaming || !provider || !model) return;

    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setInput("");
    setStreaming(true);
    setMessages((prev) => [...prev, { role: "assistant", content: "", metadata: null }]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await fetch(`${CHATBOT_BASE}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId === "new" ? null : conversationId,
          provider,
          model,
        }),
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const blocks = buffer.split("\n\n");
        buffer = blocks.pop();

        for (const block of blocks) {
          let event = "message";
          let dataStr = null;
          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataStr = line.slice(6);
          }
          if (dataStr == null) continue;
          const data = JSON.parse(dataStr);

          if (event === "meta" && data.conversation_id) {
            if (conversationId === "new") {
              setConversationId(data.conversation_id);
              window.history.replaceState(null, "", `/chat/${data.conversation_id}`);
            }
          } else if (event === "message" && data.text) {
            setMessages((prev) => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = {
                ...msgs[msgs.length - 1],
                content: msgs[msgs.length - 1].content + data.text,
              };
              return msgs;
            });
          } else if (event === "done") {
            setStreaming(false);
          } else if (event === "error" || data.error) {
            setMessages((prev) => {
              const msgs = [...prev];
              msgs[msgs.length - 1] = {
                ...msgs[msgs.length - 1],
                content: msgs[msgs.length - 1].content || `⚠ ${data.error || "stream error"}`,
              };
              return msgs;
            });
            setStreaming(false);
          }
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        setMessages((prev) => {
          const msgs = [...prev];
          const last = msgs[msgs.length - 1];
          if (last && last.role === "assistant" && !last.content) {
            last.content = `⚠ ${e.message}`;
          }
          return msgs;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
    }
  };

  const onKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="chat-shell">
      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-inner">
          {messages.length === 0 && (
            <div className="empty">
              <span className="mark">◎</span>
              <span className="empty-text">Send a message to start</span>
            </div>
          )}
          {messages.map((m, i) => (
            <MessageBubble
              key={i}
              role={m.role}
              content={m.content}
              metadata={m.metadata}
              streaming={streaming && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
        </div>
      </div>

      <div className="chat-bar">
        <div className="chat-bar-inner">
          <ProviderSelector models={models} provider={provider} model={model} onChange={onProviderChange} />
          <input
            className="chat-input"
            placeholder="Type a message…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            disabled={streaming}
          />
          {streaming ? (
            <button className="btn" onClick={cancel}>
              × Cancel
            </button>
          ) : (
            <button className="btn btn-primary" onClick={sendMessage} disabled={!input.trim()}>
              Send
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
