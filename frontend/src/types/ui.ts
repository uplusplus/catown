export type DetailLoadTarget = 'decision' | 'asset' | null;

export type NoticeState = {
  tone: 'success' | 'info';
  message: string;
} | null;

export type DetailStatus = {
  loading: DetailLoadTarget;
  error: string | null;
};
