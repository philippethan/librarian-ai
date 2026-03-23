import axios from 'axios';

const apiKey = localStorage.getItem('librarian_api_key') ?? import.meta.env.VITE_API_KEY;

const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? 'http://localhost:8000',
  headers: {
    'X-API-Key': apiKey ?? '',
  },
});

export default client;
