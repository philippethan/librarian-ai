import client from './client';

export const getStats = () => client.get('/api/stats');
