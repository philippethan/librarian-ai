import client from './client';

export const listBooks = (params) => client.get('/api/books', { params });
export const getBook = (id) => client.get(`/api/books/${id}`);
export const patchBook = (id, data) => client.patch(`/api/books/${id}`, data);
export const deleteBook = (id, deleteFile = false) =>
  client.delete(`/api/books/${id}`, { params: { delete_file: deleteFile } });
export const reprocessBook = (id) => client.post(`/api/books/${id}/reprocess`);
export const fixBook = (id) => client.post(`/api/books/${id}/fix`);
export const unfixBook = (id) => client.post(`/api/books/${id}/unfix`);
export const enrichBook = (id) => client.post(`/api/books/${id}/enrich`);
export const renameBook = (id, dryRun = false) =>
  client.post(`/api/books/${id}/rename`, null, { params: { dry_run: dryRun } });
export const renameBookAs = (id, filename) =>
  client.post(`/api/books/${id}/rename`, { new_filename: filename });
export const renameSuggest = (id) => client.get(`/api/books/${id}/rename-suggest`);
export const patchReadingStatus = (id, status) =>
  client.patch(`/api/books/${id}/reading-status`, { reading_status: status });
export const getCover = (id) => {
  const key = client.defaults.headers['X-API-Key'] ?? '';
  return `${client.defaults.baseURL}/api/books/${id}/cover?api_key=${encodeURIComponent(key)}`;
};
export const refreshCover = (id) => client.post(`/api/books/${id}/cover/refresh`);
export const openBook = (id) => client.post(`/api/books/${id}/open`);
export const chatBook = (id, message) => client.post(`/api/books/${id}/chat`, { message });
export const debugBook = (id) => client.get(`/api/books/${id}/debug`);
export const scanBooks = (data) => client.post('/api/scan', data);
export const getCategories = () => client.get('/api/categories');
export const addCategory = (data) => client.post('/api/categories', data);
export const deleteCategory = (id) => client.delete(`/api/categories/${id}`);
