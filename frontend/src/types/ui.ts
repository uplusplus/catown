export type DetailLoadTarget = 'decision' | 'asset' | null;

export type NoticeState = {
  tone: 'success' | 'info' | 'warning';
  message: string;
} | null;

export type DetailStatus = {
  loading: DetailLoadTarget;
  error: string | null;
};
