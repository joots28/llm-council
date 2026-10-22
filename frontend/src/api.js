/**
 * API client for the LLM Council.
 *
 * The API key is a client credential supplied by an authorized user and held
 * only in sessionStorage. The OpenRouter master key is never sent to the browser.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export const api = {
  setApiKey(key) {
    sessionStorage.setItem('nexus_api_key', key);
  },

  clearApiKey() {
    sessionStorage.removeItem('nexus_api_key');
  },

  getApiKey() {
    return sessionStorage.getItem('nexus_api_key') || '';
  },

  async request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const key = this.getApiKey();
    if (key) headers.set('Authorization', `Bearer ${key}`);
    if (options.body && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
    if (!response.ok) {
      let message = `API request failed (${response.status})`;
      try {
        const body = await response.json();
        if (typeof body.detail === 'string') message = body.detail;
      } catch {
        // Keep the safe generic message if the server did not return JSON.
      }
      throw new Error(message);
    }
    return response;
  },

  async listConversations() {
    const response = await this.request('/api/conversations');
    return response.json();
  },

  async createConversation() {
    const response = await this.request('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    return response.json();
  },

  async getConversation(conversationId) {
    const response = await this.request(
      `/api/conversations/${encodeURIComponent(conversationId)}`
    );
    return response.json();
  },

  async sendMessage(conversationId, content) {
    const response = await this.request(
      `/api/conversations/${encodeURIComponent(conversationId)}/message`,
      { method: 'POST', body: JSON.stringify({ content }) }
    );
    return response.json();
  },

  async sendMessageStream(conversationId, content, onEvent) {
    const response = await this.request(
      `/api/conversations/${encodeURIComponent(conversationId)}/message/stream`,
      { method: 'POST', body: JSON.stringify({ content }) }
    );
    if (!response.body) throw new Error('Streaming is not supported by this connection');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const dispatchFrame = (frame) => {
      const dataLines = frame
        .split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).replace(/^ /, ''));
      if (!dataLines.length) return;
      try {
        const event = JSON.parse(dataLines.join('\n'));
        onEvent(event.type, event);
      } catch {
        // Ignore malformed frames without exposing message contents in logs.
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        let boundary;
        while ((boundary = buffer.search(/\r?\n\r?\n/)) !== -1) {
          const frame = buffer.slice(0, boundary);
          const separator = buffer.slice(boundary).match(/^\r?\n\r?\n/)[0];
          buffer = buffer.slice(boundary + separator.length);
          dispatchFrame(frame);
        }
        if (done) break;
      }
      if (buffer.trim()) dispatchFrame(buffer);
    } finally {
      reader.releaseLock();
    }
  },
};
