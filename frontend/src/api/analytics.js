import client from './client';

export const getAnalytics = () => client.get('/api/analytics');
