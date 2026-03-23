import client from './client';

export const listShelves = () => client.get('/api/shelves');
export const createShelf = (data) => client.post('/api/shelves', data);
export const deleteShelf = (id) => client.delete(`/api/shelves/${id}`);
export const getShelfBooks = (id) => client.get(`/api/shelves/${id}/books`);
export const addBookToShelf = (shelfId, bookId) =>
  client.post(`/api/shelves/${shelfId}/books`, { book_id: bookId });
export const removeBookFromShelf = (shelfId, bookId) =>
  client.delete(`/api/shelves/${shelfId}/books/${bookId}`);
