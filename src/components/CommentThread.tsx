import { useCallback, useEffect, useState } from 'react';
import type { CommentInfo } from '../api/collaboration';
import { errorMessage } from '../api/client';
import { useAuth } from '../auth/authContext';
import { AMBER, GREEN, GREY, chip, muted, panel, row, smallButton } from './ui';

type CommentThreadProps = {
  projectId: number;
  // Identifies what is being discussed, for example "record:12", "span:40", "cell:3:7:2", or "sentence:methods:<hash>".
  anchorKey: string;
  // Shown in the team screen so a thread can be recognized away from the screen it was written on.
  anchorLabel: string;
  // How many comments this anchor already has, so the trigger can show a count without loading the thread.
  count?: number;
};

// A discussion attached to one thing in the review. Collapsed until opened, so it can sit beside a record, a passage,
// an extraction cell, or a manuscript sentence without getting in the way.
export function CommentThread({ projectId, anchorKey, anchorLabel, count = 0 }: CommentThreadProps) {
  const { apiRequest } = useAuth();
  const [open, setOpen] = useState(false);
  const [comments, setComments] = useState<CommentInfo[] | null>(null);
  const [draft, setDraft] = useState('');
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const fetchComments = useCallback(
    (): Promise<CommentInfo[]> =>
      apiRequest('GET', `/api/projects/${projectId}/comments?anchor_key=${encodeURIComponent(anchorKey)}`),
    [apiRequest, projectId, anchorKey],
  );

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetchComments()
      .then(result => {
        if (!cancelled) setComments(result);
      })
      .catch(err => {
        if (!cancelled) setProblem(errorMessage(err, 'Could not load the comments.'));
      });
    return () => {
      cancelled = true;
    };
  }, [open, fetchComments]);

  const act = async (action: () => Promise<void>, failure: string) => {
    setBusy(true);
    setProblem(null);
    try {
      await action();
      setComments(await fetchComments());
    } catch (err) {
      setProblem(errorMessage(err, failure));
    }
    setBusy(false);
  };

  const send = () =>
    act(async () => {
      await apiRequest('POST', `/api/projects/${projectId}/comments`, {
        anchor_key: anchorKey,
        anchor_label: anchorLabel,
        body: draft.trim(),
        parent_id: replyTo,
      });
      setDraft('');
      setReplyTo(null);
    }, 'Could not add the comment.');

  const shown = comments ?? [];
  const unresolved = shown.filter(comment => !comment.resolved && comment.parent_id === null).length;
  const label = count > 0 || shown.length > 0 ? `💬 ${shown.length || count}` : '💬 Comment';

  return (
    <div style={{ marginTop: '6px' }}>
      <button
        className="btn-glass"
        style={{ ...smallButton, borderColor: unresolved > 0 ? AMBER : undefined }}
        aria-label={open ? `Hide comments on ${anchorLabel}` : `Comments on ${anchorLabel}`}
        aria-expanded={open}
        onClick={() => setOpen(value => !value)}
      >
        {label}
      </button>
      {open && (
        <div style={{ ...panel, marginTop: '6px', fontSize: '0.82rem' }}>
          {problem && <p role="alert" style={muted}>{problem}</p>}
          {comments === null && <p style={muted}>Loading…</p>}
          {comments !== null && shown.length === 0 && <p style={muted}>No comments yet.</p>}
          {shown
            .filter(comment => comment.parent_id === null)
            .map(comment => (
              <div key={comment.id} style={{ marginBottom: '8px' }}>
                <div style={{ ...row, justifyContent: 'space-between' }}>
                  <strong>{comment.author ?? 'Former member'}</strong>
                  {comment.resolved ? (
                    <span style={chip(GREEN)}>resolved</span>
                  ) : (
                    <button className="btn-glass" style={smallButton} disabled={busy} onClick={() => act(async () => {
                      await apiRequest('POST', `/api/projects/${projectId}/comments/${comment.id}/resolve`);
                    }, 'Could not resolve the thread.')}>
                      Resolve
                    </button>
                  )}
                </div>
                <p style={{ margin: '4px 0' }}>{comment.deleted ? <em style={muted}>deleted</em> : comment.body}</p>
                {shown
                  .filter(reply => reply.parent_id === comment.id)
                  .map(reply => (
                    <p key={reply.id} style={{ margin: '4px 0 4px 14px', borderLeft: `2px solid ${GREY}`, paddingLeft: '8px' }}>
                      <strong>{reply.author ?? 'Former member'}:</strong>{' '}
                      {reply.deleted ? <em style={muted}>deleted</em> : reply.body}
                    </p>
                  ))}
                {!comment.resolved && (
                  <button className="btn-glass" style={smallButton} onClick={() => setReplyTo(comment.id)}>
                    Reply
                  </button>
                )}
              </div>
            ))}
          <div style={{ ...row, marginTop: '8px' }}>
            <input
              aria-label={replyTo ? 'Your reply' : `Comment on ${anchorLabel}`}
              className="search-input"
              style={{ flex: '1 1 180px' }}
              placeholder={replyTo ? 'Reply…' : 'Mention someone with @their email'}
              value={draft}
              onChange={e => setDraft(e.target.value)}
            />
            <button className="btn-primary" style={smallButton} disabled={busy || !draft.trim()} onClick={send}>
              {replyTo ? 'Reply' : 'Comment'}
            </button>
            {replyTo && (
              <button className="btn-glass" style={smallButton} onClick={() => setReplyTo(null)}>
                Cancel
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
