import { useState, useEffect } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function ApiKeyGate({ onSubmit }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  return (
    <main style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!key.trim()) return;
          setError('');
          setIsSubmitting(true);
          Promise.resolve(onSubmit(key.trim()))
            .catch((submitError) => setError(submitError.message || 'Não foi possível autenticar a chave.'))
            .finally(() => setIsSubmitting(false));
        }}
        style={{
          width: 'min(100%, 460px)',
          display: 'grid',
          gap: 16,
          padding: 28,
          border: '1px solid var(--border-color, #343b49)',
          borderRadius: 16,
          background: 'var(--panel-bg, #171b24)',
          color: 'var(--text-color, #f5f7fb)',
        }}
      >
        <div>
          <h1 style={{ margin: '0 0 8px' }}>Nexus AI API</h1>
          <p style={{ margin: 0, opacity: 0.78 }}>
            Insira a chave de um cliente autorizado para acessar suas conversas.
          </p>
        </div>
        <label htmlFor="nexus-api-key">Chave de API</label>
        <input
          id="nexus-api-key"
          type="password"
          autoComplete="current-password"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder="Cole sua chave de cliente"
          required
          style={{
            width: '100%',
            boxSizing: 'border-box',
            padding: '12px 14px',
            borderRadius: 8,
            border: '1px solid var(--border-color, #343b49)',
            background: 'var(--input-bg, #10141b)',
            color: 'inherit',
          }}
        />
        {error && <p role="alert" style={{ margin: 0, color: '#f87171' }}>{error}</p>}
        <button type="submit" disabled={isSubmitting} style={{ padding: '12px 16px', borderRadius: 8, cursor: 'pointer' }}>
          {isSubmitting ? 'Validando…' : 'Conectar'}
        </button>
        <small style={{ opacity: 0.68 }}>
          A chave fica apenas nesta sessão do navegador. Nunca use aqui a chave mestre do OpenRouter.
        </small>
      </form>
    </main>
  );
}

function App() {
  const [apiKey, setApiKey] = useState(() => api.getApiKey());
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    if (apiKey) loadConversations();
  }, [apiKey]);

  useEffect(() => {
    if (currentConversationId && apiKey) loadConversation(currentConversationId);
  }, [currentConversationId, apiKey]);

  const handleApiKeySubmit = async (key) => {
    api.setApiKey(key);
    try {
      await api.request('/v1/models');
      setApiKey(key);
    } catch (error) {
      api.clearApiKey();
      throw error;
    }
  };

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error.message);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error.message);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations((previous) => [
        { id: newConv.id, created_at: newConv.created_at, title: newConv.title, message_count: 0 },
        ...previous,
      ]);
      setCurrentConversationId(newConv.id);
      setCurrentConversation(newConv);
    } catch (error) {
      console.error('Failed to create conversation:', error.message);
    }
  };

  const handleSelectConversation = (id) => setCurrentConversationId(id);

  const handleSendMessage = async (content) => {
    if (!currentConversationId || !currentConversation) return;
    setIsLoading(true);
    try {
      const userMessage = { role: 'user', content };
      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        loading: { stage1: false, stage2: false, stage3: false },
      };
      setCurrentConversation((previous) => ({
        ...previous,
        messages: [...previous.messages, userMessage, assistantMessage],
      }));

      await api.sendMessageStream(currentConversationId, content, (eventType, event) => {
        setCurrentConversation((previous) => {
          if (!previous || !previous.messages.length) return previous;
          const messages = [...previous.messages];
          const lastIndex = messages.length - 1;
          const lastMessage = { ...messages[lastIndex] };
          lastMessage.loading = { ...(lastMessage.loading || {}) };
          switch (eventType) {
            case 'stage1_start':
              lastMessage.loading.stage1 = true;
              break;
            case 'stage1_complete':
              lastMessage.stage1 = event.data;
              lastMessage.loading.stage1 = false;
              break;
            case 'stage2_start':
              lastMessage.loading.stage2 = true;
              break;
            case 'stage2_complete':
              lastMessage.stage2 = event.data;
              lastMessage.metadata = event.metadata;
              lastMessage.loading.stage2 = false;
              break;
            case 'stage3_start':
              lastMessage.loading.stage3 = true;
              break;
            case 'stage3_complete':
              lastMessage.stage3 = event.data;
              lastMessage.loading.stage3 = false;
              break;
            default:
              break;
          }
          messages[lastIndex] = lastMessage;
          return { ...previous, messages };
        });

        if (eventType === 'title_complete' || eventType === 'complete') loadConversations();
        if (eventType === 'complete' || eventType === 'error') setIsLoading(false);
        if (eventType === 'error') console.error('Council request failed:', event.message);
      });
    } catch (error) {
      console.error('Failed to send message:', error.message);
      setCurrentConversation((previous) => previous
        ? { ...previous, messages: previous.messages.slice(0, -2) }
        : previous);
      setIsLoading(false);
    }
  };

  if (!apiKey) return <ApiKeyGate onSubmit={handleApiKeySubmit} />;

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
