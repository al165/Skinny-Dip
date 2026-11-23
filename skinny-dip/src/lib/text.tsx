// Text rendering helpers for cards and answers.
import { Fragment, type ReactNode } from "react";

import type { Item, QuestionSource } from "../api";

/** How a document treats a view, as the start of a reception line ("rejected by the author of “…”"). */
export const VOICE_LABEL: Record<string, string> = {
    argued: "argued in", conceded: "conceded in", reported: "reported in", endorsed: "endorsed by the author of",
    rejected: "rejected by the author of",
};

/** Passage text with the quote that led here highlighted (offsets from the quote check, else a plain search). */
export function withHighlight(text: string, h: Item["highlight"]): ReactNode {
    if (!h) return text;
    let start = h.start ?? -1, end = h.end ?? -1;
    if (start < 0 || end <= start || end > text.length) {
        start = text.indexOf(h.quote);
        end = start + h.quote.length;
    }
    if (start < 0) return text;
    return <>{text.slice(0, start)}<mark>{text.slice(start, end)}</mark>{text.slice(end)}</>;
}

const LABEL = /\[([A-Z]\d+)\]/g;

/** Answer text with each [A2]-style label as a chip that adds that source to the canvas. */
export function withCitations(answer: string, sources: QuestionSource[], add: (s: QuestionSource) => void): ReactNode {
    const byLabel = new Map(sources.map(s => [s.label, s]));
    const parts: ReactNode[] = [];
    let last = 0;
    for (const m of answer.matchAll(LABEL)) {
        const src = byLabel.get(m[1]);
        if (!src) continue;
        parts.push(<Fragment key={`t${last}`}>{answer.slice(last, m.index)}</Fragment>);
        parts.push(
            <button key={`c${m.index}`} className={`cite nodrag kind-${src.kind}`} onClick={() => add(src)}
                title={`${src.title}${src.holder ? ` · ${src.holder.name}` : ""}${src.document ? ` · ${src.document}` : ""}\nClick to add it to the canvas`}>
                {m[1]}
            </button>);
        last = m.index + m[0].length;
    }
    parts.push(<Fragment key="end">{answer.slice(last)}</Fragment>);
    return parts;
}
