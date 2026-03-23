import { useParams } from 'react-router-dom';

export default function ShelfDetail() {
  const { id } = useParams();
  return <div style={{ padding: '1rem', color: 'var(--text-primary)' }}>ShelfDetail #{id} — coming soon</div>;
}
