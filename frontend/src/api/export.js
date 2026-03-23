import client from './client';

export const exportCsv = () =>
  client.get('/api/export/csv', { responseType: 'blob' });

export const exportJson = () =>
  client.get('/api/export/json', { responseType: 'blob' });
