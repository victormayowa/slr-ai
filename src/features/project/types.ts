export type ProtocolItem = {
  id: string;
  text: string;
  status: 'pending' | 'accepted' | 'rejected';
  element: string | null;
  source: 'ai' | 'reviewer';
};

export type SearchItem = { id: string; database: string; string: string; status: 'pending' | 'accepted' | 'rejected' };
