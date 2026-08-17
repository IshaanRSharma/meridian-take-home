/** Documents attached to the board for the reviewer to read.
 *
 * Sits above the questions because it is what changes them: with a procedure
 * attached the reviewer can ask where the drawing and the document disagree,
 * rather than only where the drawing is silent.
 *
 * Shows what was read, not just the filename — a scan that transcribed badly
 * is otherwise invisible and looks like the reviewer ignoring it.
 */
import { useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, Plus, X } from 'lucide-react';
import { api, type ReferenceDoc } from '@/lib/api';
import { Problem, cx } from '@/components/ui';

export default function Documents({ boardId }: { boardId: string }) {
  const queryClient = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);

  const docs = useQuery({
    queryKey: ['documents', boardId],
    queryFn: () => api.documents.list(boardId),
  });

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['documents', boardId] });

  const upload = useMutation({
    mutationFn: (file: File) => api.documents.upload(boardId, file),
    onSuccess: refresh,
  });

  const remove = useMutation({
    mutationFn: (documentId: string) => api.documents.remove(boardId, documentId),
    onSuccess: refresh,
  });

  const attached = docs.data ?? [];

  return (
    <div className="border-b border-(--color-line) px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-[11.5px] font-medium text-(--color-ink-dim)">
          Attach relevant documents
        </span>
        {attached.length > 0 && (
          <span className="font-mono text-[10.5px] text-(--color-ink-faint)">
            {attached.length}
          </span>
        )}
        <button
          onClick={() => picker.current?.click()}
          disabled={upload.isPending}
          title="PDF, markdown, text or a photo of a printed page"
          className={cx(
            'ml-auto grid size-6 place-items-center rounded-md border border-(--color-line-soft)',
            'text-(--color-ink-faint) transition-colors',
            'hover:border-(--color-ink-faint) hover:text-(--color-ink)',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          <Plus size={13} />
        </button>
        <input
          ref={picker}
          type="file"
          hidden
          accept=".pdf,.md,.txt,.png,.jpg,.jpeg"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
            event.target.value = '';
          }}
        />
      </div>

      {upload.isPending && (
        <p className="mt-1.5 text-[11px] text-(--color-ink-faint)">Reading it…</p>
      )}

      {upload.error && (
        <div className="mt-2">
          <Problem
            title="Could not read that file"
            body={upload.error instanceof Error ? upload.error.message : String(upload.error)}
          />
        </div>
      )}

      {attached.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {attached.map((doc) => (
            <Row
              key={doc.id ?? doc.filename}
              doc={doc}
              onRemove={() => doc.id && remove.mutate(doc.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Row({ doc, onRemove }: { doc: ReferenceDoc; onRemove: () => void }) {
  const read = (doc.text ?? '').trim();
  return (
    <li className="group flex items-start gap-2 rounded-md border border-(--color-line-soft) px-2 py-1.5">
      <FileText size={12} className="mt-0.5 shrink-0 text-(--color-ink-faint)" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-[11.5px] text-(--color-ink)">{doc.filename}</p>
        <p
          className={cx(
            'text-[10.5px]',
            read ? 'text-(--color-ink-faint)' : 'font-medium text-(--color-ink)',
          )}
        >
          {/* Nothing read is the failure worth naming. The file uploaded fine
              and the reviewer will get nothing from it. */}
          {read
            ? `${doc.kind} · ${read.split(/\s+/).length} words read`
            : `${doc.kind} · nothing could be read from this`}
        </p>
      </div>
      <button
        onClick={onRemove}
        title="Remove"
        className="shrink-0 text-transparent transition-colors group-hover:text-(--color-ink-faint) hover:!text-(--color-ink)"
      >
        <X size={12} />
      </button>
    </li>
  );
}
