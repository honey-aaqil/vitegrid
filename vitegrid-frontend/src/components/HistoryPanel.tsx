import { timelineIndex, timelineLength, type EditorState } from "../state/history";

interface Props {
  state: EditorState;
  onUndo: () => void;
  onRedo: () => void;
}

export function HistoryPanel({ state, onUndo, onRedo }: Props) {
  const total = timelineLength(state);
  const cursor = timelineIndex(state);

  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <button onClick={onUndo} disabled={state.past.length === 0} className="btn px-2 py-1">
        Undo
      </button>
      <button onClick={onRedo} disabled={state.future.length === 0} className="btn px-2 py-1">
        Redo
      </button>
      <span>
        {cursor + 1} / {total}
      </span>
    </div>
  );
}
