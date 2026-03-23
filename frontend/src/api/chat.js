import client from './client';

export const sendChatMessage = (bookId, message) =>
  client.post(`/api/books/${bookId}/chat`, { message });
