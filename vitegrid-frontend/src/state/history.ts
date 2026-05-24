import type { DocumentBlock, DocumentLayout } from "../types";

export const HISTORY_LIMIT = 30;

export interface EditorState {
  layout: DocumentLayout;
  past: DocumentBlock[][];
  present: DocumentBlock[];
  future: DocumentBlock[][];
}

export type EditorAction =
  | { type: "commit"; next: DocumentBlock[] }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "jump"; index: number }
  | { type: "replace"; layout: DocumentLayout };

export function initEditorState(layout: DocumentLayout): EditorState {
  return {
    layout,
    past: [],
    present: layout.blocks,
    future: [],
  };
}

function clone(blocks: DocumentBlock[]): DocumentBlock[] {
  return blocks.map((b) => ({ ...b, style: { ...b.style }, bbox: b.bbox ? { ...b.bbox } : undefined }));
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "commit": {
      const past = [...state.past, state.present];
      if (past.length > HISTORY_LIMIT) past.shift();
      return { ...state, past, present: clone(action.next), future: [] };
    }
    case "undo": {
      if (state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1];
      const past = state.past.slice(0, -1);
      const future = [state.present, ...state.future];
      return { ...state, past, present: previous, future };
    }
    case "redo": {
      if (state.future.length === 0) return state;
      const [next, ...rest] = state.future;
      const past = [...state.past, state.present];
      if (past.length > HISTORY_LIMIT) past.shift();
      return { ...state, past, present: next, future: rest };
    }
    case "jump": {
      const all = [...state.past, state.present, ...state.future];
      const idx = Math.max(0, Math.min(all.length - 1, action.index));
      return {
        ...state,
        past: all.slice(0, idx),
        present: all[idx],
        future: all.slice(idx + 1),
      };
    }
    case "replace": {
      return initEditorState(action.layout);
    }
  }
}

export function timelineLength(state: EditorState): number {
  return state.past.length + 1 + state.future.length;
}

export function timelineIndex(state: EditorState): number {
  return state.past.length;
}
