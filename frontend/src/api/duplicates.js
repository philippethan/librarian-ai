import client from './client';

export const listDuplicates = () => client.get('/api/duplicates');
export const dismissGroup = (hash) => client.post('/api/duplicates/dismiss', { hash });
